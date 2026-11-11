"""Summarize source revision changes using stable segment IDs."""

from __future__ import annotations

from .contracts import CURSOR, ToolError, encode, page_cursor, page_offset, validate
from .index import revision_id


def source_changes(
    conversation: dict,
    current: dict | None,
    *,
    snapshot: tuple[str | None, int],
    checksum: str,
    limit: int,
    cursor: str | None,
) -> dict:
    if not 1 <= limit <= 200:
        raise ToolError("invalid_input", "source change limit must be between 1 and 200")
    if cursor is not None:
        validate(CURSOR, cursor, "source change cursor")
    scope = [
        *snapshot,
        "source-changes-v1",
        current["checksum"] if current else None,
        checksum,
        limit,
    ]
    offset = page_offset(scope, cursor, name="source comparison")

    previous = current["conversation"] if current else {"segments": []}
    current_revision_id = revision_id(current) if current is not None else None
    before = {
        segment["segment_id"]: (position, segment)
        for position, segment in enumerate(previous["segments"], 1)
    }
    after = {segment["segment_id"]: segment for segment in conversation["segments"]}
    common = before.keys() & after.keys()
    counts = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    segments = []
    affected = set()
    for segment_id in dict.fromkeys((*after, *before)):
        entry = {"segment_id": segment_id}
        if segment_id not in before:
            change = "added"
        elif segment_id not in after:
            change = "removed"
        else:
            old, new = before[segment_id][1], after[segment_id]
            if old == new:
                counts["unchanged"] += 1
                continue
            change = "modified"
            entry["fields"] = sorted(
                key for key in old.keys() | new.keys() if old.get(key) != new.get(key)
            )
        if current_revision_id is not None and segment_id in before:
            affected.add(segment_id)
            entry["current_source_line_id"] = f"{current_revision_id}L{before[segment_id][0]}"
        counts[change] += 1
        segments.append({**entry, "change": change})

    return {
        "conversation_fields": sorted(
            field
            for field in (previous.keys() | conversation.keys()) - {"segments"}
            if encode(previous.get(field)) != encode(conversation.get(field))
        )
        if current
        else [],
        "segment_order_changed": [sid for sid in before if sid in common]
        != [sid for sid in after if sid in common],
        "counts": counts,
        "affected_current_facts": sum(
            not affected.isdisjoint(fact["segment_ids"]) for fact in current["facts"]
        )
        if current
        else 0,
        "segments": segments[offset : offset + limit],
        "next_cursor": page_cursor(scope, offset + limit)
        if offset + limit < len(segments)
        else None,
    }
