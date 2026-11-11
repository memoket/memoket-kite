"""Exact posting-list retrieval and evidence-backed trace reports."""

from __future__ import annotations

import re
from collections import defaultdict

from memoket_kite.core.vocab import norm_code

from .contracts import (
    CURSOR,
    DATE,
    TEXT,
    ToolError,
    array,
    encode,
    object_schema,
    page_cursor,
    page_offset,
    validate,
)
from .index import event_date_basis, revision_id
from .ingest import source_day
from .timing import precedes, source_timing

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
        "conversation_id": TEXT,
        "state_key": TEXT,
        "follow_states": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "evidence_byte_budget": {"type": "integer", "minimum": 128, "maximum": 1024 * 1024},
        "cursor": CURSOR,
    },
)
QUERY["dependentRequired"] = {"conversation_id": ["source"]}


def topic_inventory(workspace, *, contains: str | None, limit: int, cursor: str | None) -> dict:
    if not 1 <= limit <= 1000:
        raise ToolError("invalid_input", "topic limit must be between 1 and 1000")
    search = norm_code(contains) if contains is not None else ""
    if contains is not None and not search:
        raise ToolError("invalid_input", "topic search must contain an indexable character")
    if cursor is not None:
        validate(CURSOR, cursor, "topic cursor")
    identity, version = workspace.snapshot_identity()
    scope = [identity, version, "topics-v1", search, limit]
    offset = page_offset(scope, cursor, name="topic listing")
    index = workspace.index()
    matches = [
        topic
        for code, topic in sorted(index.vocab.topics.items())
        if topic.status != "deprecated"
        and (search in code or any(search in alias for alias in topic.aliases))
    ]
    end = offset + limit
    return {
        "topics": [
            {
                "code": topic.code,
                "aliases": sorted(topic.aliases),
                "parents": sorted(topic.parents),
                "direct_fact_count": len(index.store.by_topic.get(topic.code, ())),
            }
            for topic in matches[offset:end]
        ],
        "matched_count": len(matches),
        "memory_version": version,
        "next_cursor": page_cursor(scope, end) if end < len(matches) else None,
    }


def _page_scope(context, query: dict) -> list:
    workspace_id, version = context.workspace.snapshot_identity()
    return [
        workspace_id,
        version,
        context.pipeline,
        {k: v for k, v in query.items() if k != "cursor"},
        "source-time-v2",
    ]


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
        page_offset(_page_scope(context, query), query["cursor"])
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


def _source_time(record: dict) -> tuple[str, str | None]:
    conversation = record["conversation"]
    day = source_day(conversation)
    return day or "", conversation.get("started_at", day)


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


def evidence_context(
    workspace, line_id: str, radius: int, *, byte_budget: int | None = None
) -> dict:
    match = re.fullmatch(r"c-([a-f0-9]{64})L([1-9][0-9]{0,4})", line_id)
    if match is None:
        raise ToolError("invalid_input", "use a source_line_id from a trace or source-lines page")
    _validate_context_window(radius, byte_budget)
    checksum, position = match.groups()
    record = workspace.record(checksum)
    offset = int(position) - 1
    if offset >= len(record["conversation"]["segments"]):
        raise ToolError("not_found", "source line was not found")
    return _context_window(workspace, record, offset, radius, byte_budget)


def segment_context(
    workspace,
    *,
    source: str,
    conversation_id: str,
    segment_id: str,
    checksum: str | None,
    radius: int,
    byte_budget: int | None,
) -> dict:
    _validate_context_window(radius, byte_budget)
    record = workspace.source_revision(source, conversation_id, checksum)
    for offset, segment in enumerate(record["conversation"]["segments"]):
        if segment["segment_id"] == segment_id:
            return _context_window(workspace, record, offset, radius, byte_budget)
    raise ToolError("not_found", "segment ID was not found in the selected revision")


def _validate_context_window(radius: int, byte_budget: int | None) -> None:
    if not 0 <= radius <= 20:
        raise ToolError("invalid_input", "context radius must be between 0 and 20")
    if byte_budget is not None and not 128 <= byte_budget <= 1024 * 1024:
        raise ToolError("invalid_input", "context byte budget must be 128–1048576")


def _context_window(
    workspace, record: dict, offset: int, radius: int, byte_budget: int | None
) -> dict:
    segments = record["conversation"]["segments"]
    revision = revision_id(record)

    def cited(index: int) -> dict:
        return _citation(record, f"{revision}L{index + 1}", segments[index])

    before, after = [], []
    available_before = min(radius, offset)
    available_after = min(radius, len(segments) - offset - 1)
    content = {"evidence": cited(offset), "before": before, "after": after}
    size = len(encode(content)) if byte_budget is not None else 0
    blocked = set()
    for distance in range(1, max(available_before, available_after) + 1):
        for direction, lines, available in (
            (-1, before, available_before),
            (1, after, available_after),
        ):
            if direction in blocked or distance > available:
                continue
            line = cited(offset + direction * distance)
            if byte_budget is not None:
                # The JSON arrays are compact and their existing brackets are counted.
                next_size = size + len(encode(line)) + (1 if lines else 0)
                if next_size > byte_budget:
                    blocked.add(direction)
                    continue
                size = next_size
            lines.append(line)
    before.reverse()

    result = {
        **content,
        "source_revision_active": record["active"],
        "source_time": _source_time(record)[1],
        "memory_version": workspace.version(),
    }
    if byte_budget is not None:
        result["context_budget"] = {
            "limit_bytes": byte_budget,
            "payload_bytes": size,
            "over_budget": size > byte_budget,
            "omitted_before": available_before - len(before),
            "omitted_after": available_after - len(after),
        }
    return result


def _trace_rows(
    index, candidates: set[str], query: dict, time_axis: str, state_keys: set[str] | None = None
) -> tuple[list[tuple], int, int, set[str]]:
    rows = []
    omitted_counts = {"unknown": 0, "inferred": 0}
    omitted_states = set()
    bounded = "start_date" in query or "end_date" in query
    time_order = {"explicit": 0, "inferred": 1, "unknown": 2}
    for fid in candidates:
        annotation = index.facts[fid]
        key = annotation.get("state_key")
        if ("state_key" in query and key != query["state_key"]) or (
            state_keys is not None and key not in state_keys
        ):
            continue
        fact = index.store.facts[fid]
        record = index.revisions[fact.unit]
        conversation = record["conversation"]
        if (
            query.get("source", conversation["source"]) != conversation["source"]
            or query.get("conversation_id", conversation["conversation_id"])
            != conversation["conversation_id"]
        ):
            continue
        if "projects" in query and conversation["metadata"].get("project") not in query["projects"]:
            continue
        day = source_day(conversation) or ""
        basis = "explicit" if day else "unknown"
        if time_axis == "event":
            day = annotation.get("event_date", "")
            basis = event_date_basis(annotation)
        if bounded:
            if basis != "explicit":
                omitted_counts[basis] += 1
                if key and annotation.get("status") == "confirmed" and record["active"]:
                    omitted_states.add(fid)
                continue
            if not query.get("start_date", "") <= day <= query.get("end_date", "9999-12-31"):
                continue
        segments = [index.segments[sid] for sid in fact.src]
        sortable = day
        if time_axis == "source" and day:
            _, _, absolute = source_timing(fact.unit, day, conversation.get("started_at"), segments)
            sortable = absolute[0]
        first_offset = min(line.get("start_ms", -1) for line in segments)
        rows.append((time_order[basis], sortable, fact.unit, first_offset, fid))
    return rows, omitted_counts["unknown"], omitted_counts["inferred"], omitted_states


def retrieve(context, resolved: dict) -> dict:
    index = context.workspace.index()
    query = resolved["query"]
    candidates = set(index.facts)
    if "topics" in query:
        candidates &= set().union(
            *(index.store.by_topic.get(code, set()) for code in resolved["topic_codes"])
        )
    follow = query.get("follow_states", False)
    topic_scope = candidates.copy() if follow else set()
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
    if "text" in query:
        needle = query["text"].casefold()
        candidates = {
            fid
            for fid in candidates
            if needle in index.store.facts[fid].text.casefold()
            or any(
                needle in index.segments[sid]["text"].casefold()
                for sid in index.store.facts[fid].src
            )
        }
    time_axis = query.get("time_axis", "source")
    rows, omitted_unknown, omitted_inferred, omitted_states = _trace_rows(
        index, candidates, query, time_axis
    )
    extras = {}
    if follow:
        keys = {
            index.facts[row[-1]]["state_key"] for row in rows if "state_key" in index.facts[row[-1]]
        }
        if keys:
            additional, unknown, inferred, related_omissions = _trace_rows(
                index, topic_scope - candidates, query, time_axis, keys
            )
            rows.extend(additional)
            omitted_unknown += unknown
            omitted_inferred += inferred
            omitted_states.update(related_omissions)
    rows.sort()
    # Select from newest dated to oldest, then from the uncertain tail.
    limit = query.get("limit", 100)
    scope = _page_scope(context, query)
    offset = page_offset(scope, query.get("cursor"))
    known = [row for row in rows if not row[0]]
    uncertain = [row for row in rows if row[0]]
    end = max(0, len(known) - offset)
    selected = known[max(0, end - limit) : end][::-1]
    dated_count = len(selected)
    uncertain_start = max(0, offset - len(known))
    selected += uncertain[uncertain_start : uncertain_start + limit - len(selected)]
    if "evidence_byte_budget" in query:
        selected, extras["evidence_budget"] = _budgeted_page(
            index, selected, query["evidence_byte_budget"]
        )
    # Keep the displayed dated portion chronological after budgeting.
    selected[:dated_count] = reversed(selected[:dated_count])
    if follow:
        extras["followed_state_keys"] = sorted(
            {
                index.facts[row[-1]]["state_key"]
                for row in selected
                if "state_key" in index.facts[row[-1]]
            }
        )
    if omitted_states and selected:
        page_keys = {index.facts[row[-1]].get("state_key") for row in selected}
        relevant = {fid for fid in omitted_states if index.facts[fid]["state_key"] in page_keys}
        if relevant:
            superseded, _ = _supersession(index, time_axis, query.get("end_date"))
            affected = {index.facts[fid]["state_key"] for fid in relevant - superseded}
            if affected:
                extras["uncertain_time_state_keys"] = sorted(affected)
    if "conversation_id" in query:
        extras["conversation_scope"] = {
            "source": query["source"],
            "conversation_id": query["conversation_id"],
        }
    next_offset = offset + len(selected)
    return {
        "fact_ids": [row[-1] for row in selected],
        "matched": len(rows),
        "next_cursor": page_cursor(scope, next_offset) if next_offset < len(rows) else None,
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
    day, time = _source_time(record)
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


def _state_time(row: dict, time_axis: str) -> tuple:
    if time_axis == "event":
        day = row["event_date"] if row["event_date_basis"] == "explicit" else None
        return None, None, (day, day) if day else None
    return source_timing(
        row["evidence"][0]["revision_checksum"],
        row["source_day"],
        row["source_time"] if row["time_precision"] == "timestamp" else None,
        row["evidence"],
    )


def _states(events: list[dict], complete: bool, time_axis: str, omitted_states: set[str]) -> dict:
    groups = defaultdict(list)
    for event in events:
        if event["state_key"] and event["status"] == "confirmed" and event["effective"]:
            groups[event["state_key"]].append(event)
    result = {}
    for key, group in sorted(groups.items()):
        timings = [_state_time(row, time_axis) for row in group]
        latest = [
            row
            for row, timing in zip(group, timings)
            if not any(precedes(timing, other) for other in timings)
        ]
        values = {row["state"] for row in latest}
        has_orderable_date = time_axis == "source" or any(
            row["event_date_basis"] == "explicit" for row in latest
        )
        if (
            not complete
            or key in omitted_states
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


def memory_snapshot(workspace) -> dict:
    """Read all retained memories, with the same state semantics as a complete trace."""
    index = workspace.index()
    superseded, _ = _supersession(index, "source", None)
    timeline = [_evidence(index, fid) for fid in index.facts]
    for row in timeline:
        row["effective"] = row["source_revision_active"] and row["fact_id"] not in superseded
        row["supersession_uncertain"] = False
    states = _states(timeline, True, "source", set())

    evidence = {}
    for row in timeline:
        for line in row["evidence"]:
            evidence.setdefault(line["source_line_id"], line)
        row["evidence"] = [line["source_line_id"] for line in row["evidence"]]

    return {
        "version": workspace.version(),
        "sources": len(
            {
                (record["conversation"]["source"], record["conversation"]["conversation_id"])
                for record in index.revisions.values()
            }
        ),
        "topics": [
            {"code": topic.code, "aliases": sorted(topic.aliases), "parents": sorted(topic.parents)}
            for topic in index.vocab.topics.values()
            if topic.status != "deprecated"
        ],
        "facts": timeline,
        "states": states,
        "revisions": [
            {
                "checksum": record["checksum"],
                "reference": record["conversation"]["metadata"]["source_reference"],
                "title": record["conversation"].get("title"),
                "day": source_day(record["conversation"]),
                "active": record["active"],
            }
            for record in index.revisions.values()
        ],
        "evidence": evidence,
    }


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
    scope = matches.get("conversation_scope")
    superseded, uncertain = _supersession(index, matches["time_axis"], end_date)
    for row in timeline:
        row["effective"] = row["source_revision_active"] and row["fact_id"] not in superseded
        row["supersession_uncertain"] = row["effective"] and row["fact_id"] in uncertain
        if scope and row["supersedes"]:
            predecessor = index.store.facts[row["supersedes"]]
            conversation = index.revisions[predecessor.unit]["conversation"]
            if any(conversation[key] != value for key, value in scope.items()):
                row["supersedes"] = None
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
        and not matches["unresolved_topics"]
        and not matches["unresolved_events"]
        and not matches["unresolved_entities"]
    )
    topic_codes = set(matches["topic_codes"])
    omitted_states = set(matches.get("uncertain_time_state_keys", []))
    states = _states(timeline, complete, matches["time_axis"], omitted_states)
    uncertain_time_keys = sorted(states.keys() & omitted_states)
    return {
        "trace": context.pipeline,
        "time_axis": matches["time_axis"],
        **({"end_date": end_date} if end_date is not None else {}),
        **({"evidence_budget": matches["evidence_budget"]} if "evidence_budget" in matches else {}),
        **(
            {"followed_state_keys": matches["followed_state_keys"]}
            if "followed_state_keys" in matches
            else {}
        ),
        "timeline": timeline,
        **sections,
        "current_reported_states": states,
        **({"uncertain_time_state_keys": uncertain_time_keys} if uncertain_time_keys else {}),
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
