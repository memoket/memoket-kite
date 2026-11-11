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
_QUESTION_BYTES = 3800
_SEARCH_TERMS = re.compile(r"[\u3400-\u9fff]+|[^\W_]+", re.UNICODE)
_HAN = re.compile(r"([\u3400-\u9fff])")
_QUERY_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+(?:\s+[a-z0-9]+)*", re.I)
_QUERY_FILLERS = re.compile(
    r"(?:请问|根据|查找|查询|哪些|哪个|哪里|什么|如何|怎么|为什么|分别|"
    r"这场|那场|会上|那次|当时|原文|给出|引用|请|是否|有没有|的|了|吗|呢)"
)
_CONTEXT_SCHEMA = object_schema(
    {"terms": array({**TEXT, "maxLength": 128}, 1, 12)},
    {
        "source": TEXT,
        "conversation_id": TEXT,
        "start_date": DATE,
        "end_date": DATE,
        "latest": {"type": "boolean"},
    },
)
CONTEXT_REQUEST = object_schema(
    {"question": {**TEXT, "maxLength": 512}},
    {
        **{key: value for key, value in _CONTEXT_SCHEMA["properties"].items() if key != "terms"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "max_bytes": {"type": "integer", "minimum": 512, "maximum": 65536},
        "neighbors": {"type": "integer", "minimum": 0, "maximum": 5},
    },
)
CONTEXT_PLAN = object_schema(
    {"request": CONTEXT_REQUEST, "terms": array(TEXT, 1, 8), "memory_version": {"type": "integer"}}
)


def _tokens(text: str) -> str:
    # Put a boundary between Han characters for mixed-language phrase search.
    return " ".join(
        _SEARCH_TERMS.findall(_HAN.sub(r" \1 ", unicodedata.normalize("NFKC", text).casefold()))
    )


def context_enabled(db) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='context_snapshot'").fetchone())


def context_current(db, version: int) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM context_snapshot WHERE version=? "
            "AND EXISTS (SELECT 1 FROM sqlite_master WHERE name='context_spans' AND type='table')",
            (version,),
        ).fetchone()
        is not None
    )


def _require_context(workspace) -> int:
    if not context_enabled(workspace.db):
        raise ToolError(
            "index_unavailable", "run kite-tool index-sources to build the context index"
        )
    version = workspace.version()
    if not context_current(workspace.db, version):
        raise ToolError("stale_index", "run kite-tool index-sources to rebuild the context index")
    return version


def _question_phrases(question: str):
    normalized = unicodedata.normalize("NFKC", question).casefold()
    normalized = re.sub(r"\b(\d+)\s*k(?:hz)?\b", r"\1 k", normalized)
    for block in _QUERY_WORDS.findall(normalized):
        if _HAN.search(block):
            for fragment in _QUERY_FILLERS.split(block):
                for start in range(len(fragment) - 1):
                    for width in range(2, min(7, len(fragment) - start) + 1):
                        yield fragment[start : start + width]
        else:
            words = block.split()
            for start, word in enumerate(words):
                if len(word) > 1 and not word.isdecimal():
                    yield word
                for width in (2, 3):
                    if start + width <= len(words):
                        yield " ".join(words[start : start + width])


def compile_question(workspace, request: dict) -> dict:
    validate(CONTEXT_REQUEST, request, "context request")
    version = _require_context(workspace)
    where, args = _search_scope(request)
    candidates = []
    for phrase in dict.fromkeys(_question_phrases(request["question"])):
        expression = f'"{_tokens(phrase)}"'
        frequency = workspace.db.execute(
            f"SELECT count(*) FROM (SELECT rowid FROM context_spans WHERE {where} LIMIT 32)",
            (expression, *args),
        ).fetchone()[0]
        if frequency:
            labeled = workspace.db.execute(
                f"SELECT 1 FROM context_spans WHERE {where} LIMIT 1",
                (f"labels : {expression}", *args),
            ).fetchone()
            length = len(phrase.replace(" ", ""))
            score = min(length, 7) * (2 - frequency / 32) + (3 if labeled else 0)
            candidates.append((score, phrase))
    selected = []
    for _, phrase in sorted(candidates, key=lambda row: -row[0]):
        if not any(phrase in current or current in phrase for current in selected):
            selected.append(phrase)
        if len(selected) == 8:
            break
    return {
        "request": request,
        "terms": selected or [request["question"][:128]],
        "memory_version": version,
    }


def search_question(workspace, compiled: dict) -> dict:
    if compiled["memory_version"] != workspace.version():
        raise ToolError("stale_snapshot", "memory changed; compile the question again")
    request = compiled["request"]
    filters = {key: request[key] for key in _CONTEXT_SCHEMA["properties"] if key in request}
    result = context_pack(
        workspace,
        {"terms": compiled["terms"], **filters},
        limit=request.get("limit", 8),
        max_bytes=request.get("max_bytes", _QUESTION_BYTES),
        neighbors=request.get("neighbors", 1),
        focus=True,
    )
    return {**result, "compilation": {"question": request["question"], "terms": compiled["terms"]}}


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


def _search_scope(plan: dict) -> tuple[str, list]:
    if plan.get("start_date", "") > plan.get("end_date", "9999-12-31"):
        raise ToolError("invalid_input", "start date must not follow end date")
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
    if "conversation_id" in plan:
        filters.append(
            "checksum IN (SELECT checksum FROM revisions WHERE conversation_id=? AND active=1)"
        )
        args.append(plan["conversation_id"])
    if "start_date" in plan or "end_date" in plan:
        filters.append("day <> ''")
    return " AND ".join(filters), args


def _coherent_order(matches: list[list]) -> list:
    # Accumulate a term's best rank once per conversation. This rewards several
    # clues pointing to the same meeting without boosting long meetings for length.
    span_scores = defaultdict(float)
    source_scores = defaultdict(float)
    candidates = {}
    for term_rank, rows in enumerate(matches):
        seen_sources = set()
        for rank, row in enumerate(rows):
            checksum = row["checksum"]
            key = (checksum, row["start"])
            candidates[key] = row
            score = 1 / ((1 + term_rank / 4) * (3 + rank))
            span_scores[key] += score
            if checksum not in seen_sources:
                source_scores[checksum] += score
                seen_sources.add(checksum)
    ordered = sorted(candidates, key=lambda key: -(span_scores[key] + 0.5 * source_scores[key[0]]))
    return [candidates[key] for key in ordered]


def _candidates(db, plan: dict, limit: int, *, focus: bool = False) -> list:
    where, args = _search_scope(plan)
    order = "bm25(context_spans, 1.0, 0.35, 1.2), day DESC"
    if plan.get("latest"):
        order = "day DESC, bm25(context_spans, 1.0, 0.35, 1.2)"
    coherent = focus and not plan.get("latest")
    stream_limit = max(limit, min(limit * 6, 30)) if coherent else limit * 6
    matches = []
    for phrase in dict.fromkeys(map(_tokens, plan["terms"])):
        if not phrase:
            continue
        matches.append(
            db.execute(
                f"SELECT checksum, start, stop FROM context_spans WHERE {where} "
                f"ORDER BY {order}, checksum, start LIMIT ?",
                (f'"{phrase}"', *args, stream_limit),
            ).fetchall()
        )
    if coherent:
        return _coherent_order(matches)
    # Round-robin across explicit terms and preserve the existing date preference.
    streams = [iter(rows) for rows in matches]
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


def _positions(segments: list[dict], start: int, stop: int, neighbors: int, phrases: tuple):
    window = range(max(0, start - neighbors), min(len(segments), stop + neighbors))
    if phrases:
        hits = []
        for pos in range(start, stop):
            text = f" {_tokens(segments[pos]['text'])} "
            if any(phrase in text for phrase in phrases):
                hits.append(pos)
        if hits:
            return sorted(
                (pos for pos in window if any(abs(pos - hit) <= neighbors for hit in hits)),
                key=lambda pos: (min(abs(pos - hit) for hit in hits), pos),
            )
    return [*range(start, stop), *(pos for pos in window if not start <= pos < stop)]


def _pack(workspace, rows: list, limit: int, max_bytes: int, neighbors: int, phrases: tuple):
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
        chosen = []
        for pos in _positions(segments, start, stop, neighbors, phrases):
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


def context_pack(
    workspace, plan: dict, *, limit: int, max_bytes: int, neighbors: int, focus: bool = False
) -> dict:
    validate(_CONTEXT_SCHEMA, plan, "context plan")
    if not 1 <= limit <= 50 or not 512 <= max_bytes <= 65536 or not 0 <= neighbors <= 5:
        raise ToolError(
            "invalid_input", "context needs limit 1–50, max bytes 512–65536, neighbors 0–5"
        )
    if not any(map(_tokens, plan["terms"])):
        raise ToolError("invalid_input", "context needs an indexable term")
    version = _require_context(workspace)
    candidates = _candidates(workspace.db, plan, limit, focus=focus)
    phrases = (
        tuple(f" {tokens} " for term in plan["terms"] if (tokens := _tokens(term))) if focus else ()
    )
    spans, size, omitted = _pack(workspace, candidates, limit, max_bytes, neighbors, phrases)
    return {
        "memory_version": version,
        "plan": plan,
        "spans": spans,
        "quote_bytes": size,
        "quote_budget_bytes": max_bytes,
        "lines_over_budget": omitted,
        "candidate_spans": len(candidates),
        "candidate_sources": len({row["checksum"] for row in candidates}),
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
