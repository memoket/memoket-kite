"""Search and pack original conversation spans for a host agent."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict

from .contracts import DATE, TEXT, ToolError, array, object_schema, validate
from .index import SPAN_FACET_AXES, revision_id
from .ingest import source_day
from .retrieve import _citation

_SPAN_CHARS = 320
_SEARCH_TERMS = re.compile(r"[\u3400-\u9fff]+|[^\W_]+", re.UNICODE)
_HAN = re.compile(r"([\u3400-\u9fff])")
_CONTEXT_SCHEMA = object_schema(
    {"terms": array({**TEXT, "maxLength": 128}, 1, 12)},
    {"source": TEXT, "start_date": DATE, "end_date": DATE, "latest": {"type": "boolean"}},
)


def _tokens(text: str) -> str:
    # Put a boundary between Han characters for mixed-language phrase search.
    return " ".join(
        _SEARCH_TERMS.findall(_HAN.sub(r" \1 ", unicodedata.normalize("NFKC", text).casefold()))
    )


def context_enabled(db) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='context_snapshot'").fetchone())


def enable_context(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS context_snapshot (version INTEGER NOT NULL)")


def _span_labels(record: dict, vocab) -> dict:
    labels = defaultdict(set)
    for span in record.get("index_spans", ()):
        names = set()
        for topic in span["topics"]:
            code = vocab.resolve_topic(topic)
            if code:
                pending = [code]
                visited = set()
                while pending:
                    node = pending.pop()
                    if node in visited:
                        continue
                    visited.add(node)
                    definition = vocab.topics[node]
                    names.update({node, *definition.aliases})
                    pending.extend(definition.parents)
        for entity in span["entities"]:
            code = vocab.resolve_entity(entity)
            if code:
                names.update({code, *vocab.entities[code].aliases})
        facets = span.get("facets", {})
        for axis in SPAN_FACET_AXES:
            names.update(facets.get(axis, ()))
        if duration := facets.get("duration"):
            names.add(duration)
        for segment_id in span["segment_ids"]:
            labels[segment_id].update(names)
    return labels


def _spans(segments: list[dict]):
    start = size = 0
    for index, segment in enumerate(segments):
        length = len(segment["text"])
        if size and size + length > _SPAN_CHARS:
            yield start, index
            start = index
            size = 0
        size += length
    yield start, len(segments)


def _index_rows(records: list[dict], vocab):
    for record in records:
        if not record["active"]:
            continue
        conversation = record["conversation"]
        segments = conversation["segments"]
        labels = _span_labels(record, vocab)
        title = _tokens(conversation.get("title", ""))
        day = source_day(conversation) or ""
        for start, stop in _spans(segments):
            selected = segments[start:stop]
            text = " ".join(_tokens(row["text"]) for row in selected)
            keys = sorted(
                {label for row in selected for label in labels.get(row["segment_id"], ())}
            )
            yield (
                text,
                title,
                _tokens(" ".join(keys)),
                record["checksum"],
                start,
                stop,
                day,
                conversation["source"],
            )


def rebuild_context(db, records: list[dict], vocab, version: int) -> None:
    # Derived data is replaced in the same transaction as the source snapshot.
    # Dropping the shadow tables also purges obsolete postings on older SQLite.
    db.execute("DROP TABLE IF EXISTS context_spans")
    try:
        db.execute(
            "CREATE VIRTUAL TABLE context_spans USING fts5("
            "text, title, labels, checksum UNINDEXED, start UNINDEXED, stop UNINDEXED,"
            "day UNINDEXED, source UNINDEXED)"
        )
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc):
            raise
        raise ToolError("missing_dependency", "this Python SQLite build needs FTS5") from exc
    db.executemany(
        "INSERT INTO context_spans VALUES(?, ?, ?, ?, ?, ?, ?, ?)", _index_rows(records, vocab)
    )
    db.execute("DELETE FROM context_snapshot")
    db.execute("INSERT INTO context_snapshot VALUES(?)", (version,))


def _candidates(db, plan: dict, limit: int) -> list:
    filters = ["context_spans MATCH ?"]
    args = []
    for field, operator, key in (
        ("source", "=", "source"),
        ("day", ">=", "start_date"),
        ("day", "<=", "end_date"),
    ):
        if key in plan:
            filters.append(f"{field} {operator} ?")
            args.append(plan[key])
    if "start_date" in plan or "end_date" in plan:
        filters.append("day <> ''")
    where = " AND ".join(filters)
    order = "bm25(context_spans, 1.0, 0.35, 1.2), day DESC"
    if plan.get("latest"):
        order = "day DESC, bm25(context_spans, 1.0, 0.35, 1.2)"
    # Round-robin across terms. A common term cannot consume the whole pack.
    streams = []
    for phrase in dict.fromkeys(map(_tokens, plan["terms"])):
        if not phrase:
            continue
        matches = db.execute(
            f"SELECT checksum, start, stop FROM context_spans WHERE {where} "
            f"ORDER BY {order}, checksum, start LIMIT ?",
            (f'"{phrase}"', *args, limit * 6),
        ).fetchall()
        streams.append(iter(matches))
    results = []
    seen = set()
    while streams:
        remaining = []
        for stream in streams:
            row = next(stream, None)
            if row is None:
                continue
            key = (row["checksum"], row["start"])
            if key not in seen:
                seen.add(key)
                results.append(row)
            remaining.append(stream)
        streams = remaining
    return results


def _pack(workspace, rows: list, limit: int, max_bytes: int, neighbors: int):
    records = {}
    seen = defaultdict(set)
    packed = []
    size = omitted = 0
    for row in rows:
        if len(packed) == limit:
            break
        checksum = row["checksum"]
        if checksum not in records:
            records[checksum] = workspace.record(checksum)
        record = records[checksum]
        conversation = record["conversation"]
        segments = conversation["segments"]
        start, stop = row["start"], row["stop"]
        if not record["active"] or not 0 <= start < stop <= len(segments):
            raise ToolError("integrity_error", "context index does not match its source revision")
        window = range(max(0, start - neighbors), min(len(segments), stop + neighbors))
        # Fill from the matched span first, then add optional context.
        positions = [*range(start, stop), *(i for i in window if not start <= i < stop)]
        chosen = []
        for pos in positions:
            if pos in seen[checksum]:
                continue
            segment = segments[pos]
            weight = len(segment["text"].encode("utf-8"))
            if size + weight > max_bytes:
                omitted += 1
                continue
            size += weight
            chosen.append(pos)
            seen[checksum].add(pos)
        if chosen:
            packed.append(
                {
                    "source_date": source_day(conversation),
                    "title": conversation.get("title", ""),
                    "evidence": [
                        _citation(record, f"{revision_id(record)}L{pos + 1}", segments[pos])
                        for pos in sorted(chosen)
                    ],
                }
            )
    return packed, size, omitted


def context_pack(workspace, plan: dict, *, limit: int, max_bytes: int, neighbors: int) -> dict:
    validate(_CONTEXT_SCHEMA, plan, "context plan")
    if plan.get("start_date", "") > plan.get("end_date", "9999-12-31"):
        raise ToolError("invalid_input", "start date must not follow end date")
    if not 1 <= limit <= 50 or not 512 <= max_bytes <= 65536 or not 0 <= neighbors <= 5:
        raise ToolError(
            "invalid_input", "context needs limit 1–50, max bytes 512–65536, neighbors 0–5"
        )
    if not any(map(_tokens, plan["terms"])):
        raise ToolError("invalid_input", "context needs an indexable term")
    if not context_enabled(workspace.db):
        raise ToolError(
            "index_unavailable", "run kite-tool index-sources to build the context index"
        )
    version = workspace.version()
    stamp = workspace.db.execute("SELECT version FROM context_snapshot").fetchone()
    if stamp is None or stamp["version"] != version:
        raise ToolError("stale_index", "run kite-tool index-sources to rebuild the context index")
    candidates = _candidates(workspace.db, plan, limit)
    spans, size, omitted = _pack(workspace, candidates, limit, max_bytes, neighbors)
    return {
        "memory_version": version,
        "plan": plan,
        "spans": spans,
        "quote_bytes": size,
        "quote_budget_bytes": max_bytes,
        "lines_over_budget": omitted,
        "candidate_spans": len(candidates),
        "span_limit": limit,
    }


def render_context(pack: dict) -> str:
    namespaces = defaultdict(set)
    for span in pack["spans"]:
        for line in span["evidence"]:
            namespaces[line["conversation_id"]].add(line["source"])
    lines = []
    for span in pack["spans"]:
        if span["source_date"]:
            lines.append(f"Date: {span['source_date']}")
        for line in span["evidence"]:
            ref = f"{line['conversation_id']}#{line['segment_id']}"
            if len(namespaces[line["conversation_id"]]) > 1:
                ref = f"{line['source']}/{ref}"
            speaker = f" {line['speaker_id']}:" if "speaker_id" in line else ""
            lines.append(f"[{ref}]{speaker} {line['text']}")
        lines.append("")
    if not lines:
        return "No evidence matched."
    return "\n".join(lines).strip()
