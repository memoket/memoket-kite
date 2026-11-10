"""Exact posting-list retrieval and evidence-backed trace reports."""

from __future__ import annotations

from collections import defaultdict

from .contracts import DATE, TEXT, ToolError, array, object_schema, utc_timestamp
from .ingest import source_day

QUERY = object_schema(
    {},
    {
        "topics": array(TEXT, 1, 32),
        "entities": array(TEXT, 1, 32),
        "speakers": array(TEXT, 1, 32),
        "projects": array(TEXT, 1, 32),
        "text": TEXT,
        "start_date": DATE,
        "end_date": DATE,
        "time_axis": {"enum": ["source", "event"]},
        "source": TEXT,
        "state_key": TEXT,
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
)


def resolve(context, query: dict) -> dict:
    if query.get("start_date", "") > query.get("end_date", "9999-12-31"):
        raise ToolError("invalid_input", "start_date must be on or before end_date")
    if context.pipeline == "topic-tracing" and not query.get("topics"):
        raise ToolError("invalid_input", "topic-tracing requires one or more topic names")
    if context.pipeline in ("task-tracing", "decision-evolution") and not (
        set(query) & {"topics", "entities", "text", "state_key"}
    ):
        raise ToolError(
            "invalid_input", "a task or decision trace requires a topic, entity, text or state key"
        )
    index = context.workspace.index()
    codes, unknown = set(), []
    for topic in query.get("topics", []):
        code = index.vocab.resolve_topic(topic)
        if code is None:
            unknown.append(topic)
            continue
        codes.update(
            index.vocab.downset(
                code,
                max_depth=len(index.vocab.topics),
                max_width=len(index.vocab.topics),
            )
        )
    return {"query": query, "topic_codes": sorted(codes), "unresolved_topics": unknown}


def _source_time(record: dict) -> tuple[str, str | None, str]:
    conversation = record["conversation"]
    day = source_day(conversation)
    timestamp = conversation.get("started_at")
    if timestamp:
        return day, timestamp, utc_timestamp(timestamp).isoformat()
    return day or "", day, day or ""


def retrieve(context, resolved: dict) -> dict:
    index = context.workspace.index()
    query = resolved["query"]
    candidates = set(index.facts)
    if "topics" in query:
        candidates &= set().union(
            *(index.store.by_topic.get(code, set()) for code in resolved["topic_codes"])
        )
    if "entities" in query:
        entity_codes = {index.vocab.resolve_entity(name) for name in query["entities"]}
        candidates &= set().union(
            *(index.store.by_entity.get(code, set()) for code in entity_codes)
        )
    if "speakers" in query:
        speakers = {speaker.casefold() for speaker in query["speakers"]}
        candidates = {
            fid
            for fid in candidates
            if any(
                index.segments[sid].get("speaker_id", "").casefold() in speakers
                for sid in index.store.facts[fid].src
            )
        }
    rows = []
    missing_time = 0
    time_axis = query.get("time_axis", "source")
    bounded = "start_date" in query or "end_date" in query
    for fid in candidates:
        fact = index.store.facts[fid]
        record = index.revisions[fact.unit]
        conversation = record["conversation"]
        if query.get("source", conversation["source"]) != conversation["source"]:
            continue
        if "projects" in query and conversation["metadata"].get("project") not in query["projects"]:
            continue
        if "state_key" in query and index.facts[fid].get("state_key") != query["state_key"]:
            continue
        if "text" in query and not any(
            query["text"].casefold() in text.casefold()
            for text in [fact.text, *(index.segments[sid]["text"] for sid in fact.src)]
        ):
            continue
        day, _, sortable = _source_time(record)
        if time_axis == "event":
            day = sortable = index.facts[fid].get("event_date", "")
        if not day:
            missing_time += 1
        if bounded:
            if not day or not query.get("start_date", "") <= day <= query.get(
                "end_date", "9999-12-31"
            ):
                continue
        offsets = [index.segments[sid].get("start_ms", -1) for sid in fact.src]
        rows.append((not bool(day), day, sortable, fact.unit, min(offsets), fid))
    rows.sort()
    # Keep the latest matched window for traces while reporting the truncation.
    limit = query.get("limit", 100)
    known = [row for row in rows if not row[0]]
    unknown = [row for row in rows if row[0]]
    selected = (known[-limit:] + unknown)[:limit]
    return {
        "fact_ids": [row[-1] for row in selected],
        "matched": len(rows),
        "time_axis": time_axis,
        "omitted_unknown_time": missing_time - len(unknown),
        "unresolved_topics": resolved["unresolved_topics"],
        "topic_codes": resolved["topic_codes"],
    }


def _evidence(index, fid: str) -> dict:
    fact = index.store.facts.get(fid)
    if fact is None or not fact.src:
        raise ToolError("invalid_evidence", "fact has no indexed source")
    annotations = index.facts[fid]
    record = index.revisions[fact.unit]
    conversation = record["conversation"]
    day, time, _ = _source_time(record)
    return {
        "fact_id": fid,
        "content": fact.text,
        "topics": list(fact.topics),
        "entities": list(fact.entities),
        "status": annotations.get("status", "statement"),
        "kind": fact.kind,
        "source_time": time,
        "source_day": day or None,
        "time_precision": ("timestamp" if conversation.get("started_at") else "date")
        if time
        else "unknown",
        "event_date": annotations.get("event_date"),
        "source_revision_active": record["active"],
        "state_key": annotations.get("state_key"),
        "state": annotations.get("state"),
        "supersedes": annotations.get("supersedes"),
        "evidence": [
            {
                "source": conversation["source"],
                "source_reference": conversation["metadata"]["source_reference"],
                "conversation_id": conversation["conversation_id"],
                "revision_checksum": record["checksum"],
                "source_line_id": sid,
                **index.segments[sid],
            }
            for sid in fact.src
        ],
    }


def _precedes(first: dict, second: dict, time_axis: str) -> bool:
    if time_axis == "event":
        left, right = first["event_date"], second["event_date"]
        return bool(left and right and left < right)
    if first["source_day"] and second["source_day"] and first["source_day"] != second["source_day"]:
        return first["source_day"] < second["source_day"]
    if first["time_precision"] == second["time_precision"] == "timestamp":
        left, right = (utc_timestamp(row["source_time"]) for row in (first, second))
        if left != right:
            return left < right
    checksum = first["evidence"][0]["revision_checksum"]
    if all(
        line["revision_checksum"] == checksum and "start_ms" in line
        for row in (first, second)
        for line in row["evidence"]
    ):
        left = first["evidence"]
        right_start = min(line["start_ms"] for line in second["evidence"])
        return (
            max(line["start_ms"] for line in left) < right_start
            and max(line.get("end_ms", line["start_ms"]) for line in left) <= right_start
        )
    return False


def _states(events: list[dict], complete: bool, time_axis: str) -> dict:
    groups = defaultdict(list)
    for event in events:
        if event["state_key"] and event["status"] == "confirmed" and event["effective"]:
            groups[event["state_key"]].append(event)
    result = {}
    for key, group in sorted(groups.items()):
        latest = [
            row
            for row in group
            if not any(_precedes(row, other, time_axis) for other in group if other is not row)
        ]
        values = {row["state"] for row in latest}
        status = "confirmed" if len(values) == 1 else "ambiguous"
        result[key] = {
            "status": status if complete else "partial",
            "value": next(iter(values)) if status == "confirmed" and complete else None,
            "fact_ids": [row["fact_id"] for row in latest],
        }
    return result


def report(context, matches: dict) -> dict:
    index = context.workspace.index()
    timeline = [_evidence(index, fid) for fid in matches["fact_ids"]]
    superseded = set()
    targets = [
        fact["supersedes"]
        for fid, fact in index.facts.items()
        if "supersedes" in fact and index.revisions[index.store.facts[fid].unit]["active"]
    ]
    while targets:
        target = targets.pop()
        if target in superseded:
            continue
        superseded.add(target)
        if predecessor := index.facts[target].get("supersedes"):
            targets.append(predecessor)
    for row in timeline:
        row["effective"] = row["source_revision_active"] and row["fact_id"] not in superseded
    sections = {
        label: [row["fact_id"] for row in timeline if row["status"] == status]
        for label, status in (
            ("confirmed_decisions", "confirmed"),
            ("proposals", "proposal"),
            ("rejected_options", "rejected"),
            ("open_questions", "open"),
        )
    }
    complete = (
        matches["matched"] == len(timeline)
        and matches["omitted_unknown_time"] == 0
        and not matches["unresolved_topics"]
    )
    return {
        "trace": context.pipeline,
        "time_axis": matches["time_axis"],
        "timeline": timeline,
        **sections,
        "current_reported_states": _states(timeline, complete, matches["time_axis"]),
        "matched_count": matches["matched"],
        "truncated": matches["matched"] > len(timeline),
        "omitted_unknown_time": matches["omitted_unknown_time"],
        "resolved_topics": matches["topic_codes"],
        "unresolved_topics": matches["unresolved_topics"],
        "memory_version": context.workspace.version(),
    }
