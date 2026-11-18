"""Run the Twin source indexing stages over a bounded group of captures."""

from concurrent.futures import ThreadPoolExecutor

from .contracts import COUNT, TEXT, ToolError, array, object_schema
from .ingest import SOURCE, normalize
from .twin_ingestion import CANDIDATES, OPTIONS, commit_twin_indexes, propose_twin_index

_MAX_ENTRIES = 12
BATCH = array(object_schema({"source": SOURCE, "options": OPTIONS}), 1, _MAX_ENTRIES)
NORMALIZED = array(
    object_schema(
        {
            "conversation": {"type": "object"},
            "checksum": TEXT,
            "expected_checksum": {"type": ["string", "null"]},
        }
    ),
    1,
    _MAX_ENTRIES,
)
_ITEM = object_schema({"position": COUNT, "candidate": CANDIDATES})
_FAILURE = object_schema({"position": COUNT, "code": TEXT, "message": TEXT})
ANNOTATED = object_schema(
    {"ready": array(_ITEM, maximum=_MAX_ENTRIES), "failed": array(_FAILURE, maximum=_MAX_ENTRIES)}
)


def normalize_twin_batch(context, batch):
    normalized = [normalize(context, item["source"]) for item in batch]
    references = [
        (item["conversation"]["source"], item["conversation"]["metadata"]["source_reference"])
        for item in normalized
    ]
    if len(set(references)) != len(references):
        raise ToolError("invalid_input", "A Twin batch can include each source only once")
    return normalized


def prepare_twin_batch(context, *, batch, normalized):
    if not context.allow_provider:
        raise ToolError("provider_disabled", "Explicit approval is required for CLI indexing")
    return {"batch": batch, "normalized": normalized, "vocab": context.workspace.vocabulary()}


def annotate_twin_batch(*, batch, normalized, vocab):
    def annotate(position):
        try:
            candidate = propose_twin_index(
                normalized=normalized[position],
                options=batch[position]["options"],
                vocab=vocab,
                page_executor=pages,
            )
        except ToolError as error:
            return "failed", {"position": position, "code": error.code, "message": str(error)}
        return "ready", {"position": position, "candidate": candidate}

    result = {"ready": [], "failed": []}
    with (
        ThreadPoolExecutor(max_workers=12) as pages,
        ThreadPoolExecutor(max_workers=min(len(batch), 6)) as pool,
    ):
        for status, row in pool.map(annotate, range(len(batch))):
            result[status].append(row)
    return result


def commit_twin_batch(context, batch, normalized, annotated):
    positions = [row["position"] for result in annotated.values() for row in result]
    if sorted(positions) != list(range(len(batch))):
        raise ToolError("integrity_error", "Twin batch results do not match the requested captures")
    entries = [
        (
            batch[row["position"]]["source"],
            row["candidate"],
            normalized[row["position"]],
            batch[row["position"]]["options"],
        )
        for row in annotated["ready"]
    ]
    if not entries:
        return {
            "changed": False,
            "memory_version": context.workspace.version(),
            "sources_changed": 0,
            "sources_seen": 0,
        }
    return commit_twin_indexes(context, entries)
