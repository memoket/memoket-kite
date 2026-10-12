"""Leak-safe construction of LoCoMo-compatible ASR system inputs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .data_tools import iter_sessions, load_json


def _required_string(value: Mapping[str, Any], field: str, context: str) -> str:
    if field not in value:
        raise ValueError(f"{context} is missing required field {field!r}")
    result = value[field]
    if not isinstance(result, str):
        raise TypeError(f"{context} field {field!r} must be a string")
    return result


def build_system_turn(
    turn: Mapping[str, Any], *, sample_id: str, session_id: str
) -> dict[str, str]:
    """Copy one turn into the strict system-facing allowlist.

    Empty ``asr_text`` values are valid ASR outputs and remain empty. The
    function never falls back to the clean ``text`` field.
    """

    context = f"sample {sample_id!r}, session {session_id!r}, turn {turn.get('dia_id')!r}"
    system_turn = {
        "dia_id": _required_string(turn, "dia_id", context),
        "speaker": _required_string(turn, "speaker", context),
        "content": _required_string(turn, "asr_text", context),
    }

    if "blip_caption" in turn:
        system_turn["blip_caption"] = _required_string(turn, "blip_caption", context)
    return system_turn


def build_system_conversation(sample: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the ordered session view for one LoCoMo sample."""

    sample_id = _required_string(sample, "sample_id", "sample")
    conversation = sample.get("conversation")
    if not isinstance(conversation, dict):
        raise TypeError(f"sample {sample_id!r} field 'conversation' must be an object")

    sessions: list[dict[str, Any]] = []
    for session_id, turns in iter_sessions(dict(sample)):
        if not isinstance(turns, list):
            raise TypeError(f"sample {sample_id!r}, session {session_id!r} must be an array")
        timestamp_field = f"{session_id}_date_time"
        timestamp = _required_string(conversation, timestamp_field, f"sample {sample_id!r}")
        sessions.append(
            {
                "session_id": session_id,
                "timestamp": timestamp,
                "turns": [
                    build_system_turn(turn, sample_id=sample_id, session_id=session_id)
                    for turn in turns
                ],
            }
        )
    return sessions


def build_system_inputs(
    samples: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group system-facing conversations by their upstream ``sample_id``."""

    result: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        sample_id = _required_string(sample, "sample_id", "sample")
        if sample_id in result:
            raise ValueError(f"duplicate sample_id {sample_id!r}")
        result[sample_id] = build_system_conversation(sample)
    return result


def load_system_inputs(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load one tier and construct strict system-facing conversations."""

    payload = load_json(path)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise TypeError(f"{path} must contain a JSON array of sample objects")
    return build_system_inputs(payload)
