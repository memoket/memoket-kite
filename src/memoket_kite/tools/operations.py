"""Tool registry and JSON contracts consumed by pipeline validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .context import CONTEXT_PLAN, CONTEXT_REQUEST, compile_question, search_question
from .contracts import COUNT, CURSOR, DATE, HASH, TEXT, ToolError, array, object_schema
from .extract import MODEL, propose
from .index import ANNOTATIONS, RECLASSIFY, TOPIC_MOVE, annotate, move_topic, reclassify
from .ingest import SOURCE, normalize
from .retrieve import QUERY, report, resolve, retrieve
from .workspace import Workspace

SOURCE_SCOPE = object_schema(
    {"source": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,63}$"}}, {"conversation_id": TEXT}
)
TARGET = {
    **SOURCE_SCOPE,
    "properties": {
        **SOURCE_SCOPE["properties"],
        "conversation_ids": {**array(TEXT, 1, 100), "uniqueItems": True},
    },
    "not": {"required": ["conversation_id", "conversation_ids"]},
}
BOOL = {"type": "boolean"}
JSON_OBJECT = {"type": "object"}
RELATED_LABELS = array(
    object_schema({"code": TEXT, "fact_count": COUNT, "fact_ids": array(TEXT, 1, 3)}), maximum=10
)
EVIDENCE_BUDGET = object_schema({"limit_bytes": COUNT, "payload_bytes": COUNT, "over_budget": BOOL})
RECEIPT = object_schema(
    {"changed": BOOL, "memory_version": COUNT},
    {"revision_id": TEXT, "checksum": HASH, "deleted_revisions": COUNT, "operation_id": HASH},
)
SCHEMAS = {
    "source": SOURCE,
    "conversation": object_schema(
        {
            "conversation": JSON_OBJECT,
            "checksum": HASH,
            "expected_checksum": {"anyOf": [HASH, {"type": "null"}]},
        }
    ),
    "annotations": ANNOTATIONS,
    "annotated": object_schema(
        {
            "conversation": JSON_OBJECT,
            "checksum": HASH,
            "expected_checksum": {"anyOf": [HASH, {"type": "null"}]},
            "facts": array(JSON_OBJECT),
            "taxonomy": array(JSON_OBJECT),
            "entity_aliases": array(JSON_OBJECT),
        },
        {"index_spans": array(JSON_OBJECT)},
    ),
    "query": QUERY,
    "resolved": object_schema(
        {
            "query": QUERY,
            "topic_codes": array(TEXT),
            "unresolved_topics": array(TEXT),
            "event_codes": array(TEXT),
            "unresolved_events": array(TEXT),
            "entity_codes": array(TEXT),
            "unresolved_entities": array(TEXT),
        }
    ),
    "matches": object_schema(
        {
            "fact_ids": array(TEXT),
            "matched": COUNT,
            "next_cursor": {"anyOf": [CURSOR, {"type": "null"}]},
            "time_axis": {"enum": ["source", "event"]},
            "omitted_unknown_time": COUNT,
            "omitted_inferred_time": COUNT,
            "unresolved_topics": array(TEXT),
            "topic_codes": array(TEXT),
            "event_codes": array(TEXT),
            "unresolved_events": array(TEXT),
            "entity_codes": array(TEXT),
            "unresolved_entities": array(TEXT),
        },
        {
            "end_date": DATE,
            "evidence_budget": EVIDENCE_BUDGET,
            "followed_state_keys": array(TEXT),
            "uncertain_time_state_keys": array(TEXT),
            "conversation_scope": object_schema({"source": TEXT, "conversation_id": TEXT}),
        },
    ),
    "report": object_schema(
        {
            "trace": TEXT,
            "time_axis": {"enum": ["source", "event"]},
            "timeline": array(JSON_OBJECT),
            "confirmed_decisions": array(TEXT),
            "proposals": array(TEXT),
            "rejected_options": array(TEXT),
            "open_questions": array(TEXT),
            "current_reported_states": JSON_OBJECT,
            "matched_count": COUNT,
            "next_cursor": {"anyOf": [CURSOR, {"type": "null"}]},
            "truncated": BOOL,
            "omitted_unknown_time": COUNT,
            "omitted_inferred_time": COUNT,
            "resolved_topics": array(TEXT),
            "unresolved_topics": array(TEXT),
            "related_topics": RELATED_LABELS,
            "related_entities": RELATED_LABELS,
            "resolved_events": array(TEXT),
            "unresolved_events": array(TEXT),
            "resolved_entities": array(TEXT),
            "unresolved_entities": array(TEXT),
            "memory_version": COUNT,
        },
        {
            "end_date": DATE,
            "evidence_budget": EVIDENCE_BUDGET,
            "followed_state_keys": array(TEXT),
            "uncertain_time_state_keys": array(TEXT),
        },
    ),
    "receipt": RECEIPT,
    "integrity": object_schema(
        {
            "memory_version": COUNT,
            "revisions": COUNT,
            "facts": COUNT,
            "source_lines": COUNT,
            "codebook_sha256": HASH,
        }
    ),
    "deletion_target": TARGET,
    "deletion_plan": object_schema(
        {
            "target": TARGET,
            "approval_token": HASH,
            "memory_version": COUNT,
            "revisions": COUNT,
            "facts": COUNT,
            "staged_conversation_ids": {"type": "array", "items": TEXT},
            "runs_to_invalidate": COUNT,
            "run_artifacts_to_purge": COUNT,
        },
        {"conversation_ids_without_revisions": array(TEXT, maximum=100)},
    ),
    "reclassify": RECLASSIFY,
    "topic_move": TOPIC_MOVE,
    "model": MODEL,
    "context_request": CONTEXT_REQUEST,
    "context_plan": CONTEXT_PLAN,
    "context_evidence": JSON_OBJECT,
}
# The CLI may accept only these external inputs. Intermediates are always
# generated by a registered tool and read back from an artifact.
EXTERNAL = frozenset(
    {
        "source",
        "annotations",
        "query",
        "deletion_target",
        "reclassify",
        "topic_move",
        "model",
        "context_request",
    }
)


@dataclass
class Context:
    root: Path
    workspace: Workspace
    pipeline: str
    approval: str | None = None
    allow_provider: bool = False
    extension_root: Path | None = None
    run_id: str = ""


@dataclass(frozen=True)
class Tool:
    function: Callable
    inputs: dict[str, str]
    output: str
    effect: str = "read"
    version: int = 1


def _commit(context: Context, annotated: dict) -> dict:
    return context.workspace.commit(annotated)


def _amend(context: Context, annotated: dict) -> dict:
    return context.workspace.commit(annotated, append_annotations=True)


def _validate(context: Context, receipt: dict | None = None) -> dict:
    from hashlib import sha256

    index = context.workspace.index(verify=True)
    if receipt and receipt.get("revision_id") and receipt["revision_id"] not in index.revisions:
        raise ToolError("integrity_error", "committed revision is missing")
    return {
        "memory_version": context.workspace.version(),
        "revisions": len(index.revisions),
        "facts": len(index.facts),
        "source_lines": len(index.segments),
        "codebook_sha256": sha256(index.xml).hexdigest(),
    }


def _preview(context: Context, target: dict) -> dict:
    return context.workspace.deletion_plan(target, run_id=context.run_id)[0]


def _delete(context: Context, plan: dict) -> dict:
    return context.workspace.delete(plan, context.approval, run_id=context.run_id)


def _rebuild(context: Context) -> dict:
    return context.workspace.replace_index(verify_sources=True)


def _compile_context(context: Context, request: dict) -> dict:
    return compile_question(context.workspace, request)


def _search_context(context: Context, compiled: dict) -> dict:
    return search_question(context.workspace, compiled)


TOOLS = {
    "normalize": Tool(normalize, {"source": "source"}, "conversation"),
    "extract": Tool(
        propose,
        {"normalized": "conversation", "model": "model"},
        "annotations",
        "provider",
        version=2,
    ),
    "annotate": Tool(
        annotate,
        {"normalized": "conversation", "annotations": "annotations"},
        "annotated",
        version=2,
    ),
    "commit": Tool(_commit, {"annotated": "annotated"}, "receipt", "write", version=2),
    "amend": Tool(_amend, {"annotated": "annotated"}, "receipt", "write", version=2),
    "validate": Tool(_validate, {}, "integrity"),
    "rebuild": Tool(_rebuild, {}, "receipt", "write"),
    "validate_commit": Tool(_validate, {"receipt": "receipt"}, "integrity"),
    "resolve": Tool(resolve, {"query": "query"}, "resolved", version=4),
    "retrieve": Tool(retrieve, {"resolved": "resolved"}, "matches", version=11),
    "report": Tool(report, {"matches": "matches"}, "report", version=12),
    "reclassify": Tool(reclassify, {"merge": "reclassify"}, "receipt", "write", version=2),
    "move_topic": Tool(move_topic, {"move": "topic_move"}, "receipt", "write"),
    "preview_delete": Tool(_preview, {"target": "deletion_target"}, "deletion_plan", version=4),
    "delete": Tool(_delete, {"plan": "deletion_plan"}, "receipt", "delete", version=4),
    "compile_context": Tool(_compile_context, {"request": "context_request"}, "context_plan"),
    "search_context": Tool(_search_context, {"compiled": "context_plan"}, "context_evidence"),
}
