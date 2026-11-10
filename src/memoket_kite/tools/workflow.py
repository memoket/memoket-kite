"""Validate definitions and advance one explicitly chosen stage at a time."""

from __future__ import annotations

import json
import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from uuid import uuid4

from .contracts import ID, ToolError, decode_object, digest, encode, now, schema_file, validate
from .ingest import minimize_source
from .operations import EXTERNAL, SCHEMAS, TOOLS, Context

SNAPSHOT_KINDS = {"resolved", "matches", "report", "deletion_plan", "integrity"}


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
    path = (directory / filename).resolve()
    if path.parent != directory.resolve():
        raise ToolError("invalid_pipeline", "definition must be under pipeline_defs/")
    try:
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ToolError("invalid_pipeline", "definition is too large")
        definition = yaml.load(raw, Loader=UniqueLoader)
        validate(schema_file(root, "pipeline"), definition, "pipeline")
    except ToolError as exc:
        raise ToolError("invalid_pipeline", str(exc)) from exc
    except (OSError, yaml.YAMLError, RecursionError) as exc:
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
        skill = (workflow_root / stage["skill"]).resolve()
        if not skill.is_relative_to(workflow_root / "skills") or not skill.is_file():
            raise ToolError("invalid_pipeline", f"stage {stage['id']} references a missing skill")
        skills[stage["skill"]] = skill.read_text(encoding="utf-8")
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
    fingerprint = digest([definition, skills, SCHEMAS, schema_file(root, "conversation")])
    return definition, fingerprint, workflow_root


def _inputs(definition: dict, supplied: dict) -> dict:
    if not isinstance(supplied, dict) or not supplied.keys() <= definition["inputs"].keys():
        raise ToolError("invalid_input", "use the pipeline's declared input names")
    safe = {}
    for key, value in supplied.items():
        kind = definition["inputs"][key]
        validate(SCHEMAS[kind], value, f"input {key}")
        safe[key] = minimize_source(value) if kind == "source" else value
    return safe


def start(context: Context, supplied: dict, run_id: str | None = None) -> dict:
    definition, fingerprint, _ = load_pipeline(
        context.root, context.pipeline, extension_root=context.extension_root
    )
    supplied = _inputs(definition, supplied)
    run_id = run_id if run_id is not None else uuid4().hex
    validate(ID, run_id, "run ID")
    if context.workspace.db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
        raise ToolError("conflict", "run ID already exists; inspect or resume that run")
    timestamp = now()
    checkpoint = {
        "run_id": run_id,
        "pipeline_id": context.pipeline,
        "pipeline_version": definition["version"],
        "definition_digest": fingerprint,
        "input_digest": digest(supplied),
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
    checkpoint = json.loads(row["checkpoint"])
    return row, checkpoint


def recent_runs(workspace, limit: int) -> dict:
    if not 1 <= limit <= 100:
        raise ToolError("invalid_input", "run limit must be between 1 and 100")
    version = workspace.version()
    rows = workspace.db.execute(
        "SELECT checkpoint FROM runs ORDER BY rowid DESC LIMIT ?",
        (limit,),
    )
    fields = ("run_id", "pipeline_id", "state", "current_stage", "created_at", "updated_at")
    runs = []
    for row in rows:
        checkpoint = json.loads(row["checkpoint"])
        runs.append(
            {
                **{field: checkpoint[field] for field in fields},
                "snapshot_current": checkpoint["memory_version"] == version,
            }
        )
    return {"runs": runs, "memory_version": version}


def _stored_inputs(row, checkpoint: dict) -> dict:
    try:
        supplied = decode_object(row["inputs"])
    except ToolError as exc:
        raise ToolError("integrity_error", "run input is not a valid JSON object") from exc
    if checkpoint["input_digest"] != digest(supplied):
        raise ToolError("integrity_error", "run input differs from its digest")
    return supplied


def _stage_input_names(stage: dict) -> set[str]:
    return {ref[6:] for ref in stage["inputs"].values() if ref.startswith("input.")}


def _dependency_pending(checkpoint: dict, stage: dict) -> bool:
    return any(checkpoint["stages"][dep]["state"] != "completed" for dep in stage["needs"])


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
    eligible = []
    flags = {"provider": ["--allow-provider"], "delete": ["--approve"]}
    for stage in definition["stages"]:
        stage_id = stage["id"]
        if checkpoint["stages"][stage_id]["state"] in {
            "completed",
            "invalidated",
            "redacted",
        } or _dependency_pending(checkpoint, stage):
            continue
        missing = _stage_input_names(stage) - supplied.keys()
        eligible.append(
            {
                "id": stage_id,
                "tool": stage["tool"],
                "skill": stage["skill"],
                "missing_inputs": {key: definition["inputs"][key] for key in sorted(missing)},
                "required_flags": flags.get(TOOLS[stage["tool"]].effect, []),
            }
        )
    if not eligible:
        return {"state": "blocked", "reason": "dependency_pending", "stages": []}
    return {"state": "available", "stages": eligible}


def status(context: Context, run_id: str) -> dict:
    row, checkpoint = _run(context.workspace, run_id)
    return {
        **checkpoint,
        "snapshot_current": checkpoint["memory_version"] == context.workspace.version(),
        "resume": _resume(context, row, checkpoint),
    }


def _check_snapshot(context, row, checkpoint):
    if checkpoint["state"] == "invalidated":
        raise ToolError("invalidated_run", "deletion invalidated this run")
    if checkpoint["memory_version"] != context.workspace.version():
        raise ToolError(
            "stale_snapshot", "memory changed; start a new run against the current snapshot"
        )
    if row["definition"] != checkpoint["definition_digest"]:
        raise ToolError("integrity_error", "checkpoint definition digest differs from its record")


def _bound_definition(context: Context, row, checkpoint: dict) -> tuple[dict, str]:
    _check_snapshot(context, row, checkpoint)
    definition, fingerprint, _ = load_pipeline(
        context.root, checkpoint["pipeline_id"], extension_root=context.extension_root
    )
    if fingerprint != row["definition"]:
        raise ToolError("stale_definition", "pipeline or skill changed; start a new run")
    return definition, fingerprint


def artifact(context: Context, run_id: str, stage_id: str) -> dict:
    run, checkpoint = _run(context.workspace, run_id)
    _bound_definition(context, run, checkpoint)
    return _artifact(context, checkpoint, stage_id)


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
    value = json.loads(row["value"])
    if row["kind"] != manifest["kind"] or not row["digest"] == manifest["digest"] == digest(value):
        raise ToolError("integrity_error", "artifact differs from the checkpoint manifest")
    if row["kind"] in SNAPSHOT_KINDS and manifest["memory_version"] != context.workspace.version():
        raise ToolError("stale_snapshot", "stage artifact was produced against an older snapshot")
    validate(SCHEMAS[row["kind"]], value, "stored artifact")
    return {"manifest": manifest, "value": value}


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
    stages = {stage["id"]: stage for stage in definition["stages"]}
    if stage_id not in stages:
        raise ToolError("not_found", "pipeline has no stage with that ID")
    stage = stages[stage_id]
    tool = TOOLS[stage["tool"]]
    if context.approval is not None and tool.effect != "delete":
        raise ToolError("invalid_input", "--approve is only valid for a deletion stage")
    state = checkpoint["stages"][stage_id]
    supplied = _stored_inputs(row, checkpoint)
    extra = _inputs(definition, extra)
    if not extra.keys() <= _stage_input_names(stage) or any(
        key in supplied and supplied[key] != value for key, value in extra.items()
    ):
        raise ToolError("conflict", "stage inputs can only fill its own unset input values")
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
