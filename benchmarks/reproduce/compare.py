"""Compare two sealed benchmark runs question by question, offline."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from benchmarks.locomo import protocol as locomo
from benchmarks.longmemeval import protocol as longmemeval
from benchmarks.reproduce import sealed

JUDGE_FIELDS = ("judge_model", "provider", "protocol_sha")
ARTIFACT_FIELDS = ("results_sha", "judged_sha", "run_manifest_sha", "score_sha")


def _index(records: list[dict], fields: tuple[str, ...], role: str) -> dict:
    indexed = {}
    for record in records:
        identity = tuple(record[field] for field in fields)
        if identity in indexed:
            raise ValueError(f"{role} contains a duplicate question ID {identity!r}")
        indexed[identity] = record
    if not indexed:
        raise ValueError(f"{role} contains no judged questions")
    return indexed


def _metrics(before: dict, after: dict, changes: Counter) -> dict:
    metrics = {}
    for name in before.keys() - {"n"}:
        old, new = before[name], after[name]
        metrics[name] = {
            "before": old,
            "after": new,
            "delta": new - old if old is not None and new is not None else None,
        }
    return {
        "n": before["n"],
        "improved": changes[(0, 1)],
        "regressed": changes[(1, 0)],
        "unchanged": changes[(0, 0)] + changes[(1, 1)],
        "metrics": metrics,
    }


def compare(name: str, baseline: Path, candidate: Path) -> dict:
    """Verify both seals, then compare verdicts on the exact same questions."""
    before, before_seal = sealed.verified_run(name, baseline / "judged.jsonl")
    after, after_seal = sealed.verified_run(name, candidate / "judged.jsonl")
    for field in JUDGE_FIELDS:
        if not before_seal[field] or before_seal[field] != after_seal[field]:
            raise ValueError(
                f"runs must declare the same judge configuration ({field} differs or is missing)"
            )

    if name == "locomo":
        id_fields = ("sample_id", "qa_idx")
        cohort_fields = ("question", "gold", "category", "gold_evidence")
        verdict_field = "J"
        summary_key = "categories"
        summarize = locomo.summarize
    else:
        id_fields = ("question_id",)
        cohort_fields = ("question", "gold", "question_type", "answer_session_ids")
        verdict_field = "ok"
        summary_key = "question_types"
        summarize = longmemeval.summarize

    baseline_questions = _index(before, id_fields, "baseline")
    candidate_questions = _index(after, id_fields, "candidate")
    if baseline_questions.keys() != candidate_questions.keys():
        missing = len(baseline_questions.keys() - candidate_questions.keys())
        extra = len(candidate_questions.keys() - baseline_questions.keys())
        raise ValueError(f"question sets differ ({missing} missing, {extra} extra in candidate)")

    overall: Counter = Counter()
    groups: dict[str, Counter] = defaultdict(Counter)
    changed = []
    identities = sorted(baseline_questions)
    for identity in identities:
        old = baseline_questions[identity]
        new = candidate_questions[identity]
        for field in cohort_fields:
            if old[field] != new[field]:
                raise ValueError(f"question {identity!r} has different {field} in the candidate")
        verdicts = (old[verdict_field], new[verdict_field])
        if any(verdict not in (0, 1) for verdict in verdicts):
            raise ValueError(f"question {identity!r} has a non-binary verdict")
        group = (
            locomo.CATEGORIES[int(old["category"])]
            if name == "locomo"
            else longmemeval.question_type(old)
        )
        overall[verdicts] += 1
        groups[group][verdicts] += 1
        if verdicts[0] != verdicts[1]:
            changed.append(
                {
                    **dict(zip(id_fields, identity)),
                    "group": group,
                    "before": verdicts[0],
                    "after": verdicts[1],
                }
            )

    before_metrics = summarize([baseline_questions[identity] for identity in identities])
    after_metrics = summarize([candidate_questions[identity] for identity in identities])
    return {
        "benchmark": name,
        "judge_model": before_seal["judge_model"],
        "protocol_sha": before_seal["protocol_sha"],
        "baseline": {key: before_seal[key] for key in ARTIFACT_FIELDS},
        "candidate": {key: after_seal[key] for key in ARTIFACT_FIELDS},
        "overall": _metrics(before_metrics["overall"], after_metrics["overall"], overall),
        "groups": {
            group: _metrics(
                before_metrics[summary_key][group], after_metrics[summary_key][group], counts
            )
            for group, counts in sorted(groups.items())
        },
        "changed": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=sealed.PROTOCOLS)
    parser.add_argument("baseline", type=Path, help="baseline results directory")
    parser.add_argument("candidate", type=Path, help="candidate results directory")
    args = parser.parse_args(argv)
    try:
        report = compare(args.benchmark, args.baseline, args.candidate)
        payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    except (OSError, KeyError, RuntimeError, TypeError, ValueError) as error:
        parser.exit(1, f"comparison failed: {error}\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
