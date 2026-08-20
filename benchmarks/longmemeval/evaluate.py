"""Run KITE answering on the cleaned LongMemEval benchmark."""

from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmarks.common import manifest, singlewriter
from benchmarks.common.paths import CODEBOOKS_ROOT, DATASETS_ROOT, RESULTS_ROOT, safe_tag
from benchmarks.common.resume import completed_rows
from benchmarks.longmemeval import adapter, profile
from benchmarks.longmemeval.build import select_indices
from memoket_kite.core.algebra import Store
from memoket_kite.errors import ConfigurationError
from memoket_kite.pipeline import postproc
from memoket_kite.pipeline.answer import answer
from memoket_kite.pipeline.retrieve import RetrievalOptions

DATASET = DATASETS_ROOT / "longmemeval" / "longmemeval_s_cleaned.json"
CODEBOOK_DIR = CODEBOOKS_ROOT / "longmemeval"


def _pack_units(rows: list[dict]) -> list[str]:
    units = set()
    for row in rows:
        row_type = row.get("type")
        if row_type == "count":
            continue
        if row_type == "instance":
            units.update(
                mention_id.rsplit("F", 1)[0] for mention_id in str(row.get("src") or "").split()
            )
            continue
        identifier = row.get("id")
        if not identifier:
            continue
        if row_type == "fact":
            units.add(identifier.rsplit("F", 1)[0])
        elif row_type == "line":
            units.add(identifier.split(":", 1)[0])
        elif row_type == "unit":
            units.add(identifier)
    return sorted(units)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--answer-model", default="")
    parser.add_argument("--tag", default="v0.1.0")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan-cache", type=Path)
    parser.add_argument(
        "--question-id",
        action="append",
        default=[],
        metavar="ID",
        help="evaluate only these question ids; repeatable",
    )
    parser.add_argument(
        "--question-ids-file",
        type=Path,
        help="evaluate only the question ids listed in this file, one per line",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be a positive integer")
    try:
        args.tag = safe_tag(args.tag)
    except ValueError as error:
        parser.error(str(error))
    # Resolve the rule set first: a typo must cost nothing, not surface after
    # every question has already been paid for.
    postproc_policy = postproc.PostprocPolicy.resolve(
        getattr(profile, "POSTPROC_RULES", ""), os.environ.get("KITE_POSTPROC")
    )
    # The pipeline refuses an estimated tokenizer with a library error; a CLI
    # surfaces that as a clean exit before any question is paid for.
    try:
        RetrievalOptions.from_profile(profile)
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error
    with DATASET.open(encoding="utf-8") as stream:
        data = json.load(stream)
    wanted = list(args.question_id)
    selecting = bool(wanted)
    if args.question_ids_file:
        selecting = True
        try:
            listed = args.question_ids_file.read_text(encoding="utf-8").split()
        except OSError as error:
            parser.error(str(error))
        wanted.extend(listed)
    if selecting:
        # Asking for a selection and getting the whole corpus is the one
        # mistake this flag exists to prevent, so an empty one is an error.
        if not wanted:
            parser.error(f"{args.question_ids_file} names no question ids")
        # A named selection is exactly what was named.
        by_id = {str(item["question_id"]): index for index, item in enumerate(data)}
        if missing := [qid for qid in wanted if qid not in by_id]:
            parser.error(f"unknown question id(s): {', '.join(sorted(set(missing)))}")
        indices = sorted({by_id[qid] for qid in wanted})
    else:
        try:
            indices = select_indices(data, args.n)
        except ValueError as error:
            parser.error(str(error))
    result_dir = RESULTS_ROOT / f"longmemeval-{args.tag}"
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
        output = result_dir / "results.jsonl"
        if output.exists() and not args.resume:
            raise SystemExit(f"output exists; pass --resume or choose another tag: {output}")
        manifest.write(
            result_dir,
            profile,
            resuming=output.exists(),
            model=args.model,
            answer_model=args.answer_model or args.model,
            plan_cache=str(args.plan_cache.resolve()) if args.plan_cache else "",
            # The cache's CONTENT, not just where it lives: a run can write a
            # freshly compiled plan into the cache it is reading, and the path
            # alone cannot show that it did.
            plan_cache_sha=manifest.plan_cache_digest(args.plan_cache),
            n=args.n,
            # `--n` is a request; `select_indices` decides. Recording the request
            # would make `--n 0` (run everything) read as zero questions, and
            # `--n 999` as a run that can never complete.
            effective_n=len(indices),
            selected_sha=manifest.question_digest(data[index]["question_id"] for index in indices),
            corpus_sha=manifest.corpus_digest([DATASET, CODEBOOKS_ROOT / "longmemeval"]),
        )
        completed = {row["question_id"] for row in completed_rows(output)}
        pending = [index for index in indices if data[index]["question_id"] not in completed]
        print(
            f"dataset={DATASET} model={args.model} answer_model={args.answer_model or args.model} "
            f"workers={args.workers} questions={len(pending)} output={output}",
            flush=True,
        )
        lock = threading.Lock()
        failures = []

        def run(index: int) -> None:
            item = data[index]
            question_id = item["question_id"]
            codebook = CODEBOOK_DIR / f"{question_id}.xml"
            try:
                if not codebook.exists():
                    raise RuntimeError(f"missing Codebook: {codebook}")
                store, vocabulary = Store.load([str(codebook)])
                question_date = adapter.parse_dt(item.get("question_date", "") or "")
                result = answer(
                    store,
                    vocabulary,
                    profile,
                    item["question"],
                    model=args.model,
                    answer_model=args.answer_model or args.model,
                    reference_date=question_date.strftime("%Y-%m-%d") if question_date else "",
                    plan_cache=str(args.plan_cache.resolve()) if args.plan_cache else None,
                    postproc_policy=postproc_policy,
                )
                record = {
                    "question_id": question_id,
                    "question_type": item["question_type"],
                    "question": item["question"],
                    "gold": str(item.get("answer", "")),
                    "answer": result["answer"],
                    "pack_rows": result.get("pack_rows", []),
                    "n_evidence_rows": result.get("n_evidence_rows", 0),
                    "answer_session_ids": item.get("answer_session_ids", []),
                    "pack_units": _pack_units(result.get("pack_rows", [])),
                    "telemetry": result.get("telemetry"),
                }
                # The answerability verdict and any post-processing are the
                # answer stage's; it is the only place either is decided, so
                # the harness copies the record it left rather than asking a
                # second time.
                for key in ("answerable_by_construction", "answer_pre_postproc", "postproc"):
                    if key in result:
                        record[key] = result[key]
            except Exception as exc:
                with lock:
                    failures.append(f"{question_id}: {exc}")
                return
            with lock:
                with output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            list(executor.map(run, pending))
        # The run has stopped compiling; the cache on disk is now what the
        # release will ship, so re-record its digest.
        manifest.seal_plan_cache(result_dir, args.plan_cache)
        if failures:
            failure_path = result_dir / "failed.txt"
            failure_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
            print(f"{len(failures)} questions failed; see {failure_path}")
            return 1
        (result_dir / "failed.txt").unlink(missing_ok=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
