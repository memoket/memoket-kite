"""Verify release artifacts and recompute published metrics without an LLM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.locomo.protocol import summarize as summarize_locomo
from benchmarks.longmemeval.protocol import summarize as summarize_longmemeval
from benchmarks.reproduce import sealed
from benchmarks.reproduce.common import artifact_path, dataset_path, load_manifest, sha256

SUMMARIZERS = {"locomo": summarize_locomo, "longmemeval": summarize_longmemeval}


def verify_benchmark(name: str, manifest: dict) -> dict:
    specification = manifest["benchmarks"][name]
    pinned = manifest["datasets"][name]
    dataset = dataset_path(pinned["path"])
    if not dataset.is_file() or sha256(dataset) != pinned["sha256"]:
        raise RuntimeError(f"{name}: pinned dataset is missing or has the wrong checksum")
    codebook_dir = artifact_path(specification["codebook_path"])
    result_path = artifact_path(specification["result_path"])
    codebook_count = len(list(codebook_dir.glob("*.xml"))) if codebook_dir.exists() else 0
    if codebook_count != specification["codebooks"]:
        raise RuntimeError(
            f"{name}: expected {specification['codebooks']} Codebooks, found {codebook_count}"
        )
    if not result_path.exists():
        raise RuntimeError(f"{name}: missing judged results: {result_path}")
    # Every digest the seal records, recomputed — then the metric is taken from
    # the bytes that check returned. Recomputing from a re-opened file would
    # accept any edit to the verdicts that preserved the row count.
    records = sealed.verified_records(name, result_path)
    if len(records) != specification["result_rows"]:
        raise RuntimeError(
            f"{name}: expected {specification['result_rows']} rows, found {len(records)}"
        )
    metrics = SUMMARIZERS[name](records)
    score = metrics["overall"][specification["metric"]]
    expected = specification.get("score")
    if expected is None:
        raise RuntimeError(f"{name}: release score is not finalized in manifest.json")
    if abs(score - float(expected)) > 1e-12:
        raise RuntimeError(f"{name}: expected score {expected}, recomputed {score}")
    binding = sealed.seal(result_path.parent)
    sealed.require_same_corpus(name, result_path.parent, dataset=dataset, codebooks=codebook_dir)
    print(
        f"{name}: {codebook_count} Codebooks, {len(records)} rows, "
        f"{specification['metric']}={score:.6f} "
        f"(judged by {binding['judge_model']} at {binding['provider']}, "
        f"seal schema {binding['schema']})"
    )
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=tuple(SUMMARIZERS))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    args = parser.parse_args(argv)
    try:
        metrics = verify_benchmark(args.benchmark, load_manifest(args.manifest))
    except (OSError, KeyError, RuntimeError, TypeError, ValueError) as error:
        parser.exit(1, f"verification failed: {error}\n")
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
