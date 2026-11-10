"""Read timed caption files into the canonical conversation segment format."""

from __future__ import annotations

import re

from .contracts import XML_UNSAFE, ToolError

CAPTION_FORMATS = ("srt", "vtt")
_SRT_TIME = r"[0-9]{2,9}:[0-5][0-9]:[0-5][0-9],[0-9]{3}"
_VTT_TIME = r"(?:[0-9]{2,9}:)?[0-5][0-9]:[0-5][0-9]\.[0-9]{3}"
_TIMING = {
    name: re.compile(rf"({time})[ \t]+-->[ \t]+({time})(?:[ \t]+.*)?")
    for name, time in zip(CAPTION_FORMATS, (_SRT_TIME, _VTT_TIME))
}
_VOICE = re.compile(r"<v(?:\.[^>\s]+)*[ \t]+([^>\r\n]+)>")


def _milliseconds(timestamp: str) -> int:
    clock, fraction = timestamp.replace(",", ".").split(".")
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total * 1000 + int(fraction)


def _speaker(text: str) -> str | None:
    voices = list(_VOICE.finditer(text))
    if len(voices) == 1:
        voice = voices[0]
        tail = text[voice.end() :].partition("</v>")[2]
        if not text[: voice.start()].strip() and not tail.strip():
            return voice[1].strip() or None
    return None


def caption_segments(raw: str, source_format: str) -> list[dict]:
    if XML_UNSAFE.search(raw):
        raise ToolError("invalid_input", "caption file contains characters XML cannot represent")
    text = raw.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n", text)
    if source_format == "vtt":
        header = blocks.pop(0).split("\n")
        if not re.fullmatch(r"WEBVTT(?:[ \t]+.*)?", header[0]) or "-->" in "\n".join(header):
            raise ToolError("invalid_input", "WebVTT requires a header separated from its cues")

    timing = _TIMING[source_format]
    segments = []
    for block in blocks:
        if not block.strip():
            continue
        first, *lines = block.strip("\n").split("\n")
        if source_format == "vtt" and re.match(r"NOTE(?:[ \t]|$)", first):
            continue
        if (
            source_format == "vtt"
            and first in {"STYLE", "REGION"}
            and (not lines or not timing.fullmatch(lines[0].strip()))
        ):
            continue

        cue_id = None
        if source_format == "srt" or "-->" not in first:
            cue_id = first.strip() if source_format == "srt" else first
            first = lines.pop(0) if lines else ""
        if source_format == "srt" and not re.fullmatch(r"[0-9]+", cue_id or ""):
            raise ToolError("invalid_input", "SRT cue requires a numeric identifier")
        match = timing.fullmatch(first.strip())
        text = "\n".join(lines)
        if (
            match is None
            or not text.strip()
            or any(timing.fullmatch(line.strip()) for line in lines)
        ):
            raise ToolError("invalid_input", "caption cue requires valid timing and text")
        start, end = map(_milliseconds, match.groups())
        if end <= start:
            raise ToolError("invalid_input", "caption end must follow its start")
        segment = {
            "segment_id": f"{source_format}-id-{cue_id}"
            if cue_id
            else f"{source_format}-cue-{len(segments) + 1}",
            "start_ms": start,
            "end_ms": end,
            "text": text,
        }
        if cue_id:
            segment["source_sentence_id"] = cue_id
        if source_format == "vtt" and (speaker := _speaker(text)):
            segment["speaker_id"] = speaker
        segments.append(segment)
    return segments
