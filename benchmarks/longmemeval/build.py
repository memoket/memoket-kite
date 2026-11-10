"""Build cleaned LongMemEval haystacks into finalized KITE Codebooks."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import tempfile
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmarks.common.build_provenance import build_attributes, reuse_existing
from benchmarks.common.paths import CACHE_ROOT, CODEBOOKS_ROOT, DATASETS_ROOT
from benchmarks.longmemeval import adapter, profile
from memoket_kite.core.vocab import Vocab, norm_code
from memoket_kite.pipeline.compile_plan import _provider_cache_identity
from memoket_kite.pipeline.extract import (
    append_session_to_xml,
    consolidate_entities,
    consolidate_topics,
    extract_facts,
    finalize_instance_alignment,
)
from memoket_kite.providers.llm import llm_json

DATASET = DATASETS_ROOT / "longmemeval" / "longmemeval_s_cleaned.json"
OUTPUT_DIR = CODEBOOKS_ROOT / "longmemeval"


def _model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.+-]+", "-", model.lower()).strip("-")


def select_indices(data: list[dict], count: int) -> list[int]:
    """Select a deterministic, question-type-stratified subset.

    The strata decide the order: each question type contributes up to
    ``count // len(groups)`` indices, of which a reserved slice goes to its
    abstention questions. That integer division leaves a remainder, so the
    shortfall is filled deterministically in index order and the caller always
    receives exactly the size it asked for. ``--n 0`` is the explicit "all
    questions" sentinel; a request at or above the corpus size has the same
    effect.
    """
    if count < 0:
        raise ValueError(f"question count must not be negative: {count}")
    if not count or count >= len(data):
        return list(range(len(data)))
    groups = collections.defaultdict(list)
    for index, item in enumerate(data):
        groups[item["question_type"]].append(index)
    per_type = max(1, count // len(groups))
    selected = []
    for question_type in sorted(groups):
        indices = groups[question_type]
        abstention = [index for index in indices if "_abs" in data[index]["question_id"]]
        answerable = [index for index in indices if "_abs" not in data[index]["question_id"]]
        selected.extend((abstention[: max(1, per_type // 5)] + answerable)[:per_type])
    if len(selected) < count:  # strata leave a remainder; fill it deterministically
        chosen = set(selected)
        selected.extend(index for index in range(len(data)) if index not in chosen)
    return sorted(selected[:count])


def _load_cached_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cached_json(path: Path, payload: dict) -> None:
    """Atomically publish a complete refinement response for concurrent readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".refine-",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def refine_topic_assignments(
    root: ET.Element,
    *,
    model: str,
    cache_dir: Path,
) -> int:
    """Refine broad Fact topics once the final Codebook taxonomy is known."""
    topics = {
        topic.get("code", ""): (topic.get("parents") or "").split()
        for topic in root.iter("topic")
        if topic.get("code")
    }
    roots = {code for code, parents in topics.items() if not parents}
    taxonomy = "\n".join(
        f"  {code} <- {' '.join(parents) if parents else '(ROOT)'}"
        for code, parents in sorted(topics.items())
    )
    changed = 0
    proposed: dict[str, str] = {}
    for session in root.iter():
        if session.tag not in ("conv", "session"):
            continue
        facts = list(session.iter("fact"))
        if not facts:
            continue
        # Same identity rule as the extraction cache: the key is the fully
        # rendered prompt — taxonomy and fact rows included — plus model and
        # provider. A template-only hash would let refreshed facts hit a
        # refinement response computed for the facts they replaced.
        rendered = profile.TOPIC_REFINEMENT_PROMPT.format(
            tree=taxonomy,
            facts="\n".join(
                f"{fact.get('id')}|{fact.get('topics') or '(none)'}|{(fact.text or '')[:200]}"
                for fact in facts
            ),
        )
        fingerprint = hashlib.sha256(
            "\x1f".join((model, _provider_cache_identity(), rendered)).encode("utf-8")
        ).hexdigest()[:12]
        cache_path = cache_dir / f"{session.get('id', 'session')}.{fingerprint}.json"
        response = _load_cached_json(cache_path)
        if response is None:
            response = llm_json(rendered, model=model)
            _write_cached_json(cache_path, response)

        if not isinstance(response, dict):
            response = {}  # llm_json may yield a bare array; degrade gracefully
        for item in response.get("new_codes") or []:
            if not isinstance(item, dict):
                continue
            code = norm_code(item.get("code") or "")
            parent = norm_code(item.get("parent") or "")
            if code and parent in topics and code != parent and code not in topics:
                topics[code] = [parent]
                proposed[code] = parent
        facts_by_id = {fact.get("id"): fact for fact in facts}
        assignments = response.get("assignments") or response.get("labels") or []
        for assignment in assignments:
            if not isinstance(assignment, dict):
                continue
            fact = facts_by_id.get(str(assignment.get("id") or ""))
            if fact is None:
                continue
            assigned_topics = assignment.get("topics")
            if not isinstance(assigned_topics, list):
                continue
            codes = [
                normalized for code in assigned_topics if (normalized := norm_code(code)) in topics
            ][:2]
            if not codes or set(codes) <= roots:
                continue
            expanded = list(codes)
            for code in codes:
                ancestor, visited = code, set()
                while ancestor in topics and topics[ancestor] and ancestor not in visited:
                    visited.add(ancestor)
                    ancestor = topics[ancestor][0]
                if ancestor and ancestor not in expanded:
                    expanded.append(ancestor)
            value = " ".join(expanded)
            if value != (fact.get("topics") or ""):
                fact.set("topics", value)
                changed += 1

    vocabulary = root.find("vocab")
    if vocabulary is not None:
        for code, parent in proposed.items():
            ET.SubElement(
                vocabulary,
                "topic",
                code=code,
                status="candidate",
                parents=parent,
                born="refinement",
            )
        # A new build must satisfy the taxonomy contract in full, so the
        # vocabulary is re-parsed under `strict=True`. The public loader's
        # default is deliberately more permissive, because it also has to read
        # Codebooks that were published under an earlier contract.
        refined_vocab = Vocab.from_xml(vocabulary, strict=True)
        problems = refined_vocab.check()
        if problems:
            raise RuntimeError(f"refined vocabulary invariants failed: {problems}")
        known_topics = set(refined_vocab.topics)
        dangling = sorted(
            {
                code
                for fact in root.iter("fact")
                for code in (fact.get("topics") or "").split()
                if code not in known_topics
            }
        )
        if dangling:
            raise RuntimeError(f"refinement assigned unknown topic codes: {dangling[:5]}")
    return changed


def build_codebook(
    instance: dict,
    *,
    model: str,
    resume: bool = False,
    force: bool = False,
    log=print,
) -> str:
    """Build and atomically publish one fully finalized Codebook."""
    question_id = instance["question_id"]
    output = OUTPUT_DIR / f"{question_id}.xml"
    units = list(adapter.iter_units(instance))
    attributes = build_attributes(
        model,
        {"id": question_id, "units": units},
        builder=__file__,
        adapter=adapter.__file__,
        profile=profile.__file__,
    )
    if reuse_existing(output, attributes, resume=resume, force=force):
        return f"{question_id}: skipped"

    vocabulary = Vocab(admission_k=4, promote_units=3)
    for topic in profile.SEED_ROOTS:
        vocabulary.define_root(topic)
    root = ET.Element("codebook", id=question_id, speakers="user assistant", **attributes)
    timeline = ET.SubElement(root, "timeline")
    extraction_cache = CACHE_ROOT / "extraction" / "longmemeval" / _model_slug(model)
    for session in units:
        facts = extract_facts(
            vocabulary,
            profile,
            session,
            model=model,
            cache_dir=str(extraction_cache),
            log=lambda *_: None,
        )
        append_session_to_xml(timeline, profile, session, facts)
        vocabulary.promotion_pass()
        if vocabulary.consolidation_pressure(pool_max=60):
            consolidate_topics(vocabulary, [root], model=model, log=lambda *_: None)
    consolidate_topics(vocabulary, [root], model=model, log=lambda *_: None)
    consolidate_entities(vocabulary, [root], model=model, log=lambda *_: None)
    vocabulary.promotion_pass()
    problems = vocabulary.check()
    if problems:
        raise RuntimeError(f"vocabulary invariants failed: {problems}")
    root.insert(0, vocabulary.to_xml())
    ET.indent(root)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{question_id}.", suffix=".xml", dir=OUTPUT_DIR
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        ET.ElementTree(root).write(temporary, encoding="unicode", xml_declaration=True)
        if profile.INSTANCE_ALIGNMENT:
            instances = finalize_instance_alignment(str(temporary), model=model, log=log)
            log(f"{question_id}: aligned {len(instances)} instances")
        if profile.TOPIC_REFINEMENT:
            tree = ET.parse(temporary)
            changed = refine_topic_assignments(
                tree.getroot(),
                model=model,
                cache_dir=CACHE_ROOT / "topic-refinement" / _model_slug(model) / question_id,
            )
            ET.indent(tree.getroot())
            tree.write(temporary, encoding="unicode", xml_declaration=True)
            log(f"{question_id}: refined {changed} topic assignments")
        # ElementTree writes XML-forbidden control characters without raising.
        # Validate the final bytes after every provider-driven finalization
        # stage and before replacing the last known-good Codebook.
        ET.parse(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return f"{question_id}: built"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be a positive integer")
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error("--shard must be in [0, --shards)")
    with DATASET.open(encoding="utf-8") as stream:
        data = json.load(stream)
    indices = select_indices(data, args.n)[args.shard :: args.shards]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"dataset={DATASET} model={args.model} workers={args.workers} "
        f"codebooks={len(indices)} output={OUTPUT_DIR}",
        flush=True,
    )
    lock = threading.Lock()
    failures = []

    def run(index: int) -> None:
        question_id = data[index]["question_id"]
        try:
            result = build_codebook(
                data[index], model=args.model, resume=args.resume, force=args.force
            )
            print(result, flush=True)
        except Exception as exc:
            with lock:
                failures.append(f"{question_id}: {exc}")

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        list(executor.map(run, indices))
    # Scope the marker to the shard, and only clear the one this process owns:
    # were parallel shards to share a single file, a shard that finished
    # cleanly would delete another shard's failure record.
    failure_path = OUTPUT_DIR / (
        "failed.txt" if getattr(args, "shards", 1) == 1 else f"failed.shard{args.shard}.txt"
    )
    if failures:
        failure_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
        print(f"{len(failures)} Codebooks failed; see {failure_path}")
        return 1
    failure_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
