"""One native compile, native evidence retrieval, one final reader. No labels."""
import argparse
import os
from pathlib import Path

from benchmarks.common.paths import safe_tag
from benchmarks.multihop import profile
from benchmarks.multihop import protocol as p
from memoket_kite.core.algebra import Store
from memoket_kite.pipeline import postproc
from memoket_kite.pipeline.answer import answer


def evaluate(dataset, tag, limit=0, run=None, plan_cache=None):
    data, memory, output, _ = p.paths(tag, dataset, run)
    if output.exists():
        raise FileExistsError("evaluation exists; do not repeat paid calls")
    bank = p.read(memory.with_suffix(".manifest.json"))
    if not bank.get("extraction_complete") or bank.get("failed_extractions"):
        raise RuntimeError("incomplete extraction bank; inspect and fix sources before evaluation")
    assert p.sha(memory) == bank["memory_sha256"]
    output.mkdir(parents=True)
    manifest = p.read(data / "manifest.json")
    assert p.sha(data / "questions.jsonl") == manifest["sha256"]["questions.jsonl"]
    questions = p.records(data / "questions.jsonl")
    if limit:
        questions = questions[:limit]
    assert all(set(q) == {"id", "question"} for q in questions)
    contract = {"profile": "benchmarks.multihop.profile", "source_hashes": p.source_hashes(),
                "memory_sha256": p.sha(memory), "questions_sha256": p.sha(data / "questions.jsonl"),
                "scope": manifest["scope"], "questions": len(questions), "compiler": "gpt-6-luna",
                "reasoning_effort": "medium", "reader": "gpt-4o-mini", "compile_candidates": 1,
                "evidence_budget": profile.DEFAULT_BUDGET,
                "extra_model_passes": int(getattr(profile, "COMPILE_MAX_ROUNDS", 1)) > 1,
                "extra_reader_passes": False,
                "compact_sources": profile.COMPACT_SOURCES,
                "answer_response_format": profile.ANSWER_RESPONSE_FORMAT,
                "compile_round_limit": int(getattr(profile, "COMPILE_MAX_ROUNDS", 1)),
                "initial_plan_cache": str(plan_cache) if plan_cache else None,
                "postprocessing": False, "vectors": False}
    p.dump(output / "manifest.json", contract)
    store, vocab = Store.load([str(memory)])
    os.environ["KITE_LLM_LOG"] = str(output / "calls.jsonl")
    os.environ["KITE_LLM_PHASE"] = "evaluate"
    for q in questions:
        os.environ["KITE_LLM_ITEM"] = q["id"]
        record = dict(q)
        try:
            result = answer(store, vocab, profile, q["question"], model="gpt-6-luna",
                            answer_model="gpt-4o-mini", budget=profile.DEFAULT_BUDGET,
                            plan_cache=str(plan_cache) if plan_cache else None,
                            postproc_policy=postproc.PostprocPolicy.resolve(""))
            record.update(status="ok", answer=result["answer"], result=result)
        except Exception as error:
            record.update(status="error", answer=None, error_type=type(error).__name__, error=str(error))
        p.append(output / "predictions.jsonl", record)
        print(dataset, q["id"], record["status"], flush=True)
    assert p.sha(memory) == contract["memory_sha256"]
    assert p.source_hashes() == contract["source_hashes"]
    calls = p.records(output / "calls.jsonl")
    for q in questions:
        requests = [r for r in calls if r["event"] == "request" and r["item"] == q["id"]]
        for model, maximum in [("gpt-6-luna", contract["compile_round_limit"]), ("gpt-4o-mini", 1)]:
            assert sum(r["request"]["model"] == model for r in requests) <= maximum
    p.dump(output / "freeze.json", {"complete": True, "predictions_sha256": p.sha(output / "predictions.jsonl"),
           "usage": p.usage(output / "calls.jsonl"), "memory_unchanged": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="full", type=safe_tag)
    parser.add_argument("--dataset", choices=p.DATASETS)
    parser.add_argument("--run", type=safe_tag, help="new immutable evaluation name; reuse the tag's unchanged memory")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--plan-cache", type=Path, help="exact native compiled-plan cache; feedback plans are never cached")
    parser.add_argument("--compile-rounds", type=int, choices=(1, 2, 3), help="maximum compiler rounds; one reader after retrieval")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be nonnegative")
    if args.compile_rounds is not None:
        profile.COMPILE_MAX_ROUNDS = args.compile_rounds
    p.configure(args.env_file)
    # Runtime guard: evaluation never opens the reference labels or raw QA files.
    import sys
    def audit(event, values):
        if event == "open" and isinstance(values[0], (str, bytes)):
            path = Path(os.fsdecode(values[0])).resolve()
            if path.name == "references.jsonl" or path.name in {ds + ".json" for ds in p.DATASETS}:
                raise RuntimeError("labels/raw QA forbidden during inference")
    sys.addaudithook(audit)
    for ds in [args.dataset] if args.dataset else p.DATASETS:
        evaluate(ds, args.tag, args.limit, args.run, args.plan_cache)


if __name__ == "__main__":
    main()
