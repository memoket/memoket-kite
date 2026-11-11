"""Prepare and normalize authorized conversation sources."""

from __future__ import annotations

import math
import re
from hashlib import sha256
from pathlib import Path

from .captions import CAPTION_FORMATS, caption_segments
from .contracts import (
    HASH,
    TEXT,
    XML_UNSAFE,
    ToolError,
    decode_object,
    digest,
    object_schema,
    read_utf8,
    schema_file,
    utc_timestamp,
    validate,
)
from .memoket_transcript import api_transcript
from .transcript_text import transcript_text_source

SOURCE = object_schema(
    {
        "format": {"enum": ["text", "memoket", "conversation", "transcript", "audio-transcript"]},
        "payload": {},
    },
    {"expected_checksum": HASH},
)


def _memoket(payload: dict) -> dict:
    if not {"id", "data", "segments"} & payload.keys() and {"items", "pages"} & payload.keys():
        return api_transcript(payload)
    if "data" in payload:
        if type(payload.get("code")) is not int or payload["code"] != 0:
            raise ToolError("invalid_input", "Memoket export must contain a successful response")
        payload = payload["data"]
    if not isinstance(payload, dict):
        raise ToolError("invalid_input", "Memoket response data must be an object")
    identity = payload.get("id")
    if isinstance(identity, (str, int)) and not isinstance(identity, bool):
        identity = str(identity)
    segments = {}
    for group in payload.get("segments") or []:
        for line in group.get("sentences") or []:
            line_id = line.get("line_id")
            key = line_id if line_id and line_id != "0" else line.get("id")
            if not isinstance(key, str) or not key.strip():
                raise ToolError("invalid_input", "Memoket sentence requires a line or sentence ID")
            segment = {"segment_id": key, "text": line["text"]}
            if isinstance(line.get("id"), str) and line["id"]:
                segment["source_sentence_id"] = line["id"]
            if line.get("is_self") is not None:
                segment["is_self"] = line["is_self"]
            if line.get("speaker"):
                segment["speaker_id"] = line["speaker"]
            if line.get("time") is not None:
                seconds = line["time"]
                if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
                    raise ToolError("invalid_input", "invalid Memoket sentence time")
                segment["start_ms"] = round(seconds * 1000)
            if key in segments and segments[key] != segment:
                raise ToolError("invalid_input", "conflicting Memoket transcript lines")
            segments[key] = segment
    # Some Memoket exports repeat the same lines across segment levels.
    result = {
        "conversation_id": identity,
        "source": "memoket",
        "segments": sorted(
            segments.values(), key=lambda line: ("start_ms" not in line, line.get("start_ms", 0))
        ),
        "metadata": {"source_reference": identity},
    }
    for key, field in (
        ("name", "title"),
        ("date", "source_date"),
        ("started_at", "started_at"),
        ("language_code", "language"),
    ):
        if payload.get(key):
            result[field] = payload[key]
    for key in ("transcript_version", "speaker_version"):
        if payload.get(key) is not None:
            result["metadata"][key] = payload[key]
    return result


def _minimize_source(source: dict) -> dict:
    if source["format"] != "memoket":
        return source
    if not isinstance(source["payload"], dict):
        raise ToolError("invalid_input", "source payload must be an object")
    try:
        conversation = _memoket(source["payload"])
    except (AttributeError, KeyError, TypeError, OverflowError) as exc:
        raise ToolError("invalid_input", "malformed Memoket export") from exc
    # Discard URLs, response metadata and generated summaries before checkpointing.
    return {**source, "format": "conversation", "payload": conversation}


def source_identity(source: dict) -> tuple:
    payload = source["payload"]
    if not isinstance(payload, dict):
        return None, None, None
    name = payload.get("source") if source["format"] == "conversation" else source["format"]
    metadata = payload.get("metadata")
    reference = metadata.get("source_reference") if isinstance(metadata, dict) else None
    return name, payload.get("conversation_id"), reference


def normalize(context, source: dict) -> dict:
    _, conversation = prepare_source(context.root, source)
    return {
        "conversation": conversation,
        "checksum": digest(conversation),
        "expected_checksum": source.get("expected_checksum"),
    }


def prepare_source(root: Path, source: dict) -> tuple[dict, dict]:
    """Validate a source and return its checkpoint input and canonical conversation."""
    source = _minimize_source(source)
    fmt, payload = source["format"], source["payload"]
    if not isinstance(payload, dict):
        raise ToolError("invalid_input", "source payload must be an object")
    if fmt == "text":
        validate(TEXT, payload.get("text"), "text")
        conversation = {key: val for key, val in payload.items() if key != "text"}
        conversation.update(
            source="text", segments=[{"segment_id": "text-1", "text": payload["text"]}]
        )
    elif fmt == "conversation":
        conversation = payload
    else:
        conversation = {**payload, "source": fmt}
    validate_conversation(root, conversation)
    return source, conversation


def _read_source_file(path: Path) -> str:
    path = path.expanduser()
    if not path.is_file():
        raise ToolError("invalid_input", "source must be a local file")
    return read_utf8(path)


def file_source(
    root: Path,
    path: Path,
    source_format: str,
    *,
    source_id: str | None,
    source_date: str | None,
    started_at: str | None,
    expected_checksum: str | None,
    split_lines: bool = False,
    pages: list[Path] | None = None,
    recording_metadata: Path | None = None,
    project: str | None = None,
) -> dict:
    """Return a minimized source input without opening a memory workspace."""
    file_formats = ("text", *CAPTION_FORMATS)
    if split_lines and source_format != "text":
        raise ToolError("invalid_input", "--split-lines requires --format text")
    if (pages or recording_metadata is not None) and source_format != "memoket":
        raise ToolError("invalid_input", "--page and --recording-metadata require --format memoket")
    if source_format in file_formats:
        validate(TEXT, source_id, "file source ID")
    elif source_format != "transcript-text" and any(
        value is not None for value in (source_id, source_date, started_at)
    ):
        raise ToolError("invalid_input", "JSON exports supply their own identity and dates")
    if expected_checksum is not None:
        validate(HASH, expected_checksum, "expected checksum")
    if project is not None:
        validate(TEXT, project, "project")
    raw = _read_source_file(path)
    if source_format == "transcript-text":
        if source_id is not None:
            validate(TEXT, source_id, "file source ID")
        prepared = transcript_text_source(path, raw, source_id=source_id)
        source_format, payload = prepared["format"], prepared["payload"]
        if source_date is not None:
            payload["source_date"] = source_date
        if started_at is not None:
            payload["started_at"] = started_at
    elif source_format in file_formats:
        payload = {
            "conversation_id": source_id,
            "metadata": {"source_reference": source_id},
        }
        if source_date is not None:
            payload["source_date"] = source_date
        if started_at is not None:
            payload["started_at"] = started_at
        if source_format in CAPTION_FORMATS:
            payload["metadata"]["source_sha256"] = sha256(raw.encode("utf-8")).hexdigest()
            payload["source"] = source_format
            payload["segments"] = caption_segments(raw, source_format)
            source_format = "conversation"
        elif split_lines:
            if XML_UNSAFE.search(raw):
                raise ToolError("invalid_input", "source contains characters XML cannot represent")
            payload["metadata"]["source_sha256"] = sha256(raw.encode("utf-8")).hexdigest()
            payload["source"] = "text"
            payload["segments"] = [
                {"segment_id": f"text-line-{number}", "text": line}
                for number, line in enumerate(re.split(r"\r\n|\r|\n", raw), 1)
                if line.strip()
            ]
            source_format = "conversation"
        else:
            payload["text"] = raw
    else:
        payload = decode_object(raw)
        if pages:
            payload = {
                "pages": [payload, *(decode_object(_read_source_file(page)) for page in pages)]
            }
        if recording_metadata is not None:
            if "pages" not in payload:
                payload = {"pages": [payload]}
            if "recording" in payload:
                raise ToolError("invalid_input", "supply Memoket recording metadata only once")
            payload["recording"] = decode_object(_read_source_file(recording_metadata))
    _, conversation = prepare_source(root, {"format": source_format, "payload": payload})
    if project is not None:
        conversation["metadata"]["project"] = project
    source = {"format": "conversation", "payload": conversation}
    if expected_checksum is not None:
        source["expected_checksum"] = expected_checksum
    return {"source": source}


def validate_conversation(root, conversation: dict) -> None:
    validate(schema_file(root, "conversation"), conversation, "conversation")
    segment_ids = set()
    for segment in conversation["segments"]:
        sid = segment["segment_id"]
        if sid in segment_ids or segment.get("end_ms", float("inf")) < segment.get("start_ms", 0):
            raise ToolError("invalid_input", "duplicate segment ID or invalid segment offsets")
        segment_ids.add(sid)
        if any(XML_UNSAFE.search(segment.get(key, "")) for key in ("text", "speaker_id")):
            raise ToolError("invalid_input", "segment contains characters XML cannot represent")
    if XML_UNSAFE.search(conversation.get("title", "")):
        raise ToolError("invalid_input", "title contains characters XML cannot represent")


def source_day(conversation: dict) -> str | None:
    if stamp := conversation.get("started_at"):
        return utc_timestamp(stamp).date().isoformat()
    return conversation.get("source_date")
