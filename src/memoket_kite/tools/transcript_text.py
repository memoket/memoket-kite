"""Read timestamped, speaker-labeled plain-text transcripts."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .contracts import ToolError, decode_object, local_timestamp, read_utf8, utc_timestamp

_TIMESTAMP = re.compile(r"([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)[ \t]*")
_LONG_MINUTES = re.compile(r"([0-9]{3,8}:[0-5][0-9])[ \t]*")


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


def _transcript_segments(text: str, *, extended_minutes: bool) -> tuple[list[dict], bool]:
    segments = []
    elapsed = None
    speaker = None
    lines = [
        (number, line)
        for number, line in enumerate(re.split(r"\r\n|\r|\n", text), 1)
        if line.strip()
    ]
    has_extended_marker = False
    for i, (number, line) in enumerate(lines):
        marker = _TIMESTAMP.fullmatch(line)
        if extended_minutes and marker is None:
            # A long minutes marker requires a speaker header so a number
            # written as a standalone line in a turn can still be quoted.
            if (
                (long_marker := _LONG_MINUTES.fullmatch(line))
                and i + 1 < len(lines)
                and lines[i + 1][1].strip().startswith("Speaker ")
            ):
                marker = long_marker
                has_extended_marker = True
        if marker:
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
    return segments, has_extended_marker


def _source(path: Path, segments: list[dict], source_id: str | None) -> dict:
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


def transcript_text_source(path: Path, text: str, *, source_id: str | None = None) -> dict:
    segments, _ = _transcript_segments(text, extended_minutes=True)
    return _source(path, segments, source_id)


def transcript_text_reimport(path: Path, text: str) -> tuple[dict, list[dict] | None]:
    """Return the new source and the exact segment layout an older import used."""
    segments, has_extended_marker = _transcript_segments(text, extended_minutes=True)
    legacy = None
    if has_extended_marker:
        try:
            legacy, _ = _transcript_segments(text, extended_minutes=False)
        except ToolError:
            # A transcript that begins with a long marker could not have
            # produced a revision with the original parser.
            pass
    return _source(path, segments, None), legacy
