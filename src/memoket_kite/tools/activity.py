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
    if "runs" in tables and event_version is None:
        placeholders = ",".join("?" for _ in _PIPELINES)
        for row in db.execute(
            "SELECT id, checkpoint FROM runs "
            f"WHERE json_extract(checkpoint, '$.pipeline_id') IN ({placeholders}) "
            "AND json_extract(checkpoint, '$.state') != 'invalidated' "
            "ORDER BY json_extract(checkpoint, '$.updated_at') DESC, rowid DESC LIMIT 25",
            tuple(_PIPELINES),
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
            row[0]
            for row in db.execute(
                f"SELECT version FROM memory_versions WHERE version IN ({','.join('?' for _ in versions)})",
                versions,
            )
        }
        for event in events:
            event["can_search_version"] = event["version"] in tracked
            event["can_search_previous"] = event["version"] - 1 in tracked
    if event_version is not None:
        if not events:
            raise ToolError("not_found", "memory change was not found")
        event = events[0]
        if event["kind"] in {"ingest", "update", "index"}:
            if "revision_visibility" in tables and event.get("can_search_previous"):
                event.update(_changed_sources(db, event_version, offset))
        elif event["kind"] == "merge" and {"runs", "artifacts"} <= tables:
            event["topic_merge"] = committed_topic_merge(workspace, event_version)
    return {
        "version": workspace.version(),
        "events": events,
        "runs": runs,
        "selected_event": event_version,
    }


def _changed_sources(db, version: int, offset: int) -> dict:
    total = db.execute(
        "SELECT count(*) FROM revision_visibility WHERE opened_version=?", (version,)
    ).fetchone()[0]
    if offset > total:
        raise ToolError("invalid_input", "offset is past the end of the changed sources")
    rows = db.execute(
        "SELECT r.conversation_id, r.source, r.checksum, "
        "json_extract(r.payload, '$.conversation.title') AS title, "
        "(SELECT prior.checksum FROM revisions prior "
        " JOIN revision_visibility p ON p.checksum=prior.checksum "
        " WHERE prior.source_key=r.source_key AND p.closed_version=v.opened_version) AS before "
        "FROM revision_visibility v JOIN revisions r ON r.checksum=v.checksum "
        "WHERE v.opened_version=? ORDER BY r.source, r.conversation_id LIMIT 20 OFFSET ?",
        (version, offset),
    ).fetchall()
    return {
        "source_changes": [
            {key: row[key] for key in ("conversation_id", "source", "checksum", "title", "before")}
            for row in rows
        ],
        "retained_source_changes": total,
        "source_offset": offset,
    }
