"""Exact posting-list retrieval and evidence-backed trace reports."""

from __future__ import annotations

import re
from collections import defaultdict

from memoket_kite.core.vocab import norm_code

from .contracts import DATE, TEXT, ToolError, array, digest, encode, object_schema, utc_timestamp
from .index import event_date_basis, revision_id
from .ingest import source_day

CURSOR = {"type": "string", "pattern": r"^[0-9]{1,16}\.[1-9][0-9]{0,15}\.[a-f0-9]{64}$"}

QUERY = object_schema(
    {},
    {
        "topics": array(TEXT, 1, 32),
        "entities": array(TEXT, 1, 32),
        "events": array(TEXT, 1, 32),
        "speakers": array(TEXT, 1, 32),
        "projects": array(TEXT, 1, 32),
        "text": TEXT,
        "start_date": DATE,
        "end_date": DATE,
        "time_axis": {"enum": ["source", "event"]},
        "source": TEXT,
        "state_key": TEXT,
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "evidence_byte_budget": {"type": "integer", "minimum": 128, "maximum": 1024 * 1024},
        "cursor": CURSOR,
    },
)


def _cursor(scope: list, offset: int) -> str:
    version = scope[1]
    return f"{version}.{offset}.{digest([scope, offset])}"


def _page_scope(context, query: dict) -> list:
    workspace_id, version = context.workspace.snapshot_identity()
    return [
        workspace_id,
        version,
        context.pipeline,
        {k: v for k, v in query.items() if k != "cursor"},
    ]


def _page_offset(scope: list, query: dict) -> int:
    token = query.get("cursor")
    if token is None:
        return 0
    version, offset, _ = token.split(".")
    if int(version) != scope[1]:
        raise ToolError("stale_snapshot", "memory changed; restart the trace without its cursor")
    offset = int(offset)
    if token != _cursor(scope, offset):
        raise ToolError("invalid_input", "cursor belongs to another workspace or query")
    return offset


def resolve(context, query: dict) -> dict:
    if query.get("start_date", "") > query.get("end_date", "9999-12-31"):
        raise ToolError("invalid_input", "start_date must be on or before end_date")
    if context.pipeline == "topic-tracing" and not query.get("topics"):
        raise ToolError("invalid_input", "topic-tracing requires one or more topic names")
    if context.pipeline in ("task-tracing", "decision-evolution") and not (
        set(query) & {"topics", "entities", "events", "text", "state_key"}
    ):
        raise ToolError(
            "invalid_input",
            "a task or decision trace requires a topic, entity, event, text or state key",
        )
    if "cursor" in query:
        _page_offset(_page_scope(context, query), query)
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
    event_codes, unknown_events = set(), []
    for event in query.get("events", []):
        code = norm_code(event)
        if code in index.store.by_event:
            event_codes.add(code)
        else:
            unknown_events.append(event)
    entity_codes, unknown_entities = set(), []
    for entity in query.get("entities", []):
        code = index.vocab.resolve_entity(entity)
        if code is None:
            unknown_entities.append(entity)
        else:
            entity_codes.add(code)
    return {
        "query": query,
        "topic_codes": sorted(codes),
        "unresolved_topics": unknown,
        "event_codes": sorted(event_codes),
        "unresolved_events": unknown_events,
        "entity_codes": sorted(entity_codes),
        "unresolved_entities": unknown_entities,
    }


def _source_time(record: dict) -> tuple[str, str | None, str]:
    conversation = record["conversation"]
    day = source_day(conversation)
    timestamp = conversation.get("started_at")
    if timestamp:
        return day, timestamp, utc_timestamp(timestamp).isoformat()
    return day or "", day, day or ""


def _citation(record: dict, line_id: str, segment: dict) -> dict:
    conversation = record["conversation"]
    return {
        "source": conversation["source"],
        "source_reference": conversation["metadata"]["source_reference"],
        "conversation_id": conversation["conversation_id"],
        "revision_checksum": record["checksum"],
        "source_line_id": line_id,
        **segment,
    }


def _citations(index, fact) -> list[dict]:
    record = index.revisions[fact.unit]
    return [_citation(record, sid, index.segments[sid]) for sid in fact.src]


def _budgeted_page(index, rows: list[tuple], budget: int) -> tuple[list[tuple], dict]:
    size = 2  # Outer JSON array delimiters.
    count = 0
    for row in rows:
        fact = index.store.facts[row[-1]]
        next_size = size + len(encode(_citations(index, fact))) + (1 if count else 0)
        if count and next_size > budget:
            break
        size = next_size
        count += 1
    return rows[:count], {
        "limit_bytes": budget,
        "payload_bytes": size,
        "over_budget": size > budget,
    }


def evidence_context(workspace, line_id: str, radius: int) -> dict:
    match = re.fullmatch(r"c-([a-f0-9]{64})L([1-9][0-9]{0,4})", line_id)
    if match is None or not 0 <= radius <= 20:
        raise ToolError("invalid_input", "use a source_line_id and a context radius of 0–20")
    checksum, position = match.groups()
    record = workspace.record(checksum)
    segments = record["conversation"]["segments"]
    offset = int(position) - 1
    if offset >= len(segments):
        raise ToolError("not_found", "source line was not found")
    revision = revision_id(record)

    def cited(index: int) -> dict:
        return _citation(record, f"{revision}L{index + 1}", segments[index])

    return {
        "evidence": cited(offset),
        "before": [cited(index) for index in range(max(0, offset - radius), offset)],
        "after": [
            cited(index) for index in range(offset + 1, min(len(segments), offset + radius + 1))
        ],
        "source_revision_active": record["active"],
        "source_time": _source_time(record)[1],
        "memory_version": workspace.version(),
    }


def retrieve(context, resolved: dict) -> dict:
    index = context.workspace.index()
    query = resolved["query"]
    candidates = set(index.facts)
    if "topics" in query:
        candidates &= set().union(
            *(index.store.by_topic.get(code, set()) for code in resolved["topic_codes"])
        )
    if "entities" in query:
        candidates &= set().union(
            *(index.store.by_entity.get(code, set()) for code in resolved["entity_codes"])
        )
    if "events" in query:
        candidates &= set().union(*(index.store.by_event[code] for code in resolved["event_codes"]))
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
    omitted_unknown = omitted_inferred = 0
    time_axis = query.get("time_axis", "source")
    bounded = "start_date" in query or "end_date" in query
    time_order = {"explicit": 0, "inferred": 1, "unknown": 2}
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
        basis = "explicit" if day else "unknown"
        if time_axis == "event":
            annotation = index.facts[fid]
            day = sortable = annotation.get("event_date", "")
            basis = event_date_basis(annotation)
        if bounded:
            if basis == "inferred":
                omitted_inferred += 1
                continue
            if not day:
                omitted_unknown += 1
                continue
            if not query.get("start_date", "") <= day <= query.get("end_date", "9999-12-31"):
                continue
        offsets = [index.segments[sid].get("start_ms", -1) for sid in fact.src]
        rows.append((time_order[basis], day, sortable, fact.unit, min(offsets), fid))
    rows.sort()
    # Select from newest dated to oldest, then from the uncertain tail.
    limit = query.get("limit", 100)
    scope = _page_scope(context, query)
    offset = _page_offset(scope, query)
    known = [row for row in rows if not row[0]]
    uncertain = [row for row in rows if row[0]]
    end = max(0, len(known) - offset)
    selected = known[max(0, end - limit) : end][::-1]
    dated_count = len(selected)
    uncertain_start = max(0, offset - len(known))
    selected += uncertain[uncertain_start : uncertain_start + limit - len(selected)]
    extras = {}
    if "evidence_byte_budget" in query:
        selected, extras["evidence_budget"] = _budgeted_page(
            index, selected, query["evidence_byte_budget"]
        )
    # Keep the displayed dated portion chronological after budgeting.
    selected[:dated_count] = reversed(selected[:dated_count])
    next_offset = offset + len(selected)
    return {
        "fact_ids": [row[-1] for row in selected],
        "matched": len(rows),
        "next_cursor": _cursor(scope, next_offset) if next_offset < len(rows) else None,
        "time_axis": time_axis,
        "omitted_unknown_time": omitted_unknown,
        "omitted_inferred_time": omitted_inferred,
        "unresolved_topics": resolved["unresolved_topics"],
        "topic_codes": resolved["topic_codes"],
        "event_codes": resolved["event_codes"],
        "unresolved_events": resolved["unresolved_events"],
        "entity_codes": resolved["entity_codes"],
        "unresolved_entities": resolved["unresolved_entities"],
        **({"end_date": query["end_date"]} if "end_date" in query else {}),
        **extras,
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
        "events": list(fact.event),
        "status": annotations.get("status", "statement"),
        "kind": fact.kind,
        "source_time": time,
        "source_day": day or None,
        "time_precision": ("timestamp" if conversation.get("started_at") else "date")
        if time
        else "unknown",
        "event_date": annotations.get("event_date"),
        "event_date_basis": event_date_basis(annotations),
        "source_revision_active": record["active"],
        "state_key": annotations.get("state_key"),
        "state": annotations.get("state"),
        "supersedes": annotations.get("supersedes"),
        "evidence": _citations(index, fact),
    }


def _precedes(first: dict, second: dict, time_axis: str) -> bool:
    if time_axis == "event":
        left, right = first["event_date"], second["event_date"]
        return (
            first["event_date_basis"] == second["event_date_basis"] == "explicit" and left < right
        )
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
        has_orderable_date = time_axis == "source" or any(
            row["event_date_basis"] == "explicit" for row in latest
        )
        if (
            not complete
            or any(row["supersession_uncertain"] for row in group)
            or (len(values) == 1 and not has_orderable_date)
        ):
            status = "partial"
        else:
            status = "confirmed" if len(values) == 1 else "ambiguous"
        result[key] = {
            "status": status,
            "value": next(iter(values)) if status == "confirmed" else None,
            "fact_ids": [row["fact_id"] for row in latest],
        }
    return result


def _correction_targets(index, targets: list[str]) -> set[str]:
    reached = set()
    while targets:
        target = targets.pop()
        if target in reached:
            continue
        reached.add(target)
        if predecessor := index.facts[target].get("supersedes"):
            targets.append(predecessor)
    return reached


def _supersession(index, time_axis: str, end_date: str | None) -> tuple[set[str], set[str]]:
    targets, uncertain = [], []
    for fid, fact in index.facts.items():
        target = fact.get("supersedes")
        if not target:
            continue
        record = index.revisions[index.store.facts[fid].unit]
        if not record["active"]:
            continue
        if end_date is not None:
            if time_axis == "event":
                day = fact["event_date"] if event_date_basis(fact) == "explicit" else None
            else:
                day = source_day(record["conversation"])
            if day is None:
                uncertain.append(target)
                continue
            if day > end_date:
                continue
        targets.append(target)
    superseded = _correction_targets(index, targets)
    return superseded, _correction_targets(index, uncertain) - superseded


def _related_labels(timeline: list[dict], field: str, excluded: set[str]) -> list[dict]:
    support = defaultdict(list)
    for row in timeline:
        for code in set(row[field]) - excluded:
            support[code].append(row["fact_id"])
    ranked = sorted(support, key=lambda code: (-len(support[code]), code))
    return [
        {"code": code, "fact_count": len(support[code]), "fact_ids": support[code][:3]}
        for code in ranked[:10]
    ]


def report(context, matches: dict) -> dict:
    index = context.workspace.index()
    timeline = [_evidence(index, fid) for fid in matches["fact_ids"]]
    end_date = matches.get("end_date")
    superseded, uncertain = _supersession(index, matches["time_axis"], end_date)
    for row in timeline:
        row["effective"] = row["source_revision_active"] and row["fact_id"] not in superseded
        row["supersession_uncertain"] = row["effective"] and row["fact_id"] in uncertain
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
        and matches["omitted_unknown_time"] == matches["omitted_inferred_time"] == 0
        and not matches["unresolved_topics"]
        and not matches["unresolved_events"]
        and not matches["unresolved_entities"]
    )
    topic_codes = set(matches["topic_codes"])
    return {
        "trace": context.pipeline,
        "time_axis": matches["time_axis"],
        **({"end_date": end_date} if end_date is not None else {}),
        **({"evidence_budget": matches["evidence_budget"]} if "evidence_budget" in matches else {}),
        "timeline": timeline,
        **sections,
        "current_reported_states": _states(timeline, complete, matches["time_axis"]),
        "matched_count": matches["matched"],
        "next_cursor": matches["next_cursor"],
        "truncated": matches["matched"] > len(timeline),
        "omitted_unknown_time": matches["omitted_unknown_time"],
        "omitted_inferred_time": matches["omitted_inferred_time"],
        "resolved_topics": matches["topic_codes"],
        "unresolved_topics": matches["unresolved_topics"],
        "related_topics": _related_labels(timeline, "topics", topic_codes) if topic_codes else [],
        "related_entities": _related_labels(timeline, "entities", set()) if topic_codes else [],
        "resolved_events": matches["event_codes"],
        "unresolved_events": matches["unresolved_events"],
        "resolved_entities": matches["entity_codes"],
        "unresolved_entities": matches["unresolved_entities"],
        "memory_version": context.workspace.version(),
    }
