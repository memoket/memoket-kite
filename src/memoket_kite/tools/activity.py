"""Project committed counters and workflow stages without opening source artifacts."""

from .contracts import decode_object

_PIPELINES = {
    "memory-ingestion": "ingest",
    "memory-provider-ingestion": "ingest",
    "memory-update": "update",
    "memory-deletion": "delete",
    "memory-consolidation": "merge",
    "context-search": "search",
    "source-revision-review": "compare",
}


def memory_activity(workspace) -> dict:
    db = workspace.db
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    events = []
    if "memory_activity" in tables:
        events.extend(
            dict(row)
            for row in db.execute(
                "SELECT version, kind, changed_count, source_count, committed_at "
                "FROM memory_activity ORDER BY version DESC LIMIT 50"
            )
        )
    if "deletion_audit" in tables:
        events.extend(
            {**dict(row), "kind": "delete"}
            for row in db.execute(
                "SELECT version, revision_count, fact_count, committed_at "
                "FROM deletion_audit ORDER BY version DESC LIMIT 50"
            )
        )
    runs = []
    if "runs" in tables:
        placeholders = ",".join("?" for _ in _PIPELINES)
        for row in db.execute(
            "SELECT checkpoint FROM runs "
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
        versions = [event["version"] for event in events]
        tracked = {
            row[0]
            for row in db.execute(
                f"SELECT version FROM memory_versions WHERE version IN ({','.join('?' for _ in versions)})",
                versions,
            )
        }
        for event in events:
            event["can_search_version"] = event["version"] in tracked
    return {
        "version": workspace.version(),
        "events": events,
        "runs": runs,
    }
