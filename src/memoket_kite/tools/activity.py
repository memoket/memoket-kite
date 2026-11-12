"""Project committed changes and workflow stages for the memory viewer."""

from .contracts import ToolError, decode_object
from .workflow import committed_topic_merge

_PIPELINES = {
    "memory-ingestion": "ingest",
    "memory-provider-ingestion": "ingest",
    "memory-update": "update",
    "memory-deletion": "delete",
    "memory-consolidation": "merge",
    "context-search": "search",
    "source-revision-review": "compare",
}


def memory_activity(workspace, *, event_version: int | None = None, offset: int = 0) -> dict:
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
        for row in db.execute(
            "SELECT id, checkpoint FROM runs "
            f"WHERE json_extract(checkpoint, '$.pipeline_id') IN ({placeholders}) "
            "AND json_extract(checkpoint, '$.state') != 'invalidated' "
            f"{commit_scope}"
            "ORDER BY json_extract(checkpoint, '$.updated_at') DESC, rowid DESC LIMIT 25",
            args,
        ):
            record = decode_object(row["checkpoint"])
            runs.append(
                {
                    "kind": _PIPELINES[record["pipeline_id"]],
                    "state": record["state"],
                    "version": record["memory_version"],
                    "updated_at": record["updated_at"],
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
            )
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
        recorded = (
            "memory_activity_sources" in tables
            and db.execute(
                "SELECT 1 FROM memory_activity_sources WHERE version=? LIMIT 1", (event_version,)
            ).fetchone()
            is not None
        )
        if event["kind"] in {"ingest", "update", "index"}:
            if "revision_visibility" in tables and (recorded or event.get("can_search_previous")):
                event.update(_changed_sources(db, event, offset, recorded=recorded))
        elif event["kind"] == "merge" and {"runs", "artifacts"} <= tables:
            event["topic_merge"] = committed_topic_merge(workspace, event_version)
            if recorded and event["topic_merge"] is not None:
                event.update(_changed_sources(db, event, offset, recorded=True))
    return {
        "version": workspace.version(),
        "events": events,
        "runs": runs,
        "selected_event": event_version,
    }


def _changed_sources(db, event: dict, offset: int, *, recorded: bool) -> dict:
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
    rows = db.execute(
        "SELECT r.conversation_id, r.source, r.checksum, "
        "json_extract(r.payload, '$.conversation.title') AS title, "
        "json_extract(r.payload, '$.conversation.source_date') AS source_date, "
        "json_extract(r.payload, '$.conversation.started_at') AS started_at, "
        "json_extract(r.payload, '$.conversation.local_started_at') AS local_started_at, "
        "a.segment_offset, v.opened_version, "
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
