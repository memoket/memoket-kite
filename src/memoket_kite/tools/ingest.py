"""Normalize authorized conversation exports without following links or reading files."""

from __future__ import annotations

import math

from .contracts import (
    HASH,
    TEXT,
    XML_UNSAFE,
    ToolError,
    digest,
    object_schema,
    schema_file,
    utc_timestamp,
    validate,
)

SOURCE = object_schema(
    {
        "format": {"enum": ["text", "memoket", "conversation", "transcript", "audio-transcript"]},
        "payload": {},
    },
    {"expected_checksum": HASH},
)


def _memoket(payload: dict) -> dict:
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


def minimize_source(source: dict) -> dict:
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


def normalize(context, source: dict) -> dict:
    source = minimize_source(source)
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
    validate_conversation(context.root, conversation)
    return {
        "conversation": conversation,
        "checksum": digest(conversation),
        "expected_checksum": source.get("expected_checksum"),
    }


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
