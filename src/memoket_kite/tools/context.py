"""Search and pack original conversation spans for a host agent."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass

from .contracts import DATE, TEXT, ToolError, array, object_schema, utc_timestamp, validate
from .index import SPAN_FACET_AXES, revision_id
from .ingest import RECORDING_CLOCK_FIELDS, source_day
from .retrieve import _citation

_SPAN_CHARS = 320
_QUESTION_BYTES = 3800
_SEARCH_TERMS = re.compile(r"[\u3400-\u9fff]+|[^\W_]+", re.UNICODE)
_HAN = re.compile(r"([\u3400-\u9fff])")
_QUERY_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+(?:\s+[a-z0-9]+)*", re.I)
_QUOTED_PHRASES = re.compile(r'「([^」]+)」|『([^』]+)』|“([^”]+)”|"([^"\n]+)"')
_NAME_WORDS = re.compile(r"\b[A-Z][A-Za-z0-9_-]{3,}\b")
_ACRONYMS = re.compile(r"\b[A-Z][A-Z0-9]{1,8}\b")
_QUESTION_TIME_PREFIX = (
    r"(?:\b(?:workspace|known)\s+(?:as of|at)\s+|工作区(?:记录)?\s*(?:在|截至)\s*)"
)
_ISO_DAY = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_QUESTION_TIME = re.compile(
    _QUESTION_TIME_PREFIX + rf"(?P<known_at>{_ISO_DAY}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2}))"
    r"(?![a-z0-9:+-])(?:\s*(?:时)?(?:能看到|已知|可见))?",
    re.I,
)
_QUESTION_DAY = re.compile(_QUESTION_TIME_PREFIX + _ISO_DAY, re.I)
_QUESTION_VERSION = re.compile(
    r"(?:\bworkspace\s+(?:(?:version|snapshot)\s+)?|工作区(?:版本|快照)?\s*)"
    r"v(?P<at_version>[0-9]{1,19})(?![a-z0-9]|\.[0-9])",
    re.I,
)
_QUERY_FILLERS = re.compile(
    r"(?:请问|根据|查找|查询|哪些|哪个|哪里|什么|如何|怎么|为什么|分别|"
    r"这场|那场|会上|那次|当时|原文|给出|引用|请|是否|有没有|的|了|吗|呢)"
)
_ENGLISH_FUNCTION_WORDS = frozenset(
    "a an and are as at be been being by can could did do does for from had has have how in is "
    "it its of on or that the their them these they this those to was were what when where which "
    "who why will with would you your".split()
)
_CONTEXT_SCHEMA = object_schema(
    {"terms": array({**TEXT, "maxLength": 128}, 1, 12)},
    {
        "source": TEXT,
        "conversation_id": TEXT,
        "start_date": DATE,
        "end_date": DATE,
        "latest": {"type": "boolean"},
        "matching_lines": {"type": "boolean"},
        "at_version": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1},
        "known_at": {"type": "string", "format": "date-time"},
    },
)
CONTEXT_REQUEST = object_schema(
    {"question": {**TEXT, "maxLength": 512}},
    {
        **{key: value for key, value in _CONTEXT_SCHEMA["properties"].items() if key != "terms"},
        "compare_with": {**TEXT, "maxLength": 512},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "max_bytes": {"type": "integer", "minimum": 512, "maximum": 65536},
        "neighbors": {"type": "integer", "minimum": 0, "maximum": 5},
    },
)
CONTEXT_PLAN = object_schema(
    {"request": CONTEXT_REQUEST, "terms": array(TEXT, 1, 8), "memory_version": {"type": "integer"}},
    {"comparison_terms": array(TEXT, 1, 8)},
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
                if 7 < len(fragment) <= 128:
                    yield fragment
                for start in range(len(fragment) - 1):
                    for width in range(2, min(7, len(fragment) - start) + 1):
                        yield fragment[start : start + width]
        else:
            words = block.split()
            for start, word in enumerate(words):
                if len(word) > 1 and not word.isdecimal() and word not in _ENGLISH_FUNCTION_WORDS:
                    yield word
                for width in (2, 3):
                    if start + width <= len(words):
                        yield " ".join(words[start : start + width])


def _quoted_phrases(question: str) -> set[str]:
    return {
        phrase.strip().casefold()
        for match in _QUOTED_PHRASES.finditer(question)
        for phrase in match.groups()
        if phrase is not None and len(phrase.strip()) <= 128 and _tokens(phrase)
    }


def _compile_terms(db, plan: dict, question: str) -> list[str]:
    where, args = _search_scope(plan)
    quoted = _quoted_phrases(question)
    if plan.get("matching_lines") and quoted:
        return sorted(quoted)[:8]
    acronyms = {name.casefold() for name in _ACRONYMS.findall(question)}
    candidates = []
    for phrase in dict.fromkeys((*sorted(quoted), *_question_phrases(question))):
        expression = f'"{_tokens(phrase)}"'
        text_expression = f"text : {expression}" if plan.get("matching_lines") else expression
        frequency = db.execute(
            f"SELECT count(*) FROM (SELECT rowid FROM context_spans WHERE {where} LIMIT 32)",
            (text_expression, *args),
        ).fetchone()[0]
        if frequency:
            labeled = db.execute(
                f"SELECT 1 FROM context_spans WHERE {where} LIMIT 1",
                (f"labels : {expression}", *args),
            ).fetchone()
            length = len(phrase.replace(" ", ""))
            score = min(length, 7) * (2 - frequency / 32) + (3 if labeled else 0)
            if phrase in quoted:
                score += 16
            elif phrase in acronyms:
                sources = db.execute(
                    f"SELECT count(*) FROM (SELECT DISTINCT checksum FROM context_spans WHERE {where} LIMIT 32)",
                    (text_expression, *args),
                ).fetchone()[0]
                if sources < 32:
                    score += 4
            candidates.append((score, phrase))
    selected = []
    for _, phrase in sorted(candidates, key=lambda row: -row[0]):
        if not any(phrase in current or current in phrase for current in selected):
            selected.append(phrase)
        if len(selected) == 8:
            break
    return selected or [question[:128]]


def _question_scope(request: dict) -> tuple[dict, str]:
    validate(CONTEXT_REQUEST, request, "context request")
    question = request["question"]
    quoted = [match.span() for match in _QUOTED_PHRASES.finditer(question)]

    def unquoted(pattern):
        return [
            match
            for match in pattern.finditer(question)
            if not any(start <= match.start() < stop for start, stop in quoted)
        ]

    timestamps = unquoted(_QUESTION_TIME)
    timestamp_starts = {match.start() for match in timestamps}
    if any(match.start() not in timestamp_starts for match in unquoted(_QUESTION_DAY)):
        raise ToolError("invalid_input", "use a workspace timestamp with a time and offset")
    selectors = sorted(
        [*timestamps, *unquoted(_QUESTION_VERSION)],
        key=lambda match: match.start(),
    )
    if not selectors:
        return request, question
    scope = {key: request[key] for key in ("known_at", "at_version") if key in request}
    parts = []
    offset = 0
    for match in selectors:
        parts.append(question[offset : match.start()])
        offset = match.end()
        if (timestamp := match.groupdict().get("known_at")) is not None:
            try:
                value = utc_timestamp(timestamp).isoformat(timespec="microseconds")
                existing = utc_timestamp(scope.get("known_at", value)).isoformat(
                    timespec="microseconds"
                )
            except (ValueError, OverflowError) as exc:
                raise ToolError("invalid_input", "invalid workspace snapshot timestamp") from exc
            if "at_version" in scope or existing != value:
                raise ToolError("invalid_input", "use one workspace snapshot per question")
            scope["known_at"] = value
        else:
            value = int(match["at_version"])
            if "known_at" in scope or scope.get("at_version", value) != value:
                raise ToolError("invalid_input", "use one workspace snapshot per question")
            scope["at_version"] = value
    parts.append(question[offset:])
    search = " ".join(parts).strip()
    if not _tokens(search):
        raise ToolError("invalid_input", "include a search question with the workspace snapshot")
    prepared = {**request, **scope}
    validate(CONTEXT_REQUEST, prepared, "context request")
    return prepared, search


def _comparison_scope(request: dict) -> tuple[dict, str, str | None]:
    request, search = _question_scope(request)
    compare = request.get("compare_with")
    if compare is None:
        return request, search, None
    if request.get("limit", 8) < 2:
        raise ToolError("invalid_input", "a comparison needs at least two spans")
    scoped, compare_search = _question_scope({**request, "question": compare})
    return {**scoped, "question": request["question"]}, search, compare_search


def _compile_questions(workspace, index, request, search, compare_search):
    result = {
        "request": request,
        "terms": _compile_terms(index.db, index.filters, search),
        "memory_version": workspace.version(),
    }
    if compare_search is not None:
        result["comparison_terms"] = _compile_terms(index.db, index.filters, compare_search)
    return result


def compile_question(workspace, request: dict) -> dict:
    request, search, compare_search = _comparison_scope(request)
    with _search_index(workspace, request) as index:
        return _compile_questions(workspace, index, request, search, compare_search)


def question_pack(workspace, request: dict) -> dict:
    request, search, compare_search = _comparison_scope(request)
    with _search_index(workspace, request) as index:
        compiled = _compile_questions(workspace, index, request, search, compare_search)
        return _search_questions(workspace, compiled, index)


def search_question(workspace, compiled: dict) -> dict:
    if compiled["memory_version"] != workspace.version():
        raise ToolError("stale_snapshot", "memory changed; compile the question again")
    request = compiled["request"]
    with _search_index(workspace, request) as index:
        return _search_questions(workspace, compiled, index)


def _search_questions(workspace, compiled: dict, index) -> dict:
    request = compiled["request"]
    filters = {key: request[key] for key in _CONTEXT_SCHEMA["properties"] if key in request}
    limit = request.get("limit", 8)
    budget = request.get("max_bytes", _QUESTION_BYTES)
    compare_terms = compiled.get("comparison_terms")
    if (compare_terms is not None) != ("compare_with" in request):
        raise ToolError("invalid_input", "compile both comparison questions before searching")

    def search(question, terms, span_limit, quote_budget):
        result = _context_evidence(
            workspace,
            {"terms": terms, **filters},
            limit=span_limit,
            max_bytes=quote_budget,
            neighbors=request.get("neighbors", 0 if request.get("matching_lines") else 1),
            focus=True,
            _index=index,
            _question=question,
        )
        return {**result, "compilation": {"question": question, "terms": terms}}

    if compare_terms is None:
        return search(request["question"], compiled["terms"], limit, budget)
    first_limit, first_budget = (limit + 1) // 2, (budget + 1) // 2
    parts = [
        search(request["question"], compiled["terms"], first_limit, first_budget),
        search(request["compare_with"], compare_terms, limit - first_limit, budget - first_budget),
    ]
    return {
        "memory_version": compiled["memory_version"],
        "quote_budget_bytes": budget,
        "quote_bytes": sum(part["quote_bytes"] for part in parts),
        "parts": parts,
        **({"history": index.history} if index.history is not None else {}),
    }


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


def _index_rows(records, vocab):
    for record in records:
        conversation = record["conversation"]
        segments = conversation["segments"]
        labels = _span_labels(record, vocab) if vocab is not None else {}
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


def _create_context_table(db) -> None:
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


def rebuild_context(db, records: list[dict], vocab, version: int) -> None:
    # Dropping the shadow tables also purges obsolete postings on older SQLite.
    db.execute("DROP TABLE IF EXISTS context_spans")
    _create_context_table(db)
    db.executemany(
        "INSERT INTO context_spans VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        _index_rows((record for record in records if record["active"]), vocab),
    )
    db.execute("DELETE FROM context_snapshot")
    db.execute("INSERT INTO context_snapshot VALUES(?)", (version,))


@dataclass
class _SearchIndex:
    db: sqlite3.Connection
    filters: dict
    history: dict | None = None
    records: dict | None = None


def _history_snapshot(workspace, plan: dict) -> dict:
    if "known_at" in plan and "at_version" in plan:
        raise ToolError("invalid_input", "choose a workspace version or known-at time")
    db = workspace.db
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_versions'").fetchone() is None:
        raise ToolError("history_unavailable", "run kite-tool integrity to begin tracking versions")
    if "at_version" in plan:
        row = db.execute(
            "SELECT * FROM memory_versions WHERE version=?", (plan["at_version"],)
        ).fetchone()
    else:
        cutoff = utc_timestamp(plan["known_at"]).isoformat(timespec="microseconds")
        row = db.execute(
            "SELECT * FROM memory_versions WHERE committed_at<=? ORDER BY version DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
        if row is not None:
            following = db.execute(
                "SELECT min(version) FROM memory_versions WHERE version>?", (row["version"],)
            ).fetchone()[0]
            if following is not None and following != row["version"] + 1:
                row = None
            elif following is None and workspace.version() != row["version"]:
                row = None
    if row is None:
        raise ToolError("history_unavailable", "no tracked workspace snapshot covers this cutoff")
    return {
        **dict(row),
        "retained_sources": db.execute(
            "SELECT count(*) FROM revision_visibility WHERE opened_version<=? "
            "AND (closed_version IS NULL OR closed_version>?)",
            (row["version"], row["version"]),
        ).fetchone()[0],
        "later_deletions": db.execute(
            "SELECT count(*) FROM deletion_audit WHERE version>?", (row["version"],)
        ).fetchone()[0],
    }


@contextmanager
def _search_index(workspace, plan: dict):
    if "at_version" not in plan and "known_at" not in plan:
        _require_context(workspace)
        yield _SearchIndex(workspace.db, plan)
        return
    _search_scope(plan)
    history = _history_snapshot(workspace, plan)
    visible = workspace.records_at(history["version"], plan)
    if "start_date" in plan or "end_date" in plan:
        lower, upper = plan.get("start_date", ""), plan.get("end_date", "9999-12-31")
        visible = [
            record
            for record in visible
            if (day := source_day(record["conversation"])) and lower <= day <= upper
        ]
    records = {record["checksum"]: record for record in visible}
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        _create_context_table(db)
        # Historical queries admit only raw, tracked revisions. Current labels
        # and topic merges would introduce information unavailable at the cutoff.
        db.executemany(
            "INSERT INTO context_spans VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            _index_rows(records.values(), None),
        )
        filters = {key: value for key, value in plan.items() if key != "conversation_id"}
        yield _SearchIndex(db, filters, history, records)


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


def _coherent_order(matches: list[list], preferred_sources: set[str] | None = None) -> list:
    # Accumulate a term's best rank once per conversation. This rewards several
    # clues pointing to the same meeting without boosting long meetings for length.
    span_scores = defaultdict(float)
    span_hits = defaultdict(int)
    source_scores = defaultdict(float)
    source_hits = defaultdict(int)
    candidates = {}
    for term_rank, rows in enumerate(matches):
        seen_sources = set()
        for rank, row in enumerate(rows):
            checksum = row["checksum"]
            key = (checksum, row["start"])
            candidates[key] = row
            score = 1 / ((1 + term_rank / 4) * (3 + rank))
            span_scores[key] += score
            span_hits[key] += 1
            if checksum not in seen_sources:
                source_scores[checksum] += score
                source_hits[checksum] += 1
                seen_sources.add(checksum)
    # A passage outside the named sources needs corroboration from a second term.
    eligible = (
        [key for key in candidates if key[0] in preferred_sources or span_hits[key] >= 2]
        if preferred_sources
        else candidates
    )
    ordered = sorted(
        eligible,
        key=lambda key: (
            -(
                span_scores[key]
                + 0.5 * source_scores[key[0]]
                + (span_hits[key] + source_hits[key[0]] - 2) / 40
            )
        ),
    )
    return [candidates[key] for key in ordered]


def _candidates(
    db,
    plan: dict,
    limit: int,
    *,
    focus: bool = False,
    preferred_sources: set[str] | None = None,
    anchors: tuple[str, ...] = (),
) -> list:
    where, args = _search_scope(plan)
    order = "bm25(context_spans, 1.0, 0.35, 1.2), day DESC"
    if plan.get("latest"):
        order = "day DESC, bm25(context_spans, 1.0, 0.35, 1.2)"
    matching_lines = plan.get("matching_lines", False)
    coherent = focus and not (plan.get("latest") or matching_lines)
    stream_limit = max(limit, min(limit * 6, 30)) if coherent else limit * 6
    matches = []
    exact = []
    for phrase in dict.fromkeys(map(_tokens, plan["terms"])):
        if not phrase:
            continue
        expression = f'"{phrase}"'
        if matching_lines:
            expression = f"text : {expression}"
        matches.append(
            db.execute(
                f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} "
                f"ORDER BY {order}, checksum, start LIMIT ?",
                (expression, *args, stream_limit),
            ).fetchall()
        )
        if phrase in anchors:
            row = db.execute(
                f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} "
                f"ORDER BY {order}, checksum, start LIMIT 1",
                (f'text : "{phrase}"', *args),
            ).fetchone()
            if row is not None:
                exact.append(row)
    if coherent:
        ranked = _coherent_order(matches, preferred_sources)
        if not exact:
            return ranked
        # Reserve a place for an explicit quote in original text before scoring
        # looser clues. Title and label matches still use the ordinary ranking.
        return list({(row["checksum"], row["start"]): row for row in (*exact, *ranked)}.values())
    # Give each term a turn before breaking ties within a recording date.
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
    return (
        sorted(results, key=lambda row: row["day"], reverse=True) if plan.get("latest") else results
    )


def _positions(
    segments: list[dict],
    start: int,
    stop: int,
    neighbors: int,
    phrases: tuple,
    anchors: tuple[str, ...],
    matching_lines: bool,
):
    window = range(max(0, start - neighbors), min(len(segments), stop + neighbors))
    if phrases:
        hits = []
        exact = set()
        for pos in range(start, stop):
            text = f" {_tokens(segments[pos]['text'])} "
            if any(phrase in text for phrase in phrases):
                hits.append(pos)
                if any(anchor in text for anchor in anchors):
                    exact.add(pos)
        if matching_lines:
            return hits
        if hits:
            return sorted(
                (pos for pos in window if any(abs(pos - hit) <= neighbors for hit in hits)),
                key=lambda pos: (pos not in exact, min(abs(pos - hit) for hit in hits), pos),
            )
    return [*range(start, stop), *(pos for pos in window if not start <= pos < stop)]


def _compact_sources(
    workspace, index: _SearchIndex, plan: dict, question: str, budget: int
) -> dict:
    quoted = {_tokens(phrase) for phrase in _quoted_phrases(question)}
    named = {_tokens(name) for name in _NAME_WORDS.findall(question)}
    where, args = _search_scope(index.filters)
    compact = {}
    for phrase in dict.fromkeys(map(_tokens, plan["terms"])):
        if phrase not in quoted and phrase not in named:
            continue
        matches = index.db.execute(
            f"SELECT DISTINCT checksum FROM context_spans WHERE {where} LIMIT 2",
            (f'"{phrase}"', *args),
        ).fetchall()
        if len(matches) != 1 or (checksum := matches[0]["checksum"]) in compact:
            continue
        record = (
            index.records[checksum] if index.records is not None else workspace.record(checksum)
        )
        conversation = record["conversation"]
        segments = conversation["segments"]
        if len(segments) > 12:
            continue
        title = conversation.get("title", "")
        if segments[0]["text"].lstrip().startswith("# "):
            title += " " + segments[0]["text"]
        if phrase not in quoted and f" {phrase} " not in f" {_tokens(title)} ":
            continue
        size = sum(len(segment["text"].encode("utf-8")) for segment in segments)
        if size <= budget:
            compact[checksum] = record
            budget -= size
    return compact


def _quoted_sources(index: _SearchIndex, question: str, terms: list[str], limit: int) -> set[str]:
    quoted = sorted(_quoted_phrases(question).intersection(terms))
    if not quoted:
        return set()
    where, args = _search_scope(index.filters)
    expression = " OR ".join(f'"{_tokens(phrase)}"' for phrase in quoted)
    rows = index.db.execute(
        f"SELECT DISTINCT checksum FROM context_spans WHERE {where} LIMIT ?",
        (expression, *args, limit + 1),
    ).fetchall()
    return {row["checksum"] for row in rows} if len(rows) <= limit else set()


def _pack(
    workspace,
    rows: list,
    limit: int,
    max_bytes: int,
    neighbors: int,
    phrases: tuple,
    *,
    anchors: tuple[str, ...] = (),
    matching_lines: bool = False,
    historical_records: dict | None = None,
    compact_sources: dict | None = None,
):
    compact_sources = compact_sources or {}
    records = historical_records if historical_records is not None else dict(compact_sources)
    if compact_sources:
        rows = [
            {"checksum": checksum, "start": 0, "stop": len(record["conversation"]["segments"])}
            for checksum, record in compact_sources.items()
        ] + [row for row in rows if row["checksum"] not in compact_sources]
    seen = defaultdict(set)
    packed = []
    size = omitted = 0
    anchored = tuple(f" {anchor} " for anchor in anchors)
    for row in rows:
        if len(packed) == limit:
            break
        checksum = row["checksum"]
        if historical_records is None and checksum not in records:
            records[checksum] = workspace.record(checksum)
        record = records.get(checksum)
        if record is None or (historical_records is None and not record["active"]):
            raise ToolError("integrity_error", "context index does not match its source revision")
        conversation = record["conversation"]
        segments = conversation["segments"]
        start, stop = row["start"], row["stop"]
        if not 0 <= start < stop <= len(segments):
            raise ToolError("integrity_error", "context index does not match its source revision")
        chosen = []
        positions = (
            range(start, stop)
            if checksum in compact_sources
            else _positions(segments, start, stop, neighbors, phrases, anchored, matching_lines)
        )
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
                    **{
                        key: conversation[key]
                        for key in RECORDING_CLOCK_FIELDS
                        if key in conversation
                    },
                    **(
                        {"source_revision_active": record["active"]}
                        if historical_records is not None
                        else {}
                    ),
                    "evidence": [
                        _citation(record, f"{revision_id(record)}L{pos + 1}", segments[pos])
                        for pos in sorted(chosen)
                    ],
                }
            )
    return packed, size, omitted


def context_pack(
    workspace,
    plan: dict,
    *,
    limit: int,
    max_bytes: int,
    neighbors: int,
    focus: bool = False,
    _index: _SearchIndex | None = None,
    _question: str | None = None,
) -> dict:
    if not 1 <= limit <= 50 or not 512 <= max_bytes <= 65536 or not 0 <= neighbors <= 5:
        raise ToolError(
            "invalid_input", "context needs limit 1–50, max bytes 512–65536, neighbors 0–5"
        )
    return _context_evidence(
        workspace,
        plan,
        limit=limit,
        max_bytes=max_bytes,
        neighbors=neighbors,
        focus=focus,
        _index=_index,
        _question=_question,
    )


def _context_evidence(workspace, plan, *, limit, max_bytes, neighbors, focus, _index, _question):
    validate(_CONTEXT_SCHEMA, plan, "context plan")
    if not any(map(_tokens, plan["terms"])):
        raise ToolError("invalid_input", "context needs an indexable term")
    matching_lines = plan.get("matching_lines", False)
    if matching_lines and neighbors:
        raise ToolError("invalid_input", "matching lines cannot include neighboring lines")
    phrases = (
        tuple(f" {tokens} " for term in plan["terms"] if (tokens := _tokens(term)))
        if focus or matching_lines
        else ()
    )
    with nullcontext(_index) if _index is not None else _search_index(workspace, plan) as index:
        compact = (
            _compact_sources(workspace, index, plan, _question, max_bytes // 2)
            if _question and neighbors and not plan.get("latest")
            else {}
        )
        preferred = set(compact)
        anchors = ()
        if _question and not (plan.get("latest") or matching_lines):
            quoted_sources = _quoted_sources(index, _question, plan["terms"], limit)
            if quoted_sources:
                preferred.update(quoted_sources)
                anchors = tuple(
                    map(_tokens, _quoted_phrases(_question).intersection(plan["terms"]))
                )
        candidates = _candidates(
            index.db,
            {**index.filters, "terms": plan["terms"]},
            limit,
            focus=focus,
            preferred_sources=preferred,
            anchors=anchors,
        )
        spans, size, omitted = _pack(
            workspace,
            candidates,
            limit,
            max_bytes,
            neighbors,
            phrases,
            anchors=anchors,
            matching_lines=matching_lines,
            historical_records=index.records,
            compact_sources=compact,
        )
    return {
        "memory_version": workspace.version(),
        "plan": plan,
        "spans": spans,
        "quote_bytes": size,
        "quote_budget_bytes": max_bytes,
        "lines_over_budget": omitted,
        "candidate_spans": len(candidates),
        "candidate_sources": len({row["checksum"] for row in candidates}),
        "span_limit": limit,
        **({"anchor_sources": len(preferred)} if preferred else {}),
        **({"compact_sources": min(len(compact), len(spans))} if compact else {}),
        **({"history": index.history} if index.history is not None else {}),
    }


def render_context(pack: dict) -> str:
    namespaces = defaultdict(set)
    parts = pack.get("parts", [pack])
    for part in parts:
        for span in part["spans"]:
            for line in span["evidence"]:
                namespaces[line["conversation_id"]].add(line["source"])
    lines = []
    if history := pack.get("history"):
        lines.append(f"Workspace v{history['version']} · {history['committed_at']}")
        if history["later_deletions"]:
            lines.append(
                f"Later deletions: {history['later_deletions']}. Deleted evidence is unavailable."
            )
    first_question = {}
    for number, part in enumerate(parts, 1):
        if len(parts) > 1:
            lines.append(f"Question {number}")
        previous_revision = None
        for span in part["spans"]:
            revision = span["evidence"][0]["revision_checksum"]
            if revision != previous_revision:
                if local := span.get("local_started_at"):
                    lines.append(
                        f"Recording started at: {local.replace('T', ' ')} (timezone unknown)"
                    )
                elif started := span.get("started_at"):
                    day = span["source_date"]
                    if started[:10] != day:
                        lines.append(f"Date: {day} UTC")
                    lines.append(f"Recording started at: {started}")
                elif span["source_date"]:
                    lines.append(f"Date: {span['source_date']}")
                if history:
                    lines.append(f"Revision: {revision}")
                previous_revision = revision
            for line in span["evidence"]:
                ref = f"{line['conversation_id']}#{line['segment_id']}"
                if len(namespaces[line["conversation_id"]]) > 1:
                    ref = f"{line['source']}/{ref}"
                speaker = f" {line['speaker_id']}:" if "speaker_id" in line else ""
                quote = f"{speaker} {line['text']}"
                if len(parts) > 1:
                    earlier = first_question.setdefault(line["source_line_id"], number)
                    reference = f" (quoted in Question {earlier})"
                    if earlier < number and len(reference.encode()) < len(quote.encode()):
                        quote = reference
                lines.append(f"[{ref}]{quote}")
            lines.append("")
        if not part["spans"]:
            lines.append("No evidence matched.")
    if run_id := pack.get("saved_run"):
        lines.extend(("", f"Saved search: {run_id}"))
    return "\n".join(lines).strip()
