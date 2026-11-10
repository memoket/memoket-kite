"""Judge and score LoCoMo result rows with the published protocol."""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmarks.common import (
    canonical,
    judging,
    manifest,
    ownership,
    publish,
    singlewriter,
)
from benchmarks.common.judging import judge_cache_key
from benchmarks.common.paths import CODEBOOKS_ROOT, RESULTS_ROOT, safe_tag
from benchmarks.locomo import DEFAULT_TAG
from benchmarks.locomo.evaluate import DATASET, _load_dataset
from benchmarks.locomo.protocol import CATEGORIES, JUDGE_PROMPT, preprocess_answer, summarize
from memoket_kite.providers.llm import llm_json


def _records_from(snapshot: dict) -> list[dict]:
    """Parse the snapshot the judge will score; the filename names the sample."""
    records = []
    for name in sorted(snapshot):
        sample_id = name[len("results_") : -len(".jsonl")]
        for line in snapshot[name].decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            recorded = record.get("sample_id")
            if recorded is not None and recorded != sample_id:
                # The filename is the authority; accepting the row's own claim
                # would let a record assert it belongs to another conversation.
                raise SystemExit(
                    f"{name} holds a row labelled {recorded!r}; it does not belong here"
                )
            record["sample_id"] = sample_id
            records.append(record)
    return records


def _corpus_moved(result_dir: Path, allow_legacy: bool) -> bool:
    """The corpus that produced these answers must still be the one on disk.

    Judging costs money and publishes a score under the run's manifest. If the
    dataset or a codebook moved since the run, that manifest no longer
    describes what is about to be scored. Returns whether the binding had to
    be waived, so the score can say so rather than read as verified.
    """
    path = result_dir / "manifest.json"
    recorded = json.loads(path.read_text()).get("corpus_sha") if path.exists() else ""
    current = manifest.corpus_digest([DATASET, CODEBOOKS_ROOT / "locomo"])
    if not recorded:
        # Skipping the check when the field is absent would fail open: an
        # artifact could escape the binding simply by not declaring what it
        # read. Waiving it has to be asked for.
        if not allow_legacy:
            raise SystemExit(
                f"{result_dir.name} declares no corpus_sha, so what it read cannot be "
                f"verified; re-run it, or score it with --allow-legacy"
            )
        return True
    if recorded != current:
        raise SystemExit(
            f"{result_dir.name} was run against corpus {recorded}, but the corpus on "
            f"disk is now {current}; restore it or re-run before scoring"
        )
    return False


def _canonical(
    records: list[dict], samples, result_dir: Path, allow_legacy: bool = False
) -> tuple[bool, list[dict]]:
    """Hold the results to the corpus and take the judge's inputs from it."""
    corpus_unverified = _corpus_moved(result_dir, allow_legacy)
    data = _load_dataset()
    expected = {
        (data[index]["sample_id"], qa_idx): item
        for index in samples
        for qa_idx, item in enumerate(data[index]["qa"])
        if item.get("category") in CATEGORIES
    }
    return corpus_unverified, canonical.reconcile(
        records,
        expected,
        key_of=lambda row: (row["sample_id"], row["qa_idx"]),
        fields=(
            ("question", "question"),
            ("gold", "answer"),
            ("category", "category"),
            ("gold_evidence", "evidence"),
        ),
    )


def judge_records(records: list[dict], *, model: str, workers: int, cache_path: Path) -> None:
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    lock = threading.Lock()

    def judge(record: dict) -> None:
        gold = preprocess_answer(int(record["category"]), str(record["gold"]))
        prompt = JUDGE_PROMPT.format(
            question=record["question"],
            answer=gold,
            response=record["answer"],
        )
        # A verdict belongs to the judge that produced it. The key covers the
        # fully rendered prompt and the model name, because keying on the
        # question and answer alone would let a different judge — or a reworded
        # rubric — reuse the previous label while the report names the new one.
        key = judge_cache_key(prompt, model)
        if key in cache:
            record["J"] = cache[key]
            return
        response = llm_json(prompt, model=model)
        verdict = 1.0 if str(response.get("label", "")).upper() == "CORRECT" else 0.0
        record["J"] = verdict
        with lock:
            cache[key] = verdict
            judging.checkpoint(cache, cache_path)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(judge, records))
    # Atomic, like every checkpoint before it. A plain write here could
    # truncate a complete, already-paid-for cache if the process died
    # mid-write — the one moment the whole round is recoverable from.
    judging.checkpoint(cache, cache_path, every=1)


def _print_summary(metrics: dict, *, model: str) -> None:
    overall = metrics["overall"]
    print(f"\n== LoCoMo ({overall['n']} QA, judge={model}) ==")
    print(f"{'category':<14}{'n':>6}{'accuracy':>11}{'F1':>9}{'BLEU-1':>10}{'ev-rec':>10}")
    for name, values in metrics["categories"].items():
        evidence = values["evidence_recall"]
        evidence_text = "n/a" if evidence is None else f"{evidence:.3f}"
        print(
            f"{name:<14}{values['n']:>6}{values['accuracy']:>11.3f}"
            f"{values['f1']:>9.3f}{values['bleu1']:>10.3f}{evidence_text:>10}"
        )
    evidence = overall["evidence_recall"]
    evidence_text = "n/a" if evidence is None else f"{evidence:.3f}"
    print(
        f"{'TOTAL':<14}{overall['n']:>6}{overall['accuracy']:>11.3f}"
        f"{overall['f1']:>9.3f}{overall['bleu1']:>10.3f}{evidence_text:>10}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=DEFAULT_TAG)
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--results", type=Path)
    parser.add_argument(
        "--allow-legacy",
        action="store_true",
        help="score an artifact whose judge manifest predates framed source digests",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="recompute metrics from an existing judged.jsonl without LLM calls",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be a positive integer")
    if args.results is None:
        try:
            args.tag = safe_tag(args.tag)
        except ValueError as error:
            parser.error(str(error))
    result_dir = args.results or RESULTS_ROOT / f"locomo-{args.tag}"
    judged_path = result_dir / "judged.jsonl"
    # Assigned by the offline check; an online run has verified nothing to
    # weaken. Bound before either branch so the summary below reads a defined
    # value on both paths, after the judging has been paid for.
    source_binding_weak = False
    corpus_unverified = False
    # One scorer per tag. The drift check and the seal both assume this
    # process is the only writer; two concurrent scorers defeat each
    # other's guards, and `refuse_overwrite` is a no-op in exactly the
    # state two fresh scorers share.
    with singlewriter.held(result_dir, purpose="scoring"):
        if args.offline:
            if not judged_path.exists():
                raise SystemExit(f"missing judged results: {judged_path}")
            _judge_model, source_binding_weak, verified = judging.check_offline(
                result_dir,
                args.judge_model,
                source=sorted(result_dir.glob("results_*.jsonl")),
                judged=judged_path,
                protocol=JUDGE_PROMPT,
                run=result_dir / "manifest.json",
                score=result_dir / "score.json",
                allow_legacy=args.allow_legacy,
            )
            # These are the bytes `check_offline` verified, handed back rather than
            # re-read: reopening the file would score content the check never saw.
            records = [
                json.loads(line) for line in verified.decode("utf-8").splitlines() if line.strip()
            ]
            samples = ownership.declared_samples(result_dir)
            if samples is None:
                raise SystemExit(f"{result_dir.name} has no run manifest; coverage is unverifiable")
            corpus_unverified, records = _canonical(records, samples, result_dir, args.allow_legacy)
        else:
            judging.refuse_overwrite(result_dir, args.judge_model, protocol=JUDGE_PROMPT)
            # Fix the file set from the manifest, read it once, and score that copy.
            # Re-globbing after judging would let a file written mid-run enter
            # the manifest without ever having been judged.
            samples = ownership.declared_samples(result_dir)
            if samples is None:
                raise SystemExit(
                    f"{result_dir} has no manifest, so which results belong to this run "
                    f"is unknown; re-run the evaluator under a fresh --tag"
                )
            data = _load_dataset()
            owned = ownership.check(result_dir, [data[index]["sample_id"] for index in samples])
            if len(owned) != len(samples):
                raise SystemExit(
                    f"{result_dir.name} declares {len(samples)} samples but only "
                    f"{len(owned)} result files exist; finish the run before scoring it"
                )
            # The run manifest is sealed from THESE bytes, not from a second
            # read once judging is over: between the two, another process
            # could give these answers a different run identity.
            run_path = result_dir / "manifest.json"
            run_bytes = run_path.read_bytes() if run_path.exists() else b""
            observed = publish.observe([run_path, *owned])
            snapshot = judging.read_snapshot(owned)
            corpus_unverified, records = _canonical(
                _records_from(snapshot), samples, result_dir, args.allow_legacy
            )
            judge_records(
                records,
                model=args.judge_model,
                workers=args.workers,
                cache_path=result_dir / "judge_cache.json",
            )
            metrics = summarize(records)
            metrics["provenance"] = {
                "source_binding": "verified",
                "corpus_binding": "unverified" if corpus_unverified else "verified",
            }
            publish.publish(
                result_dir,
                observed=observed,
                sealed={run_path: run_bytes},
                # `ownership.check` refuses any results file the manifest does not
                # declare, so re-running it here catches one written mid-judging.
                guard=lambda: ownership.check(
                    result_dir, [data[index]["sample_id"] for index in samples]
                ),
                judged="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                score=metrics,
                manifest=lambda sealed: judging.write_manifest(
                    result_dir,
                    model=args.judge_model,
                    source=snapshot,
                    judged=judged_path,
                    protocol=JUDGE_PROMPT,
                    run=result_dir / "manifest.json",
                    score=result_dir / "score.json",
                    sealed=sealed,
                ),
            )
        if args.offline:
            metrics = summarize(records)
            metrics["provenance"] = {
                "source_binding": "legacy_weak" if source_binding_weak else "verified",
                "corpus_binding": "unverified" if corpus_unverified else "verified",
            }
        if source_binding_weak:
            print("  PROVENANCE: LEGACY / WEAK SOURCE BINDING — not a release-grade result")
        _print_summary(metrics, model=args.judge_model)
        # Every figure below comes from the per-question ledger, which prices the
        # text the reader was actually given. Tokenising `memories_text` instead
        # would measure a reconstruction the reader never saw: it truncates
        # hydration to two short quotes and omits the session, profile and
        # instance blocks entirely.
        # Average only over records that carry a ledger. Dividing by every record
        # would report an artifact with partial telemetry as systematically
        # cheaper, with nothing on screen to say the denominator was wrong.
        ledgers = [entry for record in records if (entry := record.get("telemetry"))]
        print(f"token telemetry coverage: {len(ledgers)}/{len(records)}")
        if not ledgers:
            print("  no ledger on these records — token figures unavailable")
        elif len(ledgers) < len(records):
            print("  partial coverage: means below are NOT comparable to a full run")
        if ledgers:
            admitted = sum(entry.get("compact_total", 0) for entry in ledgers) / len(ledgers)
            rendered = sum(entry.get("real_total", 0) for entry in ledgers) / len(ledgers)
            reader = sum(
                sum(call.get("prompt_tokens", 0) for call in entry.get("reader_calls") or [])
                for entry in ledgers
            ) / len(ledgers)
            print(f"mean row-admission budget:      {admitted:.0f}")
            print(f"mean rendered memory context:   {rendered:.0f}  (initial pack)")
            print(f"mean reader prompt input:       {reader:.0f}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
