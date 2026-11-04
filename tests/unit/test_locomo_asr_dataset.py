from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from benchmarks.locomo_asr.data_tools import load_json, word_error_rate
from benchmarks.locomo_asr.generation.assets import (
    REFERENCE_VOICES,
    validate_reference_voice,
)
from benchmarks.locomo_asr.loader import build_system_inputs, build_system_turn
from benchmarks.locomo_asr.validate import (
    validate_data_file_set,
    validate_manifest_contract,
    validate_noisy,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "benchmarks" / "locomo_asr"


def sample_turn(*, asr_text: object = "heard text", wer: object = 0.5) -> dict:
    return {
        "sample_id": "sample-1",
        "conversation": {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1": [
                {
                    "dia_id": "D1:1",
                    "speaker": "Alice",
                    "text": "clean secret",
                    "asr_text": asr_text,
                    "wer": wer,
                    "blip_caption": "three turtles",
                    "img_url": ["https://example.invalid/image.jpg"],
                    "query": "ignored image search",
                    "re-download": True,
                }
            ],
            "session_1_date_time": "1:00 pm on 1 January, 2024",
        },
        "qa": [
            {
                "question": "What is hidden?",
                "answer": "gold secret",
                "category": 1,
                "evidence": ["D1:1"],
            }
        ],
    }


def test_loader_builds_only_the_locomo_compatible_asr_allowlist() -> None:
    result = build_system_inputs([sample_turn()])

    assert set(result) == {"sample-1"}
    session = result["sample-1"][0]
    assert session == {
        "session_id": "session_1",
        "timestamp": "1:00 pm on 1 January, 2024",
        "turns": [
            {
                "dia_id": "D1:1",
                "speaker": "Alice",
                "content": "heard text",
                "blip_caption": "three turtles",
            }
        ],
    }
    serialized = json.dumps(result)
    for forbidden_value in (
        "clean secret",
        "gold secret",
        "example.invalid",
        "ignored image search",
    ):
        assert forbidden_value not in serialized


def test_loader_preserves_empty_asr_output_without_clean_fallback() -> None:
    turn = sample_turn(asr_text="")["conversation"]["session_1"][0]
    result = build_system_turn(turn, sample_id="sample-1", session_id="session_1")
    assert result["content"] == ""


@pytest.mark.parametrize("asr_value", [None, 7, [], {}])
def test_loader_rejects_missing_or_non_string_asr_text(asr_value: object) -> None:
    turn = sample_turn()["conversation"]["session_1"][0]
    if asr_value is None:
        turn.pop("asr_text")
    else:
        turn["asr_text"] = asr_value

    with pytest.raises((TypeError, ValueError), match="asr_text"):
        build_system_turn(turn, sample_id="sample-1", session_id="session_1")


def test_word_error_rate_matches_the_reference_definition() -> None:
    assert word_error_rate("Hello, world!", "hello world") == 0.0
    assert word_error_rate("one two three", "one four") == 2 / 3
    assert word_error_rate("", "hallucinated") == 0.0
    assert word_error_rate("one", "one plus two") == 2.0


def test_manifest_freezes_eight_complete_tiers() -> None:
    manifest = load_json(DATASET_ROOT / "reference_manifest.json")
    assert manifest["dataset_version"] == "1.0.0"
    assert manifest["counts"] == {
        "conversations": 10,
        "turns": 5_882,
        "questions": 1_986,
    }
    assert len(manifest["outputs"]) == 8
    assert manifest["pipeline"]["snr_db"] == [8, 7, 6, 5, 4, 3, 2]
    assert all(len(output["sha256"]) == 64 for output in manifest["outputs"].values())
    assert {path.name for path in (DATASET_ROOT / "data").glob("*.json")} == set(
        manifest["outputs"]
    )


def test_manifest_contract_rejects_a_relabelled_snr_tier() -> None:
    manifest = load_json(DATASET_ROOT / "reference_manifest.json")
    manifest["pipeline"]["snr_db"][-1] = 9
    manifest["outputs"]["locomo10-asr-musan-full-snr9.json"] = manifest["outputs"].pop(
        "locomo10-asr-musan-full-snr2.json"
    )

    with pytest.raises(AssertionError, match="SNR tiers"):
        validate_manifest_contract(manifest)


def test_data_file_set_reports_missing_and_unexpected_json(tmp_path: Path) -> None:
    (tmp_path / "expected.json").write_text("{}", encoding="utf-8")
    (tmp_path / "unexpected.json").write_text("{}", encoding="utf-8")

    with pytest.raises(
        AssertionError,
        match=r"missing=\[missing\.json\]; unexpected=\[unexpected\.json\]",
    ):
        validate_data_file_set(tmp_path, {"expected.json", "missing.json"})


@pytest.mark.parametrize("invalid_wer", [True, math.nan, math.inf, -math.inf])
def test_validator_rejects_boolean_or_non_finite_wer(invalid_wer: object) -> None:
    source = [sample_turn()]
    source[0]["conversation"]["session_1"][0].pop("asr_text")
    source[0]["conversation"]["session_1"][0].pop("wer")
    noisy = copy.deepcopy(source)
    noisy[0]["conversation"]["session_1"][0]["asr_text"] = "heard text"
    noisy[0]["conversation"]["session_1"][0]["wer"] = invalid_wer

    with pytest.raises(AssertionError, match="WER"):
        validate_noisy(noisy, source, "tier.json")


def test_json_loader_rejects_non_standard_constants(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text('{"wer": NaN}', encoding="utf-8")
    with pytest.raises(ValueError, match="non-standard JSON constant NaN"):
        load_json(path)


def reference_voice_item(role: str, *, filename: str = "renamed.onnx") -> dict:
    expected = REFERENCE_VOICES[role]
    return {
        "filename": filename,
        "sha256": expected["model_sha256"],
        "config_filename": f"{filename}.json",
        "config_sha256": expected["config_sha256"],
    }


def test_reference_voice_validation_uses_role_hashes_not_local_filenames() -> None:
    validate_reference_voice(
        reference_voice_item("voice-a"),
        role="voice-a",
        allow_mismatch=False,
    )


def test_reference_voice_validation_rejects_unknown_asset_bytes() -> None:
    item = reference_voice_item("voice-a", filename="custom.onnx")
    item["sha256"] = "0" * 64

    with pytest.raises(RuntimeError, match=r"voice-a model custom\.onnx SHA-256"):
        validate_reference_voice(item, role="voice-a", allow_mismatch=False)


def test_reference_voice_validation_rejects_swapped_roles() -> None:
    with pytest.raises(RuntimeError, match=r"voice-a model"):
        validate_reference_voice(
            reference_voice_item("voice-b"),
            role="voice-a",
            allow_mismatch=False,
        )


def test_reference_voice_validation_allows_explicit_variant() -> None:
    item = reference_voice_item("voice-a", filename="custom.onnx")
    item["sha256"] = "0" * 64
    item["config_sha256"] = "f" * 64

    validate_reference_voice(item, role="voice-a", allow_mismatch=True)
