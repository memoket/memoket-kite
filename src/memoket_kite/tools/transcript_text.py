"""Read timestamped, speaker-labeled plain-text transcripts."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .contracts import ToolError, decode_object, local_timestamp, read_utf8, utc_timestamp

_TIMESTAMP = re.compile(r"([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)[ \t]*")


def transcript_recording_day(directory: Path, conversation_id: str) -> str | None:
    return transcript_recording_time(directory, conversation_id).get("source_date")


def transcript_recording_time(directory: Path, conversation_id: str) -> dict[str, str]:
    """Read a same-ID sidecar, retaining its original time precision."""
    sidecar = directory / f"{conversation_id}.json"
    if sidecar.is_symlink():
        raise ToolError("invalid_input", "recording metadata must be a regular JSON file")
    if not sidecar.exists():
        return {}
    if not sidecar.is_file():
        raise ToolError("invalid_input", "recording metadata must be a regular JSON file")
    metadata = decode_object(read_utf8(sidecar))
    identity = metadata.get("conversation_id")
    if type(identity) not in (int, str) or str(identity) != conversation_id:
        raise ToolError(
            "invalid_input", "recording metadata conversation ID must match the transcript"
        )
    started = metadata.get("started_at")
    if not isinstance(started, str):
        raise ToolError("invalid_input", "recording metadata requires started_at")
    try:
        if len(started) == 10:
            return {"source_date": date.fromisoformat(started).isoformat()}
        if len(started) == 19:
            local = local_timestamp(started)
            return {"source_date": local.date().isoformat(), "local_started_at": local.isoformat()}
        return {"source_date": utc_timestamp(started).date().isoformat(), "started_at": started}
    except (ValueError, OverflowError) as exc:
        raise ToolError("invalid_input", "invalid recording metadata started_at") from exc


def transcript_text_source(path: Path, text: str, *, source_id: str | None = None) -> dict:
    segments = []
    elapsed = None
    speaker = None
    for number, line in enumerate(re.split(r"\r\n|\r|\n", text), 1):
        if not line.strip():
            continue
        if marker := _TIMESTAMP.fullmatch(line):
            elapsed = 0
            for part in marker.group(1).split(":"):
                elapsed = elapsed * 60 + int(part)
            speaker = None
        elif elapsed is None:
            raise ToolError(
                "invalid_input", "expected a timestamp before the first transcript turn"
            )
        elif speaker is None:
            if not line.strip().startswith("Speaker "):
                raise ToolError("invalid_input", "expected a speaker label after the timestamp")
            speaker = line.strip()
        else:
            segments.append(
                {
                    "segment_id": f"L{number}",
                    "speaker_id": speaker,
                    "start_ms": elapsed * 1000,
                    "text": line,
                }
            )
    if not segments:
        raise ToolError("invalid_input", "timestamped transcript has no spoken lines")
    identity = source_id or path.stem.removesuffix("_transcript")
    return {
        "format": "conversation",
        "payload": {
            "source": "transcript",
            "conversation_id": identity,
            "segments": segments,
            "metadata": {"source_reference": path.name if source_id is None else identity},
        },
    }
