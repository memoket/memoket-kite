#!/usr/bin/env python3
"""Validate the structure, WER values, provenance, and hashes of LoCoMo-ASR."""

from __future__ import annotations

import argparse
import copy
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .data_tools import iter_turns, load_json, sha256_file, word_error_rate
from .loader import build_system_inputs

GENERATED_FIELDS = {"asr_text", "wer"}
CLEAN_FILENAME = "locomo10-clean-asr-view.json"
REFERENCE_DATASET_VERSION = "1.0.0"
REFERENCE_SNRS = (8, 7, 6, 5, 4, 3, 2)


def load_dataset(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise TypeError(f"{path} must contain a JSON array of sample objects")
    return payload


def strip_generated_fields(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stripped = copy.deepcopy(data)
    for sample in stripped:
        for turn in iter_turns(sample):
            for field in GENERATED_FIELDS:
                turn.pop(field, None)
    return stripped


def dataset_counts(data: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "conversations": len(data),
        "turns": sum(1 for sample in data for _ in iter_turns(sample)),
        "questions": sum(len(sample.get("qa", [])) for sample in data),
    }


def validate_manifest_contract(manifest: dict[str, Any]) -> set[str]:
    """Validate the fixed tier contract and return its expected filenames."""
    version = manifest.get("dataset_version")
    if version != REFERENCE_DATASET_VERSION:
        raise AssertionError(
            f"dataset version is {version!r}, expected {REFERENCE_DATASET_VERSION!r}"
        )
    pipeline = manifest.get("pipeline")
    if not isinstance(pipeline, dict) or pipeline.get("snr_db") != list(REFERENCE_SNRS):
        raise AssertionError(
            f"pipeline SNR tiers are {None if not isinstance(pipeline, dict) else pipeline.get('snr_db')!r}, "
            f"expected {list(REFERENCE_SNRS)!r}"
        )
    expected = {CLEAN_FILENAME}
    expected.update(f"locomo10-asr-musan-full-snr{snr}.json" for snr in REFERENCE_SNRS)
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise TypeError("manifest outputs must be a JSON object")
    if set(outputs) != expected:
        raise AssertionError(
            "manifest outputs do not match the fixed clean and SNR 8 through 2 tiers"
        )
    return expected


def validate_loader_view(
    data: list[dict[str, Any]], expected_counts: dict[str, int], filename: str
) -> None:
    system_inputs = build_system_inputs(data)
    actual = {
        "conversations": len(system_inputs),
        "turns": sum(
            len(session["turns"]) for sessions in system_inputs.values() for session in sessions
        ),
    }
    expected = {key: expected_counts[key] for key in ("conversations", "turns")}
    if actual != expected:
        raise AssertionError(f"{filename} loader view counts are {actual}, expected {expected}")


def validate_data_file_set(data_dir: Path, expected_filenames: Iterable[str]) -> None:
    expected = set(expected_filenames)
    actual = {path.name for path in data_dir.glob("*.json") if path.is_file()}
    if actual == expected:
        return

    missing = ", ".join(sorted(expected - actual)) or "none"
    unexpected = ", ".join(sorted(actual - expected)) or "none"
    raise AssertionError(
        "data file set differs from the reference manifest: "
        f"missing=[{missing}]; unexpected=[{unexpected}]"
    )


def _finite_wer(value: Any, *, filename: str, dia_id: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"{filename} {dia_id} has no numeric WER")
    result = float(value)
    if not math.isfinite(result):
        raise AssertionError(f"{filename} {dia_id} has non-finite WER")
    return result


def validate_clean(clean: list[dict[str, Any]], source: list[dict[str, Any]]) -> None:
    if strip_generated_fields(clean) != source:
        raise AssertionError("clean view does not preserve the upstream dataset")
    for sample in clean:
        for turn in iter_turns(sample):
            dia_id = turn.get("dia_id")
            text = turn.get("text")
            if not isinstance(text, str):
                raise AssertionError(f"clean turn {dia_id} has no string text")
            if "asr_text" not in turn or not isinstance(turn["asr_text"], str):
                raise AssertionError(f"clean turn {dia_id} has no string asr_text")
            if turn["asr_text"] != text:
                raise AssertionError(f"clean turn {dia_id} has asr_text != text")
            stored_wer = _finite_wer(turn.get("wer"), filename="clean view", dia_id=dia_id)
            if stored_wer != 0.0:
                raise AssertionError(f"clean turn {dia_id} has non-zero WER")


def validate_noisy(
    noisy: list[dict[str, Any]], source: list[dict[str, Any]], filename: str
) -> tuple[int, float]:
    if strip_generated_fields(noisy) != source:
        raise AssertionError(f"{filename} changes fields inherited from LoCoMo")

    turns = 0
    wer_sum = 0.0
    for source_sample, noisy_sample in zip(source, noisy):
        source_turns = list(iter_turns(source_sample))
        noisy_turns = list(iter_turns(noisy_sample))
        if len(source_turns) != len(noisy_turns):
            raise AssertionError(f"{filename} has a turn-count mismatch")
        for reference, generated in zip(source_turns, noisy_turns):
            dia_id = generated.get("dia_id")
            if dia_id != reference.get("dia_id"):
                raise AssertionError(f"{filename} changes turn ordering")
            hypothesis = generated.get("asr_text")
            if not isinstance(hypothesis, str):
                raise AssertionError(f"{filename} {dia_id} has no string asr_text")
            reference_text = reference.get("text")
            if not isinstance(reference_text, str):
                raise AssertionError(f"{filename} {dia_id} has no string reference text")
            stored_wer = _finite_wer(generated.get("wer"), filename=filename, dia_id=dia_id)
            expected_wer = round(word_error_rate(reference_text, hypothesis), 4)
            if abs(stored_wer - expected_wer) > 5e-5:
                raise AssertionError(
                    f"{filename} {dia_id} stores WER {stored_wer}, recomputed {expected_wer}"
                )
            turns += 1
            wer_sum += stored_wer
    return turns, wer_sum / turns if turns else 0.0


def validate_release(
    *,
    data_dir: Path,
    manifest_path: Path,
    source_path: Path | None = None,
    require_reference_hashes: bool = False,
) -> list[str]:
    """Validate a release and return one human-readable status line per output."""

    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise TypeError(f"{manifest_path} must contain a JSON object")
    expected_counts = manifest["counts"]
    outputs = manifest["outputs"]
    if not isinstance(expected_counts, dict) or not isinstance(outputs, dict):
        raise TypeError("manifest counts and outputs must be JSON objects")
    expected_filenames = validate_manifest_contract(manifest)

    validate_data_file_set(data_dir, expected_filenames)
    clean_path = data_dir / CLEAN_FILENAME
    clean = load_dataset(clean_path)

    if source_path is not None:
        source = load_dataset(source_path)
        source_hash = sha256_file(source_path)
        expected_source_hash = manifest["upstream"]["source_sha256"]
        if source_hash != expected_source_hash:
            raise AssertionError(
                f"upstream source SHA-256 is {source_hash}, expected {expected_source_hash}"
            )
    else:
        source = strip_generated_fields(clean)

    counts = dataset_counts(source)
    if counts != expected_counts:
        raise AssertionError(f"dataset counts are {counts}, expected {expected_counts}")
    validate_clean(clean, source)

    mismatched_hashes: list[tuple[str, str, str]] = []
    messages: list[str] = []
    for filename, expected in outputs.items():
        path = data_dir / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        actual_hash = sha256_file(path)
        hash_matches = actual_hash == expected["sha256"]
        if not hash_matches:
            mismatched_hashes.append((filename, expected["sha256"], actual_hash))

        if filename == CLEAN_FILENAME:
            dataset = clean
            turns = counts["turns"]
            mean_wer = 0.0
        else:
            noisy = load_dataset(path)
            dataset = noisy
            if dataset_counts(noisy) != expected_counts:
                raise AssertionError(f"{filename} has unexpected dataset counts")
            turns, mean_wer = validate_noisy(noisy, source, filename)
        validate_loader_view(dataset, expected_counts, filename)
        if turns != expected_counts["turns"]:
            raise AssertionError(f"{filename} has {turns} turns")
        if abs(mean_wer - expected["mean_wer"]) > 1e-10:
            raise AssertionError(
                f"{filename} mean WER is {mean_wer:.10f}, expected {expected['mean_wer']:.10f}"
            )
        messages.append(
            f"OK  {filename}: turns={turns:,} mean_WER={mean_wer:.6f} "
            f"reference_hash={'yes' if hash_matches else 'NO'}"
        )

    if mismatched_hashes and require_reference_hashes:
        details = "\n".join(
            f"  {name}: expected {expected}, got {actual}"
            for name, expected, actual in mismatched_hashes
        )
        raise AssertionError(f"reference hash mismatch:\n{details}")
    if mismatched_hashes:
        messages.append(
            "WARNING: structural/WER validation passed, but one or more files "
            "are not byte-identical to the reference release."
        )
    else:
        messages.append("All files are byte-identical to the reference release.")
    return messages


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=here / "data")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help=(
            "optional upstream locomo10.json; without it the source structure "
            "is recovered from the clean view"
        ),
    )
    parser.add_argument("--manifest", type=Path, default=here / "reference_manifest.json")
    parser.add_argument(
        "--require-reference-hashes",
        action="store_true",
        help="fail instead of reporting when a file differs byte-for-byte",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for message in validate_release(
        data_dir=args.data_dir,
        manifest_path=args.manifest,
        source_path=args.source,
        require_reference_hashes=args.require_reference_hashes,
    ):
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
