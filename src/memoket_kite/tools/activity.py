"""Project committed changes and workflow stages for the memory viewer."""

from .contracts import ID, ToolError, decode_object, validate
from .index import revision_id
from .workflow import _artifact, _run, committed_topic_merge

_PIPELINES = {
    "source-indexing": "index",
    "memory-ingestion": "ingest",
    "memory-provider-ingestion": "ingest",
    "memory-update": "update",
    "memory-deletion": "delete",
    "memory-consolidation": "merge",
    "topic-reclassification": "reclassify",
    "context-search": "search",
    "source-revision-review": "compare",
}
_BATCH_STAGE_FIELDS = {
    "preview": ("sources_seen", "source_lines", "index_spans", "dated_sources"),
    "commit": ("sources_changed", "sources_seen", "elapsed_time_repairs"),
    "verify": ("sources", "source_lines", "search_spans"),
}
FOLLOW_STAGES = {
    "source-indexing": ("preview", "commit", "verify"),
    "memory-ingestion": ("normalize", "annotate", "commit", "verify"),
    "memory-update": ("normalize", "annotate", "commit", "verify"),
    "memory-consolidation": ("commit", "verify"),
    "memory-deletion": ("preview", "apply"),
    "context-search": ("compile", "search"),
}


def workflow_progress(workspace, run_id: str) -> dict:
    """Read one workflow's durable progress without exposing its stored inputs."""
    _, checkpoint = _run(workspace, run_id)
    pipeline = checkpoint["pipeline_id"]
    if pipeline not in FOLLOW_STAGES:
        raise ToolError("invalid_input", "this workflow does not support live viewing")
    if checkpoint["state"] == "invalidated":
        raise ToolError("invalidated_run", "the workflow was invalidated by deletion")

    progress = {
        "run_id": run_id,
        "pipeline": pipeline,
        "state": checkpoint["state"],
        "stages": [],
    }
    for name in FOLLOW_STAGES[pipeline]:
        state = checkpoint["stages"][name]
        stage = {"name": name, "state": state["state"], "attempts": state["attempts"]}
        progress["stages"].append(stage)
        if state["error"]:
            stage["error"] = state["error"]
        if state["state"] != "completed":
            continue
        manifest = state["artifact"]
        stage["version"] = manifest["memory_version"]
        stage["completed_at"] = manifest["created_at"]
        if pipeline in {"source-indexing", "memory-deletion"} or name in {
            "compile",
            "search",
            "commit",
        }:
            value = _artifact(workspace, checkpoint, name)["value"]
        if pipeline == "source-indexing":
            stage["counts"] = {key: value[key] for key in _BATCH_STAGE_FIELDS[name] if key in value}
        if pipeline == "memory-deletion" and name == "preview":
            stage["counts"] = {
                key: value[key]
                for key in ("revisions", "facts", "runs_to_invalidate", "run_artifacts_to_purge")
            }
        if name == "compile":
            stage["terms"] = value["terms"]
            if value.get("comparison_terms"):
                stage["comparison_terms"] = value["comparison_terms"]
            if value.get("output_targets"):
                stage["output_targets"] = value["output_targets"]
        if name == "search":
            parts = value.get("parts", [value])
            stage["counts"] = {
                "candidate_spans": sum(part["candidate_spans"] for part in parts),
                "cited_spans": sum(len(part["spans"]) for part in parts),
                "quote_bytes": value["quote_bytes"],
            }
            stage["citations"] = [
                {
                    "reference": f"{line['conversation_id']}#{line['segment_id']}",
                    "line_id": line["source_line_id"],
                    "checksum": line["revision_checksum"],
                }
                for part in parts
                for span in part["spans"][:3]
                for line in span["evidence"][:3]
            ]
            progress["saved_search"] = run_id
        if name in {"commit", "apply"}:
            stage["changed"] = value["changed"]
            if value["changed"]:
                progress["commit_version"] = value["memory_version"]
    if "commit_version" in progress:
        version = progress["commit_version"]
        if pipeline == "memory-deletion":
            event = memory_activity(workspace, event_version=version)["events"][0]
            progress["change"] = {
                "kind": "delete",
                "deleted_graph_counts": event.get(
                    "deleted_graph_counts", {"revisions": event["revision_count"]}
                ),
            }
        else:
            progress["change"] = _workflow_change(workspace, version)
    return progress


def _workflow_change(workspace, version):
    db = workspace.db
    event = db.execute(
        "SELECT kind, version FROM memory_activity WHERE version=?", (version,)
    ).fetchone()
    if event is None:
        return {}
    topic_graph = db.execute(
        "SELECT 1 FROM memory_activity_graphs WHERE version=? AND checksum=''", (version,)
    ).fetchone()
    change = _changed_sources(db, dict(event), 0, recorded=True, graphs=True)
    if event["kind"] == "merge":
        return {
            **change,
            "topic_graph": topic_graph is not None,
            "topic_merge": committed_topic_merge(workspace, version),
        }
    paths = []
    for source in change["source_changes"][:3]:
        graph = db.execute(
            "SELECT after FROM memory_activity_graphs WHERE version=? AND checksum=?",
            (version, source["checksum"]),
        ).fetchone()
        passages = decode_object(graph["after"])["passages"] if graph else []
        record = workspace.record(source["checksum"])
        segments = record["conversation"]["segments"]
        position = source.get("segment_offset") or (
            passages[0]["offsets"][0] - 1 if passages else 0
        )
        paths.append(
            {
                "checksum": source["checksum"],
                "label": source["title"] or source["conversation_id"],
                "topics": sorted({topic for passage in passages for topic in passage["topics"]}),
                "lines": [
                    {
                        "line_id": f"{revision_id(record)}L{offset + 1}",
                        "reference": f"{source['conversation_id']}#{segment['segment_id']}",
                    }
                    for offset, segment in enumerate(segments[position : position + 2], position)
                ],
            }
        )
    return {**change, "topic_graph": topic_graph is not None, "paths": paths}


def memory_activity(
    workspace, *, event_version: int | None = None, offset: int = 0, run_id: str | None = None
) -> dict:
    if run_id is not None:
        validate(ID, run_id, "run ID")
    db = workspace.db
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    events = []
    scope = "WHERE version=?" if event_version is not None else ""
    args = (event_version,) if event_version is not None else ()
    if "memory_activity" in tables:
        events.extend(
            dict(row)
            for row in db.execute(
                "SELECT version, kind, changed_count, source_count, committed_at "
                f"FROM memory_activity {scope} ORDER BY version DESC LIMIT 50",
                args,
            )
        )
    if "deletion_audit" in tables:
        events.extend(
            {**dict(row), "kind": "delete"}
            for row in db.execute(
                "SELECT version, revision_count, fact_count, committed_at "
                f"FROM deletion_audit {scope} ORDER BY version DESC LIMIT 50",
                args,
            )
        )
    runs = []
    pipelines = tuple(
        pipeline
        for pipeline, kind in _PIPELINES.items()
        if event_version is None or (events and kind == events[0]["kind"])
    )
    if "runs" in tables and pipelines and (event_version is None or "artifacts" in tables):
        placeholders = ",".join("?" for _ in pipelines)
        commit_scope = ""
        args = pipelines
        if event_version is not None:
            stage = "apply" if events[0]["kind"] == "delete" else "commit"
            commit_scope = (
                f"AND json_extract(checkpoint, '$.stages.{stage}.artifact.memory_version')=? "
                "AND EXISTS (SELECT 1 FROM artifacts WHERE run_id=runs.id AND stage=? "
                "AND json_extract(value, '$.changed')=1) "
            )
            args += (event_version, stage)
        if run_id is not None:
            commit_scope += "AND id=? "
            args += (run_id,)
        for row in db.execute(
            "SELECT id, checkpoint FROM runs "
            f"WHERE json_extract(checkpoint, '$.pipeline_id') IN ({placeholders}) "
            "AND json_extract(checkpoint, '$.state') != 'invalidated' "
            f"{commit_scope}"
            "ORDER BY json_extract(checkpoint, '$.updated_at') DESC, rowid DESC LIMIT 25",
            args,
        ):
            record = decode_object(row["checkpoint"])
            run = {
                "kind": _PIPELINES[record["pipeline_id"]],
                "state": record["state"],
                "version": record["memory_version"],
                "updated_at": record["updated_at"],
                **({"follow_run": row["id"]} if record["pipeline_id"] in FOLLOW_STAGES else {}),
                **(
                    {"saved_search": row["id"]}
                    if record["pipeline_id"] == "context-search"
                    and record["state"] == "completed"
                    and record["stages"].get("search", {}).get("artifact", {}).get("kind")
                    == "context_evidence"
                    else {}
                ),
                "stages": [
                    {"name": name, "state": stage["state"], "attempts": stage["attempts"]}
                    for name, stage in record["stages"].items()
                ],
            }
            if record["pipeline_id"] == "source-indexing" and "artifacts" in tables:
                run["run_id"] = row["id"]
                for stage in run["stages"]:
                    name = stage["name"]
                    if error := record["stages"][name].get("error"):
                        stage["error"] = error
                    if stage["state"] != "completed":
                        continue
                    value = _artifact(workspace, record, name)["value"]
                    stage["counts"] = {
                        key: value[key] for key in _BATCH_STAGE_FIELDS[name] if key in value
                    }
                    if name == "commit" and value["changed"]:
                        run["commit_version"] = value["memory_version"]
            runs.append(run)
    if run_id is not None and not runs:
        raise ToolError("not_found", "memory workflow run was not found")
    # Normalized checkpoints are serialized with sorted keys, so recover the built-in stage order.
    stage_order = {
        name: i
        for i, name in enumerate(
            (
                "normalize",
                "extract",
                "annotate",
                "preview",
                "commit",
                "apply",
                "verify",
                "compile",
                "search",
            )
        )
    }
    for run in runs:
        run["stages"].sort(key=lambda stage: stage_order.get(stage["name"], len(stage_order)))
    events = sorted(events, key=lambda event: event["version"], reverse=True)[:50]
    if events and "memory_versions" in tables:
        versions = sorted({event["version"] - offset for event in events for offset in (0, 1)})
        tracked = {
            row["version"]: row["committed_at"]
            for row in db.execute(
                "SELECT version, committed_at FROM memory_versions "
                f"WHERE version IN ({','.join('?' for _ in versions)})",
                versions,
            )
        }
        for event in events:
            event["committed_at"] = tracked.get(event["version"], event["committed_at"])
            event["can_search_version"] = event["version"] in tracked
            event["can_search_previous"] = event["version"] - 1 in tracked
    if event_version is not None:
        if not events:
            raise ToolError("not_found", "memory change was not found")
        event = events[0]
        graphs = "memory_activity_graphs" in tables
        if graphs:
            event["topic_graph"] = (
                db.execute(
                    "SELECT 1 FROM memory_activity_graphs WHERE version=? AND checksum=''",
                    (event_version,),
                ).fetchone()
                is not None
            )
        recorded = (
            "memory_activity_sources" in tables
            and db.execute(
                "SELECT 1 FROM memory_activity_sources WHERE version=? LIMIT 1", (event_version,)
            ).fetchone()
            is not None
        )
        if event["kind"] in {"ingest", "update", "index"}:
            if "revision_visibility" in tables and (recorded or event.get("can_search_previous")):
                event.update(_changed_sources(db, event, offset, recorded=recorded, graphs=graphs))
        elif event["kind"] == "merge" and {"runs", "artifacts"} <= tables:
            event["topic_merge"] = committed_topic_merge(workspace, event_version)
            if recorded and event["topic_merge"] is not None:
                event.update(_changed_sources(db, event, offset, recorded=True, graphs=graphs))
        elif event["kind"] == "delete" and "memory_deletion_graph" in tables:
            counts = db.execute(
                "SELECT memories, citations, memberships FROM memory_deletion_graph WHERE version=?",
                (event_version,),
            ).fetchone()
            if counts is not None:
                event["deleted_graph_counts"] = {
                    "revisions": event["revision_count"],
                    **dict(counts),
                }
    return {
        "version": workspace.version(),
        "events": events,
        "runs": runs,
        "selected_event": event_version,
        **({"selected_run": run_id} if run_id is not None else {}),
    }


def _changed_sources(db, event: dict, offset: int, *, recorded: bool, graphs: bool) -> dict:
    version = event["version"]
    affected = (
        "SELECT checksum, version, segment_offset FROM memory_activity_sources"
        if recorded
        else "SELECT checksum, opened_version AS version, NULL AS segment_offset FROM revision_visibility"
    )
    total = db.execute(f"SELECT count(*) FROM ({affected}) WHERE version=?", (version,)).fetchone()[
        0
    ]
    if offset > total:
        raise ToolError("invalid_input", "offset is past the end of the changed sources")
    graph = (
        "EXISTS (SELECT 1 FROM memory_activity_graphs g WHERE g.version=a.version AND g.checksum=a.checksum)"
        if graphs
        else "0"
    )
    rows = db.execute(
        "SELECT r.conversation_id, r.source, r.checksum, "
        "json_extract(r.payload, '$.conversation.title') AS title, "
        "json_extract(r.payload, '$.conversation.source_date') AS source_date, "
        "json_extract(r.payload, '$.conversation.started_at') AS started_at, "
        "json_extract(r.payload, '$.conversation.local_started_at') AS local_started_at, "
        "a.segment_offset, v.opened_version, "
        f"{graph} AS has_graph, "
        "CASE WHEN v.opened_version=a.version THEN (SELECT prior.checksum FROM revisions prior "
        " JOIN revision_visibility p ON p.checksum=prior.checksum "
        " WHERE prior.source_key=r.source_key AND p.closed_version=a.version) END AS before "
        f"FROM ({affected}) a JOIN revisions r ON r.checksum=a.checksum "
        "JOIN revision_visibility v ON v.checksum=a.checksum "
        "WHERE a.version=? ORDER BY r.source, r.conversation_id LIMIT 20 OFFSET ?",
        (version, offset),
    ).fetchall()
    changes = []
    for row in rows:
        if event["kind"] == "merge":
            effect = "retagged"
        elif row["opened_version"] < version:
            effect = "annotated"
        else:
            effect = "revised" if row["before"] else "added"
        changes.append(
            {
                **{
                    key: row[key]
                    for key in ("conversation_id", "source", "checksum", "title", "before")
                },
                "effect": effect,
                **({"has_graph": True} if row["has_graph"] else {}),
                **{
                    key: row[key]
                    for key in ("source_date", "started_at", "local_started_at")
                    if row[key] is not None
                },
                **(
                    {"segment_offset": row["segment_offset"]}
                    if row["segment_offset"] is not None
                    else {}
                ),
            }
        )
    return {
        "source_changes": changes,
        "retained_source_changes": total,
        "source_offset": offset,
    }
