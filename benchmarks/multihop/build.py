"""One native extraction call per passage; no consolidation or repair passes."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from benchmarks.common.paths import safe_tag
from benchmarks.multihop import adapter, profile
from benchmarks.multihop import protocol as p
from memoket_kite.core.vocab import Vocab
from memoket_kite.pipeline.extract import append_session_to_xml, extract_facts
from memoket_kite.providers.llm import _balanced_json_candidates


def same_extraction(previous, current):
    keys = ("source", "model", "reasoning_effort", "extraction_rounds")
    files = ("benchmarks/multihop/profile.py", "benchmarks/multihop/adapter.py",
             "src/memoket_kite/pipeline/extract.py", "src/memoket_kite/core/vocab.py",
             "src/memoket_kite/providers/llm.py")
    return (all(previous.get(k) == current[k] for k in keys)
            and all(previous.get("code", {}).get(k) == current["code"].get(k) for k in files))


def seed_prefix(dataset, tag, origin_tag):
    """Snapshot only a stopped origin; never race a live full-corpus builder."""
    _, _, _, old_cache = p.paths(origin_tag, dataset)
    with (old_cache / "campaign.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _seed_prefix(dataset, tag, origin_tag)


def _seed_prefix(dataset, tag, origin_tag):
    """Copy an immutable prefix's raw receipts; native prompt keys still gate replay."""
    data, _, _, cache = p.paths(tag, dataset)
    old_data, old_output, _, old_cache = p.paths(origin_tag, dataset)
    if origin_tag == tag:
        raise ValueError("reuse origin must be a different tag")
    previous = p.read(old_output.with_suffix(".manifest.json"))
    old_docs = p.records(old_data / "corpus.jsonl")
    documents = p.records(data / "corpus.jsonl")
    if not old_docs or documents[:len(old_docs)] != old_docs:
        raise RuntimeError("reuse requires the identical ordered source prefix")
    if (previous["source"] != p.sha(old_data / "corpus.jsonl")
            or previous["memory_sha256"] != p.sha(old_output)
            or previous["model"] != "gpt-6-luna"
            or previous["reasoning_effort"] != (getattr(profile, "EXTRACT_REASONING_EFFORT", None) or "medium")
            or previous["extraction_rounds"] != 1):
        raise RuntimeError("reuse origin is not a verified single-pass build")
    receipt = old_cache / "extract.jsonl"
    usage = p.usage(receipt)
    if usage != previous["usage"]:
        raise RuntimeError("reuse origin receipts changed")
    raw_files = sorted((old_cache / "raw").glob("*.json"))
    recovery_files = [path for kind in ("format-retries", "transport-retries", "interrupted-workers")
                      for path in sorted((old_cache / kind).glob("*.json"))]
    if not raw_files:
        raise RuntimeError("reuse origin has no raw extraction cache")
    # An interrupted build can provide only its accepted ordered prefix. Its
    # failed source remains attempted in receipts, so it cannot be silently
    # re-requested or counted as a reusable successful extraction.
    prefix_length = previous["documents"] - len(previous.get("failed_extractions", []))
    if prefix_length < 1:
        raise RuntimeError("reuse origin has no accepted prefix")
    lineage = {"origin_tag": origin_tag, "source_sha256": previous["source"],
               "memory_sha256": previous["memory_sha256"],
               "receipt_sha256": p.sha(receipt) if receipt.exists() else None,
               "raw_sha256": {path.name: p.sha(path) for path in raw_files},
               "recovery_metadata_sha256": {str(path.relative_to(old_cache)): p.sha(path)
                                            for path in recovery_files},
               "origin_extraction_complete": previous["extraction_complete"],
               "origin_code": previous["code"],
               "prefix_ids": [u["id"] for u in adapter.iter_units(old_docs[:prefix_length])],
               "reused_usage": usage}
    marker = cache / "reuse.json"
    if marker.exists():
        if p.read(marker) != lineage:
            raise RuntimeError("reuse lineage changed")
        if receipt.exists() and (not (cache / "extract.jsonl").exists()
                               or not (cache / "extract.jsonl").read_bytes().startswith(receipt.read_bytes())):
            raise RuntimeError("reused request receipts changed")
        for name, digest in lineage["raw_sha256"].items():
            if not (cache / "raw" / name).exists() or p.sha(cache / "raw" / name) != digest:
                raise RuntimeError("reused raw extraction cache changed")
        for name, digest in lineage["recovery_metadata_sha256"].items():
            if not (cache / name).exists() or p.sha(cache / name) != digest:
                raise RuntimeError("reused recovery lineage changed")
    else:
        if (cache / "extract.jsonl").exists() or (cache / "raw").exists():
            raise RuntimeError("cannot seed an already populated build cache")
        shutil.copytree(old_cache / "raw", cache / "raw")
        if receipt.exists():
            shutil.copy2(receipt, cache / "extract.jsonl")
        for path in recovery_files:
            destination = cache / path.relative_to(old_cache)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        p.dump(marker, lineage)
    return lineage


def _terminated_worker_proof(previous, cache, request):
    """Verify an explicit zero-network checkpoint of a now-absent worker."""
    source = request.get("item")
    path = cache / "interrupted-workers" / (source + ".json")
    if not path.exists():
        return None
    proof = p.read(path)
    pid = proof.get("origin_pid")
    if type(pid) is not int or pid <= 0:
        return None
    if not (proof.get("process_absent") is True and proof.get("new_API_calls") == 0
            and proof.get("exact_native_cache_replay") is True
            and proof.get("source_id") == source
            and proof.get("lost_call_id") == request.get("call_id")
            and proof.get("receipt_sha256") == p.sha(cache / "extract.jsonl")
            and proof.get("manifest_memory_sha256") == previous.get("memory_sha256")
            and proof.get("extraction_source_sha256") == previous.get("source")
            and proof.get("code") == previous.get("code")
            and proof.get("accepted_prefix") == previous.get("documents", 0) - 1):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    except OSError:
        return None
    else:
        return None
    return {"terminated_worker_proof": str(path.relative_to(cache)),
            "terminated_worker_proof_sha256": p.sha(path), "origin_pid": pid,
            "recovery_reason": "verified_terminated_worker"}


def _transport_recovery(previous, cache):
    """One explicit resume after a logged terminal transport failure.

    A complete model response, invalid extraction, unverified in-flight call,
    cached output, or previously retried source is never eligible. The lost
    response's usage stays unknown in the immutable receipts.
    """
    failures = previous.get("failed_extractions", [])
    if len(failures) != 1 or failures[0].get("error_type") not in {"ProviderError", "RuntimeError"}:
        return None
    source = failures[0]["id"]
    # Limit recovery per source, not per entire corpus. Preserve the legacy
    # marker as well so an old interrupted source cannot be retried twice.
    marker = cache / "transport-retry.json"
    if ((cache / "transport-retries" / (source + ".json")).exists()
            or (marker.exists() and p.read(marker).get("source_id") == source)):
        return None
    if list((cache / "raw").glob(source + ".*.json")):
        return None
    records = [r for r in p.records(cache / "extract.jsonl") if r.get("item") == source]
    requests = [r for r in records if r["event"] == "request"]
    if len(requests) != 1:
        return None
    call_id = requests[0]["call_id"]
    if any(r["event"] == "response" for r in records):
        return None
    transport_errors = {"URLError", "ConnectionResetError", "ConnectionAbortedError",
                        "TimeoutError", "RemoteDisconnected"}
    errors = [r for r in records if r["event"] == "error" and r["call_id"] == call_id]
    worker = None
    if (failures[0].get("error_type") == "RuntimeError" and not errors
            and failures[0].get("error") ==
            "exact extraction cache missing or invalid; cache-only mode forbids API calls"):
        worker = _terminated_worker_proof(previous, cache, requests[0])
    if failures[0].get("error_type") != "ProviderError" and not worker:
        return None
    # Older receipts omit HTTP status, but their matching native failure keeps
    # the exact terminal HTTPError text. Never retry an unknown status or 4xx.
    http_status = None
    if any(r.get("error_type") == "HTTPError" for r in errors):
        match = re.fullmatch(r"llm failed: HTTP Error (\d{3}):[^\n]*",
                             failures[0].get("error", ""))
        if match:
            http_status = int(match.group(1))
    if not (any(r.get("error_type") in transport_errors for r in errors)
            or (http_status is not None and 500 <= http_status <= 599) or worker):
        return None
    recovery = {"source_id": source, "lost_call_id": call_id,
                "lost_response_usage_unknown": True, "explicit_resume": True,
                "max_additional_transport_attempts": 1}
    request = requests[0].get("request", {})
    messages = request.get("messages", [])
    if len(messages) == 1 and isinstance(messages[0].get("content"), str):
        recovery["prompt_sha256"] = hashlib.sha256(messages[0]["content"].encode()).hexdigest()
    if http_status is not None:
        recovery["terminal_http_status"] = http_status
    if worker:
        if len(messages) != 1 or not isinstance(messages[0].get("content"), str):
            return None
        recovery.update(worker)
    return recovery


def _format_recovery(previous, cache):
    """One explicit JSON-mode attempt for an unparseable received response.

    Never retries valid JSON with bad fields, low-quality facts, refusals,
    valid output or unknown transport state. A truncated invalid JSON response
    may receive the same bounded format-only attempt, without a larger budget.
    No received text is repaired
    or passed back to the model. Receipts enforce the bound across copied runs.
    """
    failures = previous.get("failed_extractions", [])
    if len(failures) != 1:
        return None
    source = failures[0]["id"]
    if (cache / "format-retries" / (source + ".json")).exists():
        return None
    for path in (cache / "raw").glob(source + ".*.json"):
        cached = p.read(path)
        # llm_json may salvage a balanced inner fact from a broken envelope.
        # A fragment is not an accepted extraction. Complete envelopes with
        # invalid fields remain ineligible for a quality repair.
        if isinstance(cached, dict) and any(key in cached for key in ("facts", "proposals", "entity_types")):
            return None
    records = [r for r in p.records(cache / "extract.jsonl") if r.get("item") == source]
    requests = [r for r in records if r["event"] == "request"]
    responses = [r for r in records if r["event"] == "response"]
    if len(requests) != 1 or len(responses) != 1 or requests[0]["call_id"] != responses[0]["call_id"]:
        return None
    request = requests[0]["request"]
    messages = request.get("messages", [])
    if (request.get("response_format") is not None or request.get("model") != "gpt-6-luna"
            or request.get("reasoning_effort") != "none" or request.get("temperature") != 0
            or len(messages) != 1 or messages[0].get("role") != "user"
            or not isinstance(messages[0].get("content"), str)):
        return None
    choices = responses[0]["response"].get("choices", [])
    if len(choices) != 1 or choices[0].get("finish_reason") not in {"stop", "length"}:
        return None
    message = choices[0].get("message", {})
    content = message.get("content")
    if message.get("refusal") or not isinstance(content, str) or not content.strip().startswith("{"):
        return None
    try:
        json.loads(content)
    except json.JSONDecodeError:
        pass
    else:
        return None
    for candidate in _balanced_json_candidates(content):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and any(key in parsed for key in ("facts", "proposals", "entity_types")):
                return None
        except json.JSONDecodeError:
            pass
    return {"source_id": source, "failed_call_id": requests[0]["call_id"],
            "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
            "prompt_sha256": hashlib.sha256(messages[0]["content"].encode()).hexdigest(),
            "response_format": {"type": "json_object"}, "max_additional_format_attempts": 1,
            "failed_finish_reason": choices[0]["finish_reason"],
            "same_prompt_model_reasoning_temperature": True,
            "failed_response_preserved_not_model_input": True}


def _restore_checkpoint(dataset, candidate, units, contract, cache):
    """Load a verified native snapshot rather than re-rendering its history."""
    if candidate is None:
        return None
    path, previous = candidate
    if (previous.get("failed_extractions") or not (
            previous.get("control_stop") or previous.get("extraction_complete"))
            or not same_extraction(dict(previous, source=contract["source"]), contract)):
        return None
    if p.sha(path) != previous["memory_sha256"]:
        raise RuntimeError("native checkpoint memory changed")
    root = ET.parse(path).getroot()
    timeline = root.find("timeline")
    count = previous["documents"]
    if (root.tag != "codebook" or root.get("id") != dataset or timeline is None
            or not 0 < count <= len(units)
            or [node.get("id") for node in timeline] != [u["id"] for u in units[:count]]):
        raise RuntimeError("native checkpoint is not the verified ordered source prefix")
    # The code/source/model contract seals the snapshot's history. Check the
    # first original prompt too, so template/provider/environment drift cannot
    # bypass the native cache identity when resuming a legacy snapshot.
    guard = Vocab(admission_k=4, promote_units=3)
    for topic in profile.SEED_ROOTS:
        guard.define_root(topic)
    options = {}
    marker = cache / "format-retries" / (units[0]["id"] + ".json")
    if marker.exists():
        value = p.read(marker)
        options = {"response_format": value["response_format"],
                   "expected_prompt_sha256": value["prompt_sha256"]}
    extract_facts(guard, profile, units[0], model="gpt-6-luna", cache_dir=str(cache / "raw"),
                  cache_only=True, **options)
    vocab_node = root.find("vocab")
    if vocab_node is None:
        raise RuntimeError("native checkpoint has no vocabulary")
    vocab = Vocab.from_xml(vocab_node, strict=True, admission_k=4, promote_units=3)
    root.remove(vocab_node)
    facts = root.findall("timeline/session/fact")
    if (len(facts) != previous["facts"] or sum(not f.get("topics") for f in facts)
            != previous["empty_topic_facts"]):
        raise RuntimeError("native checkpoint counters differ from its manifest")
    return vocab, root, timeline, count, {"sources": count,
        "memory_sha256": previous["memory_sha256"], "first_prompt_exact_cache_guard": True,
        "native_vocab_restored": True, "completed_sources_not_reextracted": True}


def build(dataset, tag, resume=False, reuse_from=None, *, stop_requested=None, progress=None,
          recover_format=False):
    data, output, _, cache = p.paths(tag, dataset)
    manifest = p.read(data / "manifest.json")
    assert p.sha(data / "corpus.jsonl") == manifest["sha256"]["corpus.jsonl"]
    contract = {"source": p.sha(data / "corpus.jsonl"), "code": p.source_hashes(),
                "model": "gpt-6-luna",
                "reasoning_effort": getattr(profile, "EXTRACT_REASONING_EFFORT", None) or "medium",
                "extraction_rounds": 1}
    recovery = None
    format_recovery = None
    checkpoint_candidate = None
    if output.exists():
        previous = p.read(output.with_suffix(".manifest.json"))
        if not (resume and same_extraction(previous, contract) and p.sha(output) == previous["memory_sha256"]):
            raise RuntimeError("existing Codebook differs or resume was not requested; do not silently reuse memory")
        checkpoint_candidate = (output, previous)
        recovery = _transport_recovery(previous, cache) if resume else None
        format_recovery = _format_recovery(previous, cache) if resume and recover_format else None
        if (not (previous.get("control_stop") and not previous.get("failed_extractions"))
                and not recovery and not format_recovery):
            return {"dataset": dataset, "status": "already built", "extraction_complete": previous["extraction_complete"]}
    cache.mkdir(parents=True, exist_ok=True)
    if (cache / "build.json").exists():
        previous = p.read(cache / "build.json")
        # A builder error-handling fix may resume cached native extractions;
        # prompt, source, model and all extraction-kernel code must still match.
        if not resume or not same_extraction(previous, contract):
            raise RuntimeError("build already started or contract changed; no blind repeat")
    else:
        p.dump(cache / "build.json", contract)
    reuse = seed_prefix(dataset, tag, reuse_from) if reuse_from else None
    if not reuse and (cache / "reuse.json").exists():
        raise RuntimeError("resume must explicitly preserve --reuse-from lineage")
    prefix_ids = set(reuse["prefix_ids"]) if reuse else set()
    if reuse and not output.exists():
        _, origin_memory, _, _ = p.paths(reuse_from, dataset)
        origin = p.read(origin_memory.with_suffix(".manifest.json"))
        checkpoint_candidate = (origin_memory, origin)
        # Appending sources legitimately changes the corpus digest. Recovery
        # still requires the same extraction kernel and per-call settings;
        # seed_prefix independently verifies the entire ordered old corpus.
        if origin.get("failed_extractions") and not same_extraction(
                dict(origin, source=contract["source"]), contract):
            raise RuntimeError("recovery origin extraction kernel or configuration differs")
        recovery = _transport_recovery(origin, cache)
        format_recovery = _format_recovery(origin, cache) if recover_format else None
    os.environ["KITE_LLM_LOG"] = str(cache / "extract.jsonl")
    os.environ["KITE_LLM_PHASE"] = "extract"
    raw_cache = cache / "raw"
    failures = []
    attempted = {r.get("item") for r in p.records(cache / "extract.jsonl") if r["event"] == "request"}
    units = list(adapter.iter_units(p.records(data / "corpus.jsonl")))
    restored = _restore_checkpoint(dataset, checkpoint_candidate, units, contract, cache)
    loaded_sources = 0
    checkpoint_resume = None
    if restored:
        vocab, root, timeline, loaded_sources, checkpoint_resume = restored
    else:
        vocab = Vocab(admission_k=4, promote_units=3)
        for topic in profile.SEED_ROOTS:
            vocab.define_root(topic)
        root = ET.Element("codebook", id=dataset)
        timeline = ET.SubElement(root, "timeline")
    control_stop = False
    fact_count = len(timeline.findall("session/fact"))
    empty_topic_facts = sum(not fact.get("topics") for fact in timeline.findall("session/fact"))
    for ordinal, unit in enumerate(units, 1):
        if stop_requested and stop_requested():
            control_stop = True
            break
        if ordinal <= loaded_sources:
            if progress and ordinal == loaded_sources:
                progress({"processed_sources": ordinal, "total_sources": len(units),
                          "successful_extractions": loaded_sources, "facts": fact_count,
                          "source_id": unit["id"], "empty_topic_facts": empty_topic_facts,
                          "cache_replay": True, "native_checkpoint_resume": True, "failed": False})
            continue
        os.environ["KITE_LLM_ITEM"] = unit["id"]
        retry_transport = bool(recovery and recovery["source_id"] == unit["id"])
        if retry_transport:
            p.dump(cache / "transport-retry.json", recovery)
            p.dump(cache / "transport-retries" / (unit["id"] + ".json"), recovery)
        retry_format = bool(format_recovery and format_recovery["source_id"] == unit["id"])
        format_marker = cache / "format-retries" / (unit["id"] + ".json")
        if retry_format:
            p.dump(format_marker, format_recovery)
        options = {}
        if retry_transport and recovery.get("prompt_sha256"):
            options["expected_prompt_sha256"] = recovery["prompt_sha256"]
        if format_marker.exists():
            marker = p.read(format_marker)
            options.update(response_format=marker["response_format"],
                           expected_prompt_sha256=marker["prompt_sha256"])
        # Completed native cache entries replay the same one-pass responses.
        try:
            facts = extract_facts(vocab, profile, unit, model="gpt-6-luna", cache_dir=str(raw_cache),
                                  cache_only=(unit["id"] in attempted or unit["id"] in prefix_ids)
                                  and not retry_transport and not retry_format, **options)
        except Exception as error:
            if unit["id"] in prefix_ids:
                raise RuntimeError("reused prefix did not match exact native prompt cache; stopping before new API calls") from error
            facts = []
            failures.append({"id": unit["id"], "error_type": type(error).__name__, "error": str(error)})
        append_session_to_xml(timeline, profile, unit, facts)
        vocab.promotion_pass()
        fact_count += len(facts)
        empty_topic_facts += sum(not fact["topics"] for fact in facts)
        if progress:
            progress({"processed_sources": ordinal, "total_sources": len(units),
                      "successful_extractions": ordinal - len(failures),
                      "facts": fact_count, "source_id": unit["id"],
                      "empty_topic_facts": empty_topic_facts,
                      "cache_replay": (unit["id"] in attempted or unit["id"] in prefix_ids)
                                      and not retry_transport and not retry_format,
                      "failed": bool(failures)})
        print(f"{dataset} {unit['id']}: {len(facts)} facts", flush=True)
        if failures:
            break  # Preserve this source and its raw receipt; no later paid calls.
    problems = vocab.check()
    if problems:
        raise RuntimeError(problems)
    if p.source_hashes() != contract["code"]:
        raise RuntimeError("source code changed during extraction; refuse to freeze mixed build")
    root.insert(0, vocab.to_xml())
    ET.indent(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".xml.tmp")
    ET.ElementTree(root).write(temporary, encoding="unicode", xml_declaration=True)
    ET.parse(temporary)
    temporary.replace(output)
    usage = p.usage(cache / "extract.jsonl")
    p.dump(output.with_suffix(".manifest.json"), {**contract, "memory_sha256": p.sha(output),
           "scope": manifest["scope"], "usage": usage, "reuse": reuse,
           "new_HTTP_calls": usage["HTTP_calls"] - (reuse["reused_usage"]["HTTP_calls"] if reuse else 0),
           "documents": len(root.findall("timeline/session")),
           "facts": len(root.findall("timeline/session/fact")),
           "empty_topic_facts": empty_topic_facts,
           "topic_policy": "strict_validation" if profile.EXTRACT_STRICT_TOPICS else "original_native_resolution",
           "extraction_complete": not failures and len(root.findall("timeline/session")) == len(units),
           "failed_extractions": failures, "control_stop": control_stop,
           "transport_recovery": p.read(cache / "transport-retry.json")
           if (cache / "transport-retry.json").exists() else None,
           "transport_recoveries": [p.read(path) for path in
                                    sorted((cache / "transport-retries").glob("*.json"))],
           "format_recoveries": [p.read(path) for path in
                                 sorted((cache / "format-retries").glob("*.json"))],
           "format_recovery_enabled": recover_format,
           "native_checkpoint_resume": checkpoint_resume,
           "unprocessed_sources": len(units) - len(root.findall("timeline/session")),
           "post_extraction_model_passes": 0})
    complete = not failures and len(root.findall("timeline/session")) == len(units)
    return {"dataset": dataset, "status": "built" if complete else ("stopped" if control_stop else "incomplete"),
            "extraction_complete": complete, "memory": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="full", type=safe_tag)
    parser.add_argument("--dataset", choices=p.DATASETS)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--recover-format", action="store_true",
                        help="one JSON-mode attempt after a received unparseable response; no semantic retries")
    parser.add_argument("--reuse-from", type=safe_tag,
                        help="reuse only a verified ordered source prefix, guarded by exact native prompt keys")
    args = parser.parse_args()
    p.configure(args.env_file)
    stop = threading.Event()
    def request_stop(signum, frame):
        stop.set()  # Drain the current HTTP response; stop at the next source boundary.
    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for ds in [args.dataset] if args.dataset else p.DATASETS:
            result = build(ds, args.tag, args.resume, args.reuse_from,
                           stop_requested=stop.is_set, recover_format=args.recover_format)
            print(result, flush=True)
            if not result["extraction_complete"] and result["status"] != "stopped":
                raise SystemExit(1)
            if stop.is_set():
                break
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
