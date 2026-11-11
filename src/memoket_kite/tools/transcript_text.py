"""Read timestamped, speaker-labeled plain-text transcripts."""

from __future__ import annotations

import re
from pathlib import Path

from .contracts import ToolError

_TIMESTAMP = re.compile(r"([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)[ \t]*")


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
