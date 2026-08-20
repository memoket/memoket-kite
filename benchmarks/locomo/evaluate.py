"""Run KITE answering on LoCoMo categories 1-4."""

from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from benchmarks.common import manifest, ownership, singlewriter
from benchmarks.common.paths import CODEBOOKS_ROOT, DATASETS_ROOT, RESULTS_ROOT, safe_tag
from benchmarks.common.resume import completed_rows
from benchmarks.locomo import profile
from benchmarks.locomo.protocol import CATEGORIES
from memoket_kite.core.algebra import Store
from memoket_kite.errors import ConfigurationError
from memoket_kite.pipeline import postproc
from memoket_kite.pipeline.answer import answer
from memoket_kite.pipeline.retrieve import RetrievalOptions

DATASET = DATASETS_ROOT / "locomo" / "locomo10.json"


def _human_date(value: str) -> str:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        return value


def _render_memories(rows: list[dict], store: Store) -> str:
    """A human-readable copy of the selected rows, stored beside each answer.

    This runs AFTER the answer and never reaches the reader, so it must not
    carry instructions: what the reader sees is built by the pipeline's own
    renderer under this binding's DUAL_DATE and HYDRATE settings.
    """
    rendered = [
        "The following memories are presented in chronological order (oldest to newest).",
        "",
    ]
    for row in rows:
        if row.get("type") == "count":
            rendered.append(f"(aggregate) {row.get('text', 'count')}")
            continue
        text = row["text"]
        if row.get("type") == "fact" and row.get("src"):
            quotes = [
                store.lines[source_id].text
                for source_id in row["src"].split()
                if source_id in store.lines
            ][:2]
            if quotes:
                text += " | verbatim: " + " / ".join(quote[:120] for quote in quotes)
        rendered.append(f"({_human_date(row.get('date') or '')}) {text}")
    return "\n".join(rendered)


def _result_rows(result: dict, store: Store) -> list[dict]:
    rows = []
    for packed in result.get("pack_rows", []):
        identifier, row_type = packed.get("id", ""), packed.get("type")
        if row_type == "fact" and identifier in store.facts:
            fact = store.facts[identifier]
            rows.append(
                {
                    "type": "fact",
                    "date": fact.when,
                    "unit": fact.unit,
                    "src": " ".join(fact.src),
                    "text": fact.text,
                }
            )
        elif row_type == "line" and identifier in store.lines:
            line = store.lines[identifier]
            rows.append({"type": "line", "date": line.unit_date, "src": line.id, "text": line.text})
        elif row_type == "count":
            rows.append({"type": "count", "text": packed.get("text", "count")})
    return rows


def _load_dataset() -> list[dict]:
    with DATASET.open(encoding="utf-8") as stream:
        return json.load(stream)


def evaluate_sample(
    sample: dict,
    *,
    model: str,
    answer_model: str,
    workers: int,
    output: Path,
    resume: bool,
    plan_cache: Path | None,
    policy: postproc.PostprocPolicy,
) -> list[str]:
    """Evaluate one LoCoMo sample and return failed question identifiers."""
    if output.exists() and not resume:
        raise RuntimeError(f"output exists; pass --resume or choose another tag: {output}")
    completed = {row["qa_idx"] for row in completed_rows(output)}

    sample_id = sample["sample_id"]
    codebook = CODEBOOKS_ROOT / "locomo" / f"{sample_id}.xml"
    if not codebook.exists():
        raise RuntimeError(f"missing Codebook: {codebook}")
    store, vocabulary = Store.load([str(codebook)])
    pending = [
        (index, item)
        for index, item in enumerate(sample["qa"])
        if item.get("category") in CATEGORIES and index not in completed
    ]
    print(f"{sample_id}: {len(pending)} questions", flush=True)
    lock = threading.Lock()
    failures: list[str] = []

    def run(item: tuple[int, dict]) -> None:
        index, question = item
        try:
            result = answer(
                store,
                vocabulary,
                profile,
                question["question"],
                model=model,
                answer_model=answer_model,
                plan_cache=str(plan_cache) if plan_cache else None,
                postproc_policy=policy,
            )
            record = {
                "qa_idx": index,
                "category": question["category"],
                "question": question["question"],
                "gold": str(question.get("answer", "")),
                "gold_evidence": question.get("evidence", []),
                "answer": result["answer"],
                "memories_text": _render_memories(_result_rows(result, store), store),
                "pack_src": result.get("pack_src", []),
                "telemetry": result.get("telemetry"),
            }
            # The answerability verdict and any post-processing are the answer
            # stage's; it is the only place either is decided, so the harness
            # copies the record it left rather than asking a second time.
            for key in ("answerable_by_construction", "answer_pre_postproc", "postproc"):
                if key in result:
                    record[key] = result[key]
        except Exception as exc:
            with lock:
                failures.append(f"{sample_id}:{index}: {exc}")
            return
        with lock:
            with output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(run, pending))
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, nargs="+", default=list(range(10)))
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--answer-model", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tag", default="v0.1.0")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan-cache", type=Path)
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be a positive integer")
    try:
        args.tag = safe_tag(args.tag)
    except ValueError as error:
        parser.error(str(error))
    # Resolve the rule set first: a typo must cost nothing, not surface after
    # every question has already been answered.
    policy = postproc.PostprocPolicy.resolve(
        getattr(profile, "POSTPROC_RULES", ""), os.environ.get("KITE_POSTPROC")
    )
    # The pipeline refuses an estimated tokenizer with a library error; a CLI
    # surfaces that as a clean exit before any question is paid for.
    try:
        RetrievalOptions.from_profile(profile)
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error
    data = _load_dataset()
    if len(args.samples) != len(set(args.samples)):
        parser.error("--samples must not contain duplicates")
    if any(index < 0 or index >= len(data) for index in args.samples):
        parser.error(f"--samples must be between 0 and {len(data) - 1}")
    result_dir = RESULTS_ROOT / f"locomo-{args.tag}"
    result_dir.mkdir(parents=True, exist_ok=True)
    # The same per-tag lock the scorer takes. Two evaluators on one tag pay
    # twice and append twice; an evaluator running under a scorer moves the
    # very files the scorer is about to seal.
    with singlewriter.held(result_dir, purpose="evaluating"):
        # Checked INSIDE the lock. Outside it there is a real interleaving:
        # the evaluator sees no seal, the scorer takes the lock and seals,
        # the evaluator then takes the lock and appends to a sealed tag.
        if (result_dir / "judge_manifest.json").exists():
            raise SystemExit(
                f"{result_dir.name} has already been judged and sealed; "
                f"evaluating into it would invalidate the seal. Use a new --tag."
            )
        # A fresh run owns the whole directory. Checking only the samples it is
        # about to write would let `--samples 0` and a later `--samples 1` share
        # one tag: both sets then get scored together while the manifest
        # describes only the second. Any existing result or provenance file
        # means this tag is taken.
        started = sorted(result_dir.glob("results_*.jsonl"))
        if not args.resume and (started or (result_dir / "manifest.json").exists()):
            raise SystemExit(
                "output exists; pass --resume or choose another tag: "
                f"{started[0] if started else result_dir / 'manifest.json'}"
            )
        if args.resume and started:
            # Resuming inherits the manifest's claim; make it true before adding to it.
            ownership.check(result_dir, [data[index]["sample_id"] for index in args.samples])
        manifest.write(
            result_dir,
            profile,
            resuming=args.resume and bool(started),
            model=args.model,
            answer_model=args.answer_model or args.model,
            plan_cache=str(args.plan_cache.resolve()) if args.plan_cache else "",
            # The cache's CONTENT, not just where it lives: a run can write a
            # freshly compiled plan into the cache it is reading, and the path
            # alone cannot show that it did.
            plan_cache_sha=manifest.plan_cache_digest(args.plan_cache),
            samples=args.samples,
            corpus_sha=manifest.corpus_digest([DATASET, CODEBOOKS_ROOT / "locomo"]),
        )
        failures = []
        for index in args.samples:
            sample = data[index]
            failures.extend(
                evaluate_sample(
                    sample,
                    model=args.model,
                    answer_model=args.answer_model or args.model,
                    workers=args.workers,
                    output=result_dir / f"results_{sample['sample_id']}.jsonl",
                    resume=args.resume,
                    plan_cache=args.plan_cache.resolve() if args.plan_cache else None,
                    policy=policy,
                )
            )
        # The run has stopped compiling; the cache on disk is now what the
        # release will ship, so re-record its digest.
        manifest.seal_plan_cache(result_dir, args.plan_cache)
        if failures:
            failed_path = result_dir / "failed.txt"
            failed_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
            print(f"{len(failures)} questions failed; see {failed_path}")
            return 1
        (result_dir / "failed.txt").unlink(missing_ok=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
