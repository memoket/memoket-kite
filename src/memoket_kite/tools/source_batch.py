"""Prepare and commit source-only batches for CLI and staged workflows."""

from __future__ import annotations

from itertools import zip_longest
from pathlib import Path
from typing import TYPE_CHECKING

from .context import _spans, context_current, context_enabled
from .contracts import (
    COUNT,
    HASH,
    TEXT,
    ToolError,
    array,
    decode_object,
    digest,
    object_schema,
    read_utf8,
    validate,
)
from .index import ANNOTATIONS, annotate
from .ingest import RECORDING_CLOCK_FIELDS, SOURCE, normalize, source_day
from .transcript_text import transcript_recording_time, transcript_text_reimport

if TYPE_CHECKING:
    from .operations import Context

BATCH_INPUT = object_schema(
    {
        "sources": array(
            object_schema({"source": SOURCE}, {"annotations": ANNOTATIONS}), maximum=1000
        )
    }
)
BATCH_SELECTION = {
    **object_schema({}, {"batch_file": TEXT, "transcripts": TEXT, "recording_metadata": TEXT}),
    "oneOf": [{"required": ["batch_file"]}, {"required": ["transcripts"]}],
    "dependentRequired": {"recording_metadata": ["transcripts"]},
}
BATCH_PREVIEW = object_schema(
    {
        "batch_sha256": HASH,
        "sources_seen": COUNT,
        "source_lines": COUNT,
        "index_spans": COUNT,
        "dated_sources": COUNT,
    }
)
BATCH_RECEIPT = object_schema(
    {
        "changed": {"type": "boolean"},
        "memory_version": COUNT,
        "sources_changed": COUNT,
        "sources_seen": COUNT,
    },
    {"elapsed_time_repairs": COUNT},
)
BATCH_VERIFIED = object_schema(
    {"memory_version": COUNT, "sources": COUNT, "source_lines": COUNT, "search_spans": COUNT}
)


def read_transcript_batch(directory: Path, metadata: Path | None) -> tuple[dict, dict]:
    files = sorted(directory.expanduser().glob("*.txt"))
    if (
        not files
        or len(files) > 1000
        or any(path.is_symlink() or not path.is_file() for path in files)
    ):
        raise ToolError("invalid_input", "provide a directory of 1–1000 regular TXT transcripts")
    if metadata is not None:
        metadata = metadata.expanduser()
        if not metadata.is_dir():
            raise ToolError(
                "invalid_input", "recording metadata needs a directory and --transcripts"
            )
    entries, legacy_transcripts = [], {}
    for path in files:
        source, legacy = transcript_text_reimport(path, read_utf8(path))
        if metadata is not None:
            conversation = source["payload"]
            conversation.update(
                transcript_recording_time(metadata, conversation["conversation_id"])
            )
        entries.append({"source": source})
        if legacy is not None:
            legacy_transcripts[path.name] = legacy
    return {"sources": entries}, legacy_transcripts


def _preserve_transcript_revision(
    context: Context, normalized: dict, prior: dict, legacy: list[dict] | None
) -> tuple[dict, dict, bool] | None:
    conversation = normalized["conversation"]
    previous = prior["conversation"]
    clock_fields = {"source_date", *RECORDING_CLOCK_FIELDS}
    repair = legacy is not None and legacy == previous["segments"] and not prior["facts"]
    if (
        legacy is not None
        and (repair or conversation["segments"] == previous["segments"])
        and not clock_fields.intersection(conversation)
    ):
        conversation = {
            **conversation,
            **{key: previous[key] for key in clock_fields if key in previous},
        }
        if not repair and conversation == previous:
            return (
                {**normalized, "conversation": conversation, "checksum": prior["checksum"]},
                {"facts": []},
                False,
            )
    missing = set(conversation) - previous.keys()
    comparable = {key: value for key, value in conversation.items() if key not in missing}
    if repair:
        comparable["segments"] = legacy
    if (not missing and not repair) or not missing <= clock_fields or comparable != previous:
        return None
    annotations = {
        key: prior[key]
        for key in ("facts", "taxonomy", "entity_aliases", "index_spans")
        if key in prior
    }
    if repair and "index_spans" in annotations:
        retained = {line["segment_id"] for line in conversation["segments"]}
        annotations["index_spans"] = [
            {**span, "segment_ids": ids}
            for span in annotations["index_spans"]
            if (ids := [segment for segment in span["segment_ids"] if segment in retained])
        ]
    source = {
        "format": "conversation",
        "payload": conversation,
        "expected_checksum": prior["checksum"],
    }
    return normalize(context, source), annotations, repair


def prepare_batch(
    context: Context,
    value: dict,
    *,
    backfill_recording_time: bool = False,
    legacy_transcripts: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], int]:
    validate(BATCH_INPUT, value, "source batch")
    records = []
    elapsed_time_repairs = 0
    for entry in value["sources"]:
        normalized = normalize(context, entry["source"])
        annotations = entry.get("annotations", {"facts": []})
        conversation = normalized["conversation"]
        legacy = (legacy_transcripts or {}).get(conversation["metadata"]["source_reference"])
        if legacy is not None or (backfill_recording_time and "source_date" in conversation):
            previous = context.workspace.records(
                source=conversation["source"],
                conversation_id=conversation["conversation_id"],
                active_only=True,
            )
            if previous and (
                preserved := _preserve_transcript_revision(context, normalized, previous[0], legacy)
            ):
                normalized, annotations, repaired = preserved
                elapsed_time_repairs += repaired
        if annotations["facts"]:
            raise ToolError("invalid_input", "index-sources accepts original spans without facts")
        records.append(annotate(context, normalized, annotations))
    return records, elapsed_time_repairs


def commit_batch(context: Context, records: list[dict], elapsed_time_repairs: int) -> dict:
    receipt = context.workspace.index_sources(records)
    if elapsed_time_repairs:
        receipt["elapsed_time_repairs"] = elapsed_time_repairs
    return receipt


def _selected_batch(context: Context, selection: dict) -> tuple[list[dict], int]:
    paths = {name: Path(value).expanduser() for name, value in selection.items()}
    if any(not path.is_absolute() for path in paths.values()):
        raise ToolError("invalid_input", "source-indexing needs absolute paths to selected files")
    metadata = paths.get("recording_metadata")
    try:
        if "batch_file" in paths:
            payload, legacy = decode_object(read_utf8(paths["batch_file"])), {}
        else:
            payload, legacy = read_transcript_batch(paths["transcripts"], metadata)
    except OSError as exc:
        raise ToolError("invalid_input", "selected batch inputs are not readable") from exc
    return prepare_batch(
        context, payload, backfill_recording_time=metadata is not None, legacy_transcripts=legacy
    )


def preview_source_batch(context: Context, selection: dict) -> dict:
    records, repairs = _selected_batch(context, selection)
    return {
        "batch_sha256": digest([records, repairs]),
        "sources_seen": len(records),
        "source_lines": sum(len(row["conversation"]["segments"]) for row in records),
        "index_spans": sum(len(row.get("index_spans", ())) for row in records),
        "dated_sources": sum(source_day(row["conversation"]) is not None for row in records),
    }


def commit_source_batch(context: Context, selection: dict, preview: dict) -> dict:
    records, repairs = _selected_batch(context, selection)
    if digest([records, repairs]) != preview["batch_sha256"]:
        raise ToolError("stale_source", "selected sources changed after preview; start a new run")
    return commit_batch(context, records, repairs)


def verify_source_batch(context: Context, receipt: dict) -> dict:
    workspace = context.workspace
    version = workspace.version()
    db = workspace.db
    if version != receipt["memory_version"]:
        raise ToolError("stale_snapshot", "memory changed after the batch commit")
    if not context_enabled(db) or not context_current(db, version):
        raise ToolError("integrity_error", "the committed workspace has no current source index")
    index = workspace.index(verify=True)
    sources = sorted(
        (record for record in index.revisions.values() if record["active"]),
        key=lambda record: record["checksum"],
    )
    expected = (
        (record["checksum"], start, stop)
        for record in sources
        for start, stop in _spans(record["conversation"]["segments"])
    )
    indexed = db.execute("SELECT checksum, start, stop FROM context_spans ORDER BY checksum, start")
    count = 0
    for row, span in zip_longest(indexed, expected):
        if row is None or tuple(row) != span:
            raise ToolError(
                "integrity_error", "the source index does not cover committed originals"
            )
        count += 1
    return {
        "memory_version": version,
        "sources": len(sources),
        "source_lines": sum(len(record["conversation"]["segments"]) for record in sources),
        "search_spans": count,
    }
