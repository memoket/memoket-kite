"""Reference asset identities for LoCoMo-ASR generation."""

from __future__ import annotations

from typing import Any

REFERENCE_VOICES = {
    "voice-a": {
        "model_filename": "en_US-lessac-medium.onnx",
        "model_sha256": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        "config_filename": "en_US-lessac-medium.onnx.json",
        "config_sha256": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    },
    "voice-b": {
        "model_filename": "en_US-ryan-medium.onnx",
        "model_sha256": "abf4c274862564ed647ba0d2c47f8ee7c9b717d27bdad9219100eb310db4047a",
        "config_filename": "en_US-ryan-medium.onnx.json",
        "config_sha256": "44034c056cb15681b2ad494307c7f3f2e4499d1253c700c711fa0a4607ffe78d",
    },
}


def validate_reference_voice(
    item: dict[str, Any],
    *,
    role: str,
    allow_mismatch: bool,
) -> None:
    """Require the reference model/config bytes for one generator voice role.

    Local filenames are informational: a byte-identical asset remains valid after
    being renamed, while swapping voice A and B is rejected unless the caller
    explicitly opts into a new dataset variant.
    """
    try:
        expected = REFERENCE_VOICES[role]
    except KeyError as exc:
        raise ValueError(f"unknown reference voice role: {role}") from exc

    pairs = (
        ("model", item["filename"], item["sha256"]),
        ("config", item["config_filename"], item["config_sha256"]),
    )
    for asset_type, filename, actual in pairs:
        expected_filename = expected[f"{asset_type}_filename"]
        expected_sha256 = expected[f"{asset_type}_sha256"]
        if actual != expected_sha256 and not allow_mismatch:
            raise RuntimeError(
                f"{role} {asset_type} {filename} SHA-256 is {actual}, expected "
                f"reference asset {expected_filename} ({expected_sha256}); pass "
                "--allow-asset-mismatch only if you intend to create a new variant"
            )
