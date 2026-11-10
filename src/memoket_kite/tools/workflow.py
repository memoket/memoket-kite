"""Validate definitions and advance one explicitly chosen stage at a time."""

from __future__ import annotations

import json
import re
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from uuid import uuid4

from .contracts import ID, ToolError, digest, encode, now, schema_file, validate
from .ingest import minimize_source
from .operations import EXTERNAL, SCHEMAS, TOOLS, Context

SNAPSHOT_KINDS = {"resolved", "matches", "report", "deletion_plan", "integrity"}


def load_pipeline(root: Path, name: str) -> tuple[dict, str]:
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            mapping = super().construct_mapping(node, deep=deep)
            if len(mapping) != len(node.value):
                raise ToolError("invalid_pipeline", "duplicate YAML mapping keys")
            return mapping

    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ToolError("invalid_pipeline", "invalid pipeline name")
    directory = root / "pipeline_defs"
    path = (directory / f"{name}.yaml").resolve()
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
        skill = (root / stage["skill"]).resolve()
        if not skill.is_relative_to(root / "skills") or not skill.is_file():
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
    return definition, digest([definition, skills, SCHEMAS, schema_file(root, "conversation")])


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
    definition, fingerprint = load_pipeline(context.root, context.pipeline)
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


def status(workspace, run_id: str) -> dict:
    _, checkpoint = _run(workspace, run_id)
    return {**checkpoint, "snapshot_current": checkpoint["memory_version"] == workspace.version()}


def _check_snapshot(context, row, checkpoint):
    if checkpoint["state"] == "invalidated":
        raise ToolError("invalidated_run", "deletion invalidated this run")
    if checkpoint["memory_version"] != context.workspace.version():
        raise ToolError(
            "stale_snapshot", "memory changed; start a new run against the current snapshot"
        )
    if row["definition"] != checkpoint["definition_digest"]:
        raise ToolError("integrity_error", "checkpoint definition digest differs from its record")


def artifact(context: Context, run_id: str, stage_id: str) -> dict:
    run, checkpoint = _run(context.workspace, run_id)
    _check_snapshot(context, run, checkpoint)
    _, fingerprint = load_pipeline(context.root, checkpoint["pipeline_id"])
    if fingerprint != run["definition"]:
        raise ToolError("stale_definition", "pipeline or skill changed; start a new run")
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


def advance(
    context: Context, run_id: str, stage_id: str, extra: dict
) -> tuple[dict, ToolError | None]:
    row, checkpoint = _run(context.workspace, run_id)
    _check_snapshot(context, row, checkpoint)
    context.pipeline = checkpoint["pipeline_id"]
    definition, fingerprint = load_pipeline(context.root, context.pipeline)
    if fingerprint != row["definition"] or fingerprint != checkpoint["definition_digest"]:
        raise ToolError("stale_definition", "pipeline or skill changed; start a new run")
    stages = {stage["id"]: stage for stage in definition["stages"]}
    if stage_id not in stages:
        raise ToolError("not_found", "pipeline has no stage with that ID")
    stage = stages[stage_id]
    tool = TOOLS[stage["tool"]]
    if context.approval is not None and tool.effect != "delete":
        raise ToolError("invalid_input", "--approve is only valid for a deletion stage")
    state = checkpoint["stages"][stage_id]
    supplied = json.loads(row["inputs"])
    if checkpoint["input_digest"] != digest(supplied):
        raise ToolError("integrity_error", "run input differs from its digest")
    extra = _inputs(definition, extra)
    input_keys = {
        reference[6:] for reference in stage["inputs"].values() if reference.startswith("input.")
    }
    if not extra.keys() <= input_keys or any(
        key in supplied and supplied[key] != value for key, value in extra.items()
    ):
        raise ToolError("conflict", "stage inputs can only fill its own unset input values")
    if state["state"] == "completed":
        if extra.keys() - supplied.keys():
            raise ToolError("conflict", "cannot change a completed stage")
        return {"checkpoint": checkpoint, **_artifact(context, checkpoint, stage_id)}, None
    if state["state"] in ("invalidated", "redacted"):
        raise ToolError("invalidated_run", "this stage's artifacts were redacted after deletion")
    if any(checkpoint["stages"][dep]["state"] != "completed" for dep in stage["needs"]):
        raise ToolError("dependency_pending", "finish prerequisite stages first")

    pending_inputs = {**supplied, **extra}
    db = context.workspace.db
    db.execute("SAVEPOINT stage_effect")
    state["attempts"] += 1
    state["retry_count"] = state["attempts"] - 1
    checkpoint["current_stage"] = stage_id
    try:
        args = {}
        for argument, ref in stage["inputs"].items():
            scope, key = ref.split(".")
            if scope == "input":
                if key not in pending_inputs:
                    raise ToolError("missing_input", f"stage requires input {key}")
                args[argument] = pending_inputs[key]
            else:
                args[argument] = _artifact(context, checkpoint, key)["value"]
        value = tool.function(context, **args)
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
