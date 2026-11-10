"""Check a benchmark run locally before issuing any model requests."""

from __future__ import annotations

import argparse

from benchmarks.common.paths import CODEBOOKS_ROOT, RESULTS_ROOT, safe_tag
from benchmarks.common.singlewriter import LOCK_NAME
from benchmarks.locomo import evaluation_tag
from benchmarks.locomo import profile as locomo_profile
from benchmarks.longmemeval import profile as longmemeval_profile
from benchmarks.reproduce.common import dataset_path, load_manifest, sha256
from memoket_kite.errors import ConfigurationError, ProviderError
from memoket_kite.pipeline.retrieve import RetrievalOptions
from memoket_kite.providers.llm import ensure_configured

PROFILES = {"locomo": locomo_profile, "longmemeval": longmemeval_profile}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=tuple(PROFILES))
    parser.add_argument("--model", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--build-workers", type=int, default=1)
    parser.add_argument("--eval-workers", type=int, default=4)
    parser.add_argument("--mode", choices=("full", "resume"), default="full")
    parser.add_argument("--offline", action="store_true", help="check without model credentials")
    args = parser.parse_args(argv)
    name = args.benchmark
    if args.build_workers < 1 or args.eval_workers < 1:
        parser.error("worker counts must be positive integers")
    if not args.model.strip():
        parser.error("model cannot be empty")
    try:
        tag = evaluation_tag(args.tag) if name == "locomo" else safe_tag(args.tag)
        dataset = load_manifest()["datasets"][name]
        path = dataset_path(dataset["path"])
        RetrievalOptions.from_profile(PROFILES[name])
        if not args.offline:
            ensure_configured(args.model)
    except (ConfigurationError, ProviderError, ValueError) as error:
        parser.error(str(error))

    try:
        verified = path.is_file() and sha256(path) == dataset["sha256"]
    except OSError as error:
        parser.error(f"cannot read pinned dataset {path}: {error.strerror}")
    if not verified:
        parser.error(
            f"missing or invalid pinned dataset: {path}. "
            "Run python -m benchmarks.reproduce.prepare datasets with the same KITE_ARTIFACTS_DIR."
        )
    result = RESULTS_ROOT / f"{name}-{tag}"
    codebooks = CODEBOOKS_ROOT / name
    for directory in (RESULTS_ROOT, result, CODEBOOKS_ROOT, codebooks):
        if directory.exists() and not directory.is_dir():
            parser.error(f"expected a directory: {directory}")
    if (result / "judge_manifest.json").exists():
        parser.error(f"results for {tag!r} are sealed; choose a new TAG for evaluation")
    if (result / LOCK_NAME).exists():
        parser.error(f"result directory has a writer lock: {result / LOCK_NAME}")
    if args.mode == "full":
        if result.is_dir() and any(result.iterdir()):
            parser.error(f"result directory already exists: {result}. Use resume or a new TAG.")
        if any(not path.name.startswith(".") for path in codebooks.glob("*.xml")):
            parser.error(
                f"Codebooks already exist: {codebooks}. Use resume or a new artifact root."
            )
    print(f"Preflight passed for {name}: verified {path} and local configuration.")
    if args.offline:
        print("Offline check skipped model credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
