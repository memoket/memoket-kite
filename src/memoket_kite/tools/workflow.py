"""Validate definitions and advance one explicitly chosen stage at a time."""

from __future__ import annotations

import json
import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from uuid import uuid4

from .contracts import (
    HASH,
    ID,
    ToolError,
    decode_object,
    digest,
    encode,
    now,
    object_schema,
    schema_file,
    validate,
)
from .index import revision_id
from .ingest import prepare_source, source_identity
from .operations import EXTERNAL, SCHEMAS, TOOLS, Context

SNAPSHOT_KINDS = {"resolved", "matches", "report", "deletion_plan", "integrity"}
LOCAL_IMPORT_TOOLS = {"normalize", "annotate", "commit", "validate_commit"}
SCRIPTED_IMPORT_STAGES = {
    "memory-ingestion": (
        ("normalize", "normalize"),
        ("annotate", "annotate"),
        ("commit", "commit"),
        ("verify", "validate_commit"),
    ),
    "memory-update": (
        ("normalize", "normalize"),
        ("annotate", "annotate"),
        ("commit", "amend"),
        ("verify", "validate_commit"),
    ),
}
TRACE_PIPELINES = ("topic-tracing", "task-tracing", "timeline-reconstruction", "decision-evolution")
_REPLACEABLE_STAGE_INPUTS = {
    "annotate": ("annotations", {"invalid_evidence", "invalid_input", "integrity_error"}),
    "resolve": ("query", {"invalid_input", "stale_snapshot"}),
}


def query_report(context: Context, supplied: dict) -> dict:
    """Run the built-in trace tools without creating a checkpoint or artifacts."""
    validate(object_schema({"query": SCHEMAS["query"]}), supplied, "query input")
    result = supplied["query"]
    for name, argument in (("resolve", "query"), ("retrieve", "resolved"), ("report", "matches")):
        tool = TOOLS[name]
        result = tool.function(context, **{argument: result})
        validate(SCHEMAS[tool.output], result, "tool output")
    return result


def load_pipeline(
    root: Path, name: str, *, extension_root: Path | None = None
) -> tuple[dict, str, Path]:
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = super().construct_mapping(node, deep=deep)
            if len(mapping) != len(node.value):
                raise ToolError("invalid_pipeline", "duplicate YAML mapping keys")
            return mapping

    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ToolError("invalid_pipeline", "invalid pipeline name")
    workflow_root = root
    filename = f"{name}.yaml"
    if extension_root is not None:
        custom = extension_root / "pipeline_defs" / filename
        if custom.exists() or custom.is_symlink():
            builtin = root / "pipeline_defs" / filename
            if builtin.exists() or builtin.is_symlink():
                raise ToolError("invalid_pipeline", f"extension shadows bundled pipeline {name}")
            workflow_root = extension_root
    directory = workflow_root / "pipeline_defs"
    try:
        path = (directory / filename).resolve()
        if path.parent != directory.resolve():
            raise ToolError("invalid_pipeline", "definition must be under pipeline_defs/")
        if not path.is_file():
            raise ToolError("invalid_pipeline", "pipeline YAML must be a regular file")
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ToolError("invalid_pipeline", "definition is too large")
        definition = yaml.load(raw, Loader=UniqueLoader)
        validate(schema_file(root, "pipeline"), definition, "pipeline")
    except ToolError as exc:
        raise ToolError("invalid_pipeline", str(exc)) from exc
    except (OSError, ValueError, yaml.YAMLError, RuntimeError) as exc:
        raise ToolError("invalid_pipeline", "cannot read pipeline YAML or schema") from exc
    if definition["id"] != name:
        raise ToolError("invalid_pipeline", "pipeline ID must match its filename")
    inputs = definition["inputs"]
    if not set(inputs.values()) <= EXTERNAL:
        raise ToolError("invalid_pipeline", "pipeline declares an unknown or internal input type")
    stages = {stage["id"]: stage for stage in definition["stages"]}
    if len(stages) != len(definition["stages"]):
        raise ToolError("invalid_pipeline", "stage IDs must be unique")
    skills = {}
    for stage in stages.values():
        tool = TOOLS.get(stage["tool"])
        if (
            tool is None
            or tool.output != stage["output"]
            or tool.inputs.keys() != stage["inputs"].keys()
        ):
            raise ToolError("invalid_pipeline", f"stage {stage['id']} has an invalid tool contract")
        if not set(stage["needs"]) <= stages.keys():
            raise ToolError("invalid_pipeline", f"stage {stage['id']} has an unknown dependency")
        try:
            skill = (workflow_root / stage["skill"]).resolve()
            if not skill.is_relative_to(workflow_root / "skills") or not skill.is_file():
                raise ToolError(
                    "invalid_pipeline", f"stage {stage['id']} references a missing skill"
                )
            skills[stage["skill"]] = skill.read_text(encoding="utf-8")
        except (OSError, UnicodeError, RuntimeError) as exc:
            raise ToolError(
                "invalid_pipeline", f"stage {stage['id']} skill cannot be read"
            ) from exc
        for argument, reference in stage["inputs"].items():
            scope, key = reference.split(".")
            kind = (
                inputs.get(key)
                if scope == "input"
                else (stages[key]["output"] if key in stage["needs"] else None)
            )
            if kind != tool.inputs[argument]:
                raise ToolError(
                    "invalid_pipeline", f"stage {stage['id']} has an incompatible input reference"
                )
    try:
        list(
            TopologicalSorter(
                {name: stage["needs"] for name, stage in stages.items()}
            ).static_order()
        )
    except CycleError as exc:
        raise ToolError("invalid_pipeline", "stage dependency graph contains a cycle") from exc
    tool_versions = {
        name: TOOLS[name].version
        for name in {stage["tool"] for stage in stages.values()}
        if TOOLS[name].version != 1
    }
    kinds = set(inputs.values()) | {stage["output"] for stage in stages.values()}
    parts = [definition, skills, {kind: SCHEMAS[kind] for kind in kinds}]
    if "conversation" in kinds:
        parts.append(schema_file(root, "conversation"))
    if tool_versions:
        parts.append(tool_versions)
    return definition, digest(parts), workflow_root


def discover_pipelines(root: Path, *, extension_root: Path | None = None) -> dict:
    bundled = {path.stem for path in (root / "pipeline_defs").glob("*.yaml")}
    extensions = set()
    if extension_root is not None:
        extensions = {path.stem for path in (extension_root / "pipeline_defs").glob("*.yaml")}
    if not bundled and not extensions:
        raise ToolError("invalid_pipeline", "no definitions found; use --repo with a KITE checkout")

    pipelines, invalid_extensions = [], []
    for name in sorted(bundled | extensions):
        try:
            definition, _, _ = load_pipeline(root, name, extension_root=extension_root)
        except ToolError as error:
            if name not in extensions:
                raise
            invalid_extensions.append({"id": name, "code": error.code, "message": str(error)})
            continue
        pipelines.append({key: definition[key] for key in ("id", "version", "description")})
    result = {"pipelines": pipelines}
    if invalid_extensions:
        result["invalid_extensions"] = invalid_extensions
    return result


def _inputs(context: Context, definition: dict, supplied: dict) -> dict:
    if not isinstance(supplied, dict) or not supplied.keys() <= definition["inputs"].keys():
        raise ToolError("invalid_input", "use the pipeline's declared input names")
    safe = {}
    for key, value in supplied.items():
        kind = definition["inputs"][key]
        validate(SCHEMAS[kind], value, f"input {key}")
        if kind == "source":
            value, _ = prepare_source(context.root, value)
            context.workspace.require_source_allowed(*source_identity(value))
        safe[key] = value
    return safe


def _require_stage_sequence(definition: dict, expected: tuple[tuple[str, str], ...]) -> None:
    if tuple((stage["id"], stage["tool"]) for stage in definition["stages"]) != expected:
        raise ToolError("invalid_pipeline", "ingest requires the standard local ingestion stages")


def start(
    context: Context,
    supplied: dict,
    run_id: str | None = None,
    *,
    expected_stages: tuple[tuple[str, str], ...] | None = None,
    resume_existing: bool = False,
) -> dict:
    definition, fingerprint, _ = load_pipeline(
        context.root, context.pipeline, extension_root=context.extension_root
    )
    if expected_stages is not None:
        _require_stage_sequence(definition, expected_stages)
    supplied = _inputs(context, definition, supplied)
    run_id = run_id if run_id is not None else uuid4().hex
    validate(ID, run_id, "run ID")
    input_digest = digest(supplied)
    row = context.workspace.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is not None:
        if not resume_existing:
            raise ToolError("conflict", "run ID already exists; inspect or resume that run")
        checkpoint = _checkpoint(row)
        if checkpoint["pipeline_id"] != context.pipeline:
            raise ToolError("conflict", "run ID belongs to a different pipeline")
        _bound_definition(context, row, checkpoint)
        _stored_inputs(row, checkpoint)
        if checkpoint["input_digest"] != input_digest:
            raise ToolError("conflict", "run ID belongs to different inputs; choose a new run ID")
        return checkpoint
    timestamp = now()
    checkpoint = {
        "run_id": run_id,
        "pipeline_id": context.pipeline,
        "pipeline_version": definition["version"],
        "definition_digest": fingerprint,
        "input_digest": input_digest,
        "memory_version": context.workspace.version(),
        "state": "pending",
        "current_stage": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "stages": {
            stage["id"]: {"state": "pending", "attempts": 0, "retry_count": 0, "error": None}
            for stage in definition["stages"]
        },
    }
    validate(schema_file(context.root, "checkpoint"), checkpoint, "checkpoint")
    context.workspace.db.execute(
        "INSERT INTO runs VALUES(?, ?, ?, ?)",
        (run_id, fingerprint, encode(supplied), encode(checkpoint)),
    )
    return checkpoint


def _run(workspace, run_id: str):
    validate(ID, run_id, "run ID")
    row = workspace.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise ToolError("not_found", "run was not found")
    return row, _checkpoint(row)


def _checkpoint(row) -> dict:
    try:
        checkpoint = decode_object(row["checkpoint"])
    except ToolError as exc:
        raise ToolError("integrity_error", "run checkpoint is not a valid JSON object") from exc
    if checkpoint.get("run_id") != row["id"]:
        raise ToolError("integrity_error", "checkpoint ID differs from its record")
    return checkpoint


def recent_runs(
    context: Context,
    limit: int,
    *,
    pipeline: str | None = None,
    unfinished: bool = False,
    resumable: bool = False,
    before: str | None = None,
) -> dict:
    if not 1 <= limit <= 100:
        raise ToolError("invalid_input", "run limit must be between 1 and 100")
    workspace = context.workspace
    version = workspace.version()
    if before is None:
        rows = workspace.db.execute("SELECT id, checkpoint FROM runs ORDER BY rowid DESC")
    else:
        validate(ID, before, "run ID")
        position = workspace.db.execute("SELECT rowid FROM runs WHERE id=?", (before,)).fetchone()
        if position is None:
            raise ToolError("not_found", "page boundary run was not found")
        rows = workspace.db.execute(
            "SELECT id, checkpoint FROM runs WHERE rowid < ? ORDER BY rowid DESC", (position[0],)
        )
    fields = ("run_id", "pipeline_id", "state", "current_stage", "created_at", "updated_at")
    runs = []
    next_before = None
    for row in rows:
        checkpoint = _checkpoint(row)
        try:
            summary = {field: checkpoint[field] for field in fields}
            summary["snapshot_current"] = checkpoint["memory_version"] == version
        except KeyError as exc:
            raise ToolError("integrity_error", "run checkpoint is missing summary fields") from exc
        if pipeline is not None and summary["pipeline_id"] != pipeline:
            continue
        if (unfinished or resumable) and summary["state"] not in (
            "pending",
            "running",
            "failed",
            "awaiting_approval",
        ):
            continue
        if resumable:
            stored = workspace.db.execute(
                "SELECT definition, inputs FROM runs WHERE id=?", (row["id"],)
            ).fetchone()
            resume = _resume(context, stored, checkpoint)
            if resume["state"] != "available":
                continue
            summary["resume"] = resume
        if len(runs) == limit:
            next_before = runs[-1]["run_id"]
            break
        runs.append(summary)
    return {"runs": runs, "memory_version": version, "next_before": next_before}


def _stored_inputs(row, checkpoint: dict) -> dict:
    try:
        supplied = decode_object(row["inputs"])
    except ToolError as exc:
        raise ToolError("integrity_error", "run input is not a valid JSON object") from exc
    if checkpoint["input_digest"] != digest(supplied):
        raise ToolError("integrity_error", "run input differs from its digest")
    return supplied


def verify_run_storage(workspace, root: Path) -> dict[str, int]:
    """Verify persisted runs without requiring their current pipeline definitions."""
    tables = workspace.db.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('runs', 'artifacts')"
    ).fetchone()[0]
    if tables != 2:
        raise ToolError("integrity_error", "workspace is missing workflow tables")
    checkpoint_schema = schema_file(root, "checkpoint")
    artifact_schema = schema_file(root, "artifact")
    memory_version = workspace.version()
    runs = artifacts = 0
    for row in workspace.db.execute("SELECT * FROM runs"):
        checkpoint = _checkpoint(row)
        try:
            validate(checkpoint_schema, checkpoint, "stored checkpoint")
            for stage in checkpoint["stages"].values():
                if "artifact" in stage:
                    validate(artifact_schema, stage["artifact"], "stored artifact manifest")
        except ToolError as exc:
            raise ToolError("integrity_error", str(exc)) from exc
        current_stage = checkpoint["current_stage"]
        if current_stage is not None and current_stage not in checkpoint["stages"]:
            raise ToolError("integrity_error", "checkpoint names an unknown current stage")
        if checkpoint["memory_version"] > memory_version:
            raise ToolError("integrity_error", "checkpoint refers to a future memory snapshot")
        supplied = _stored_inputs(row, checkpoint)
        if checkpoint["state"] == "invalidated":
            if (
                supplied
                or row["definition"]
                or any(stage["state"] != "invalidated" for stage in checkpoint["stages"].values())
            ):
                raise ToolError("integrity_error", "invalidated run retains workflow data")
        elif row["definition"] != checkpoint["definition_digest"]:
            raise ToolError("integrity_error", "checkpoint definition differs from its record")

        expected = {}
        for stage_id, stage in checkpoint["stages"].items():
            manifest = stage.get("artifact")
            if (stage["state"] == "completed") != (manifest is not None):
                raise ToolError(
                    "integrity_error", "checkpoint stage and artifact manifest disagree"
                )
            if manifest is None:
                continue
            if (
                manifest["run_id"] != row["id"]
                or manifest["stage_id"] != stage_id
                or manifest["operation_id"] != digest([row["id"], stage_id])
                or manifest["memory_version"] > checkpoint["memory_version"]
            ):
                raise ToolError("integrity_error", "artifact manifest does not belong to its stage")
            expected[stage_id] = manifest
        for artifact_row in workspace.db.execute(
            "SELECT * FROM artifacts WHERE run_id=?", (row["id"],)
        ):
            manifest = expected.pop(artifact_row["stage"], None)
            if manifest is None:
                raise ToolError("integrity_error", "artifact is not referenced by its checkpoint")
            _verified_artifact_value(artifact_row, manifest)
            artifacts += 1
        if expected:
            raise ToolError("integrity_error", "checkpoint refers to a missing artifact")
        runs += 1
    return {"verified_runs": runs, "verified_artifacts": artifacts}


def _stage_input_names(stage: dict) -> set[str]:
    return {ref[6:] for ref in stage["inputs"].values() if ref.startswith("input.")}


def _dependency_pending(checkpoint: dict, stage: dict) -> bool:
    return any(checkpoint["stages"][dep]["state"] != "completed" for dep in stage["needs"])


def _replaceable_stage_input(definition: dict, checkpoint: dict, stage: dict) -> str | None:
    state = checkpoint["stages"][stage["id"]]
    rule = _REPLACEABLE_STAGE_INPUTS.get(stage["tool"])
    if rule is None or state["state"] != "failed":
        return None
    argument, errors = rule
    if state["error"] not in errors:
        return None
    reference = stage["inputs"][argument]
    if not reference.startswith("input."):
        return None
    name = reference[6:]
    if any(
        checkpoint["stages"][other["id"]]["state"] == "completed"
        and name in _stage_input_names(other)
        for other in definition["stages"]
    ):
        return None
    return name


def _resume(context: Context, row, checkpoint: dict) -> dict:
    try:
        definition, _ = _bound_definition(context, row, checkpoint)
        supplied = _stored_inputs(row, checkpoint)
    except ToolError as error:
        return {"state": "blocked", "reason": error.code, "stages": []}
    except (OSError, ValueError):
        return {"state": "blocked", "reason": "invalid_pipeline", "stages": []}

    if checkpoint["state"] == "completed":
        return {"state": "complete", "stages": []}
    if any(stage["error"] == "stale_preview" for stage in checkpoint["stages"].values()):
        return {"state": "blocked", "reason": "stale_preview", "stages": []}
    eligible = []
    blocked_reason = None
    flags = {"provider": ["--allow-provider"], "delete": ["--approve"]}
    for stage in definition["stages"]:
        stage_id = stage["id"]
        if checkpoint["stages"][stage_id]["state"] in {
            "completed",
            "invalidated",
            "redacted",
        } or _dependency_pending(checkpoint, stage):
            continue
        try:
            for reference in stage["inputs"].values():
                if reference.startswith("stage."):
                    prerequisite = _artifact(context, checkpoint, reference[6:])
                    if prerequisite["manifest"]["kind"] == "deletion_plan":
                        context.workspace.deletion_impact(
                            prerequisite["value"], run_id=checkpoint["run_id"]
                        )
        except ToolError as error:
            blocked_reason = blocked_reason or error.code
            continue
        missing = _stage_input_names(stage) - supplied.keys()
        details = {
            "id": stage_id,
            "tool": stage["tool"],
            "skill": stage["skill"],
            "missing_inputs": {key: definition["inputs"][key] for key in sorted(missing)},
            "required_flags": flags.get(TOOLS[stage["tool"]].effect, []),
        }
        replacement = _replaceable_stage_input(definition, checkpoint, stage)
        if replacement is not None and replacement in supplied:
            details["replaceable_inputs"] = {replacement: definition["inputs"][replacement]}
        eligible.append(details)
    if not eligible:
        return {"state": "blocked", "reason": blocked_reason or "dependency_pending", "stages": []}
    return {"state": "available", "stages": eligible}


def status(context: Context, run_id: str) -> dict:
    row, checkpoint = _run(context.workspace, run_id)
    return {
        **checkpoint,
        "snapshot_current": checkpoint["memory_version"] == context.workspace.version(),
        "resume": _resume(context, row, checkpoint),
    }


def _only_commit_verification_remains(definition: dict, checkpoint: dict) -> bool:
    stages = definition["stages"]
    pending = [
        stage for stage in stages if checkpoint["stages"][stage["id"]]["state"] != "completed"
    ]
    if len(pending) != 1 or pending[0]["tool"] != "validate_commit":
        return False
    receipt = pending[0]["inputs"]["receipt"]
    return any(
        receipt == f"stage.{stage['id']}" and stage["tool"] in {"commit", "amend"}
        for stage in stages
    )


def _bound_definition(context: Context, row, checkpoint: dict) -> tuple[dict, str]:
    if checkpoint["state"] == "invalidated":
        raise ToolError("invalidated_run", "deletion invalidated this run")
    if row["definition"] != checkpoint["definition_digest"]:
        raise ToolError("integrity_error", "checkpoint definition digest differs from its record")

    stale = checkpoint["memory_version"] != context.workspace.version()
    stale_error = ToolError(
        "stale_snapshot", "memory changed; start a new run against the current snapshot"
    )
    if stale:
        if checkpoint["state"] == "completed":
            raise stale_error
        # A stale run without a receipt cannot be waiting on commit verification.
        if checkpoint["pipeline_id"] != "memory-ingestion" and not any(
            stage.get("artifact", {}).get("kind") == "receipt"
            for stage in checkpoint["stages"].values()
        ):
            raise stale_error
    definition, fingerprint, _ = load_pipeline(
        context.root, checkpoint["pipeline_id"], extension_root=context.extension_root
    )
    if fingerprint != row["definition"]:
        raise ToolError("stale_definition", "pipeline, skill or tool changed; start a new run")
    if stale and not _only_commit_verification_remains(definition, checkpoint):
        # Local preparation for a new source is independent of intervening imports.
        # The commit still validates the current records in its write transaction.
        if checkpoint["pipeline_id"] != "memory-ingestion" or any(
            stage["tool"] not in LOCAL_IMPORT_TOOLS for stage in definition["stages"]
        ):
            raise stale_error
        source = _stored_inputs(row, checkpoint).get("source")
        if not isinstance(source, dict) or "expected_checksum" in source:
            raise stale_error
    return definition, fingerprint


def artifact(context: Context, run_id: str, stage_id: str) -> dict:
    run, checkpoint = _run(context.workspace, run_id)
    _bound_definition(context, run, checkpoint)
    return _artifact(context, checkpoint, stage_id)


def resume_ingestion(context: Context, run_id: str) -> dict:
    row, checkpoint = _run(context.workspace, run_id)
    expected_stages = SCRIPTED_IMPORT_STAGES.get(checkpoint["pipeline_id"])
    if expected_stages is None:
        raise ToolError(
            "invalid_pipeline", "ingest can only resume a local ingestion or update run"
        )
    definition, _ = _bound_definition(context, row, checkpoint)
    _require_stage_sequence(definition, expected_stages)
    if _stored_inputs(row, checkpoint).keys() != {"source", "annotations"}:
        raise ToolError(
            "missing_input", "supply the missing source or annotations with kite-tool stage"
        )
    return checkpoint


def _validate_segment_window(offset: int, limit: int, byte_budget: int) -> None:
    if offset < 0 or not 1 <= limit <= 200 or not 128 <= byte_budget <= 1024 * 1024:
        raise ToolError("invalid_input", "invalid transcript offset, limit or byte budget")


def _segment_page(
    conversation: dict, *, offset: int, limit: int, byte_budget: int, revision: str | None = None
) -> dict:
    segments = conversation["segments"]
    if offset > len(segments):
        raise ToolError("invalid_input", "offset is past the end of the transcript")

    page = []
    size = 2  # JSON array delimiters.
    for position, segment in enumerate(segments[offset : offset + limit], offset + 1):
        if revision is not None:
            segment = {**segment, "source_line_id": f"{revision}L{position}"}
        next_size = size + len(encode(segment)) + (1 if page else 0)
        if page and next_size > byte_budget:
            break
        page.append(segment)
        size = next_size
    end = offset + len(page)
    return {
        "conversation_id": conversation["conversation_id"],
        "source": conversation["source"],
        "total_segments": len(segments),
        "offset": offset,
        "next_offset": end if end < len(segments) else None,
        "segment_payload_bytes": size,
        "over_budget": size > byte_budget,
        "segments": page,
    }


def transcript_page(
    context: Context, run_id: str, stage_id: str, *, offset: int, limit: int, byte_budget: int
) -> dict:
    _validate_segment_window(offset, limit, byte_budget)
    result = artifact(context, run_id, stage_id)
    if result["manifest"]["kind"] != "conversation":
        raise ToolError("invalid_input", "select a normalized conversation artifact")
    page = _segment_page(
        result["value"]["conversation"], offset=offset, limit=limit, byte_budget=byte_budget
    )
    return {"manifest": result["manifest"], **page}


def source_lines(
    workspace,
    source: str,
    conversation_id: str,
    *,
    checksum: str | None,
    offset: int,
    limit: int,
    byte_budget: int,
) -> dict:
    _validate_segment_window(offset, limit, byte_budget)
    if checksum is not None:
        validate(HASH, checksum, "revision checksum")
    elif offset:
        raise ToolError("invalid_input", "pass the previous page's revision_checksum as --revision")
    record = workspace.source_revision(source, conversation_id, checksum)
    conversation = record["conversation"]
    revision = revision_id(record)
    page = _segment_page(
        conversation, offset=offset, limit=limit, byte_budget=byte_budget, revision=revision
    )
    return {
        **page,
        "source_reference": conversation["metadata"]["source_reference"],
        "revision_checksum": record["checksum"],
        "source_revision_active": record["active"],
        "memory_version": workspace.version(),
        **{
            key: conversation[key]
            for key in ("started_at", "source_date", "language", "title")
            if key in conversation
        },
    }


def _artifact(context: Context, checkpoint: dict, stage_id: str) -> dict:
    stage = checkpoint["stages"].get(stage_id, {})
    manifest = stage.get("artifact")
    if stage.get("state") != "completed" or not manifest:
        raise ToolError("not_found", "stage artifact is unavailable")
    row = context.workspace.db.execute(
        "SELECT * FROM artifacts WHERE run_id=? AND stage=?", (checkpoint["run_id"], stage_id)
    ).fetchone()
    if row is None:
        raise ToolError("integrity_error", "checkpoint refers to a missing artifact")
    value = _verified_artifact_value(row, manifest)
    if row["kind"] in SNAPSHOT_KINDS and manifest["memory_version"] != context.workspace.version():
        raise ToolError("stale_snapshot", "stage artifact was produced against an older snapshot")
    validate(SCHEMAS[row["kind"]], value, "stored artifact")
    return {"manifest": manifest, "value": value}


def _verified_artifact_value(row, manifest: dict) -> dict:
    try:
        value = decode_object(row["value"])
    except ToolError as exc:
        raise ToolError("integrity_error", "artifact contains invalid JSON") from exc
    if row["kind"] != manifest["kind"] or not row["digest"] == manifest["digest"] == digest(value):
        raise ToolError("integrity_error", "artifact differs from the checkpoint manifest")
    return value


def _save(context: Context, checkpoint: dict, supplied: dict, fingerprint: str) -> None:
    checkpoint["updated_at"] = now()
    validate(schema_file(context.root, "checkpoint"), checkpoint, "checkpoint")
    context.workspace.db.execute(
        "UPDATE runs SET checkpoint=?, inputs=?, definition=? WHERE id=?",
        (encode(checkpoint), encode(supplied), fingerprint, checkpoint["run_id"]),
    )


def _stage_args(context: Context, checkpoint: dict, stage: dict, pending_inputs: dict) -> dict:
    args = {}
    for argument, ref in stage["inputs"].items():
        scope, key = ref.split(".")
        if scope == "input":
            if key not in pending_inputs:
                raise ToolError("missing_input", f"stage requires input {key}")
            args[argument] = pending_inputs[key]
        else:
            args[argument] = _artifact(context, checkpoint, key)["value"]
    return args


def _provider_result(context, row, checkpoint, stage, tool, pending_inputs):
    run_id = checkpoint["run_id"]
    with context.workspace.provider_slot(run_id, stage["id"]):
        try:
            args = _stage_args(context, checkpoint, stage, pending_inputs)
        except ToolError as error:
            return None, error
        with context.workspace.unlocked_for_provider():
            try:
                value, error = tool.function(allow_provider=context.allow_provider, **args), None
            except ToolError as failure:
                value, error = None, failure

        # Check the current database and definition before attaching either a
        # result or a failure to a checkpoint. Never restore a changed run.
        current, current_checkpoint = _run(context.workspace, run_id)
        _bound_definition(context, current, current_checkpoint)
        if current != row:
            raise ToolError(
                "conflict", "run changed during extraction; retry from its current checkpoint"
            )
        return value, error


def advance(
    context: Context, run_id: str, stage_id: str, extra: dict
) -> tuple[dict, ToolError | None]:
    row, checkpoint = _run(context.workspace, run_id)
    definition, fingerprint = _bound_definition(context, row, checkpoint)
    context.pipeline = checkpoint["pipeline_id"]
    context.run_id = run_id
    stages = {stage["id"]: stage for stage in definition["stages"]}
    if stage_id not in stages:
        raise ToolError("not_found", "pipeline has no stage with that ID")
    stage = stages[stage_id]
    tool = TOOLS[stage["tool"]]
    if context.approval is not None and tool.effect != "delete":
        raise ToolError("invalid_input", "--approve is only valid for a deletion stage")
    state = checkpoint["stages"][stage_id]
    supplied = _stored_inputs(row, checkpoint)
    extra = _inputs(context, definition, extra)
    changes = {key for key, value in extra.items() if key in supplied and supplied[key] != value}
    if not extra.keys() <= _stage_input_names(stage) or (
        changes and changes != {_replaceable_stage_input(definition, checkpoint, stage)}
    ):
        raise ToolError(
            "conflict", "stage input can fill an unset value or replace a rejected stage input"
        )
    if state["state"] == "completed":
        if extra.keys() - supplied.keys():
            raise ToolError("conflict", "cannot change a completed stage")
        return {"checkpoint": checkpoint, **_artifact(context, checkpoint, stage_id)}, None
    if state["state"] in ("invalidated", "redacted"):
        raise ToolError("invalidated_run", "this stage's artifacts were redacted after deletion")
    if _dependency_pending(checkpoint, stage):
        raise ToolError("dependency_pending", "finish prerequisite stages first")

    pending_inputs = {**supplied, **extra}
    if tool.effect == "provider":
        candidate, provider_error = _provider_result(
            context,
            row,
            checkpoint,
            stage,
            tool,
            pending_inputs,
        )
    db = context.workspace.db
    db.execute("SAVEPOINT stage_effect")
    state["attempts"] += 1
    state["retry_count"] = state["attempts"] - 1
    checkpoint["current_stage"] = stage_id
    try:
        if tool.effect == "provider":
            if provider_error is not None:
                raise provider_error
            value = candidate
        else:
            value = tool.function(
                context, **_stage_args(context, checkpoint, stage, pending_inputs)
            )
        validate(SCHEMAS[tool.output], value, "tool output")
        if tool.effect == "delete" and value["changed"]:
            pending_inputs = {}
            for prior_id, prior_state in checkpoint["stages"].items():
                if prior_id != stage_id and prior_state["state"] == "completed":
                    prior_state.pop("artifact", None)
                    prior_state.update(state="redacted", error=None)
        version = context.workspace.version()
        manifest = {
            "run_id": run_id,
            "stage_id": stage_id,
            "kind": tool.output,
            "digest": digest(value),
            "memory_version": version,
            "operation_id": digest([run_id, stage_id]),
            "created_at": now(),
        }
        validate(schema_file(context.root, "artifact"), manifest, "artifact manifest")
        db.execute(
            "INSERT INTO artifacts VALUES(?, ?, ?, ?, ?)",
            (run_id, stage_id, tool.output, manifest["digest"], encode(value)),
        )
        state.update(state="completed", error=None, artifact=manifest)
        complete = all(
            item["state"] in {"completed", "redacted"} for item in checkpoint["stages"].values()
        )
        checkpoint.update(
            state="completed" if complete else "running",
            memory_version=version,
            input_digest=digest(pending_inputs),
        )
        _save(context, checkpoint, pending_inputs, fingerprint)
        db.execute("RELEASE stage_effect")
        return {"checkpoint": checkpoint, "manifest": manifest, "value": value}, None
    except ToolError as error:
        db.execute("ROLLBACK TO stage_effect")
        db.execute("RELEASE stage_effect")
        # Use the pre-stage checkpoint even if the error occurred after a tool
        # prepared database changes or redacted its previous outputs.
        checkpoint = json.loads(row["checkpoint"])
        stage_state = checkpoint["stages"][stage_id]
        attempts = stage_state["attempts"] + 1
        outcome = "awaiting_approval" if error.code == "awaiting_approval" else "failed"
        stage_state.update(
            state=outcome, error=error.code, attempts=attempts, retry_count=attempts - 1
        )
        checkpoint.update(state=outcome, current_stage=stage_id)
        _save(context, checkpoint, supplied, fingerprint)
        return {"checkpoint": checkpoint}, error
