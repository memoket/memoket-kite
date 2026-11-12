"""Search and pack original conversation spans for a host agent."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass, replace
from itertools import islice
from math import log

from .contracts import DATE, TEXT, ToolError, array, encode, object_schema, utc_timestamp, validate
from .index import SPAN_FACET_AXES, revision_id, source_key
from .ingest import RECORDING_CLOCK_FIELDS, source_day
from .retrieve import _citation

_SPAN_CHARS = 320
_QUESTION_BYTES = 3800
_TERM_BYTES = 6000
_TRACE_CANDIDATES = 80
_TEXT_SOURCE_COPIES = 8
_FOLLOWUP_LINES = 3
_SELECTIVE_SPANS = 4
_CLAUSE_MATCH_LIMIT = 300
_CLAUSE_SPAN_LIMIT = 1200
_VERSION_CHANGE_SOURCE_LIMIT = 32
_VERSION_CHANGE_LINE_LIMIT = 16000
_RECORDING_DATES = ("start_date", "end_date")
_SEARCH_TERMS = re.compile(r"[\u3400-\u9fff]+|[^\W_]+", re.UNICODE)
_HAN = re.compile(r"([\u3400-\u9fff])")
_QUERY_WORDS = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+(?:\s+[a-z0-9]+)*", re.I)
_QUOTED_PHRASES = re.compile(r'「([^」]+)」|『([^』]+)』|“([^”]+)”|"([^"\n]+)"')
_NAME_WORDS = re.compile(r"\b[A-Z][A-Za-z0-9_-]{3,}\b")
_ACRONYMS = re.compile(r"\b[A-Z][A-Z0-9]{1,8}\b")
_ENGLISH_WORD = re.compile(r"[a-z]{4,}")
_VOWEL_GROUP = re.compile(r"[aeiou]+")
_FINAL_CONSONANT = re.compile(r"[^aeiou][aeiou][^aeiouwxy]$")
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
_SCHEDULE_QUESTION = re.compile(
    r"日期|哪天|哪日|排期|改期|延期|推迟|提前|"
    r"\b(?:dates?|deadlines?|schedule[ds]?|reschedule\w*|postpon\w*|which day|what day)\b",
    re.I,
)
_SCHEDULE_SEQUENCE = re.compile(
    r"最初|原定|原本|早先|后来|后续|从什么|从哪|"
    r"\b(?:original\w*|initial\w*|earlier|later|subsequently|previous\w*)\b",
    re.I,
)
_CALENDAR_DAY = re.compile(
    r"(?<!\d)(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|"
    r"(?:1[0-2]|0?[1-9])\s*月\s*(?:3[01]|[12][0-9]|0?[1-9]))(?!\d)|"
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+(?:3[01]|[12][0-9]|0?[1-9])\b",
    re.I,
)
_SCHEDULE_CHANGE = re.compile(
    r"改成|改到|改上|改了|改为|推迟|延期|延后|提前到|挪到|调整到|重新定|"
    r"\b(?:reschedul\w*|postpon\w*|bring forward|brought forward|"
    r"mov(?:e|ed|ing) to|chang(?:e|ed|ing) to|push(?:ed|ing)? back)\b",
    re.I,
)
_CLAUSE_BREAK = re.compile(r"[?？。！!;；]")
_EARLIER_PHASE = re.compile(
    r"原定|原计划|原先|原本|原来|最初|早期|早先|"
    r"\b(?:original(?:ly)?|initial(?:ly)?|earlier|previous(?:ly)?)\b",
    re.I,
)
_LATER_PHASE = re.compile(
    r"后来|后续|之后|随后|延期后|改期后|调整后|现在改|"
    r"\b(?:later|subsequent(?:ly)?|afterwards?|revised|updated|now)\b",
    re.I,
)
_PHASE_BREAK = re.compile(
    r"[?？。！!;；]"
    r"|[，,、]\s*(?:以及|并说明|和)?\s*(?=原先|原本|后来|后续|之后|随后|现在改|是否|有没有)"
    r"|[，,、]\s*(?:以及|并说明)\s*"
    r"|\s*(?:以及|和|到|而)(?=后来|后续|之后|随后|延期后|改期后|调整后|现在改|是否|有没有)"
    r"|(?:,\s*|\s+)\b(?:and|then|to|through)\s+"
    r"(?=(?:(?:the|a|an)\s+)?(?:later|subsequent|afterwards|revised|updated|whether|now)\b)",
    re.I,
)
_PHASE_DATE_SUFFIX = re.compile(
    r"(?:日|日期|时间|时段|节点|期限)$|\b(?:date|deadline|timing)$", re.I
)
_CLAUSE_FILLERS = re.compile(
    r"原定|原本|原先|之后|然后|团队|我们|何时|开始|准备|采用|打算|"
    r"怎么样|怎么|如何|什么时候|什么时间|最初|早先|此前|后来|后续|什么|哪个|哪天|哪种|"
    r"哪几|多少|分别|梳理|有没有|已经|为什么|是否|当时|刚|这个|那个|的|了|吗|呢|"
    r"\b(?:initially|later|earlier|then|previously|originally|what|when|where|why|how|did|"
    r"was|were|has|have|had|the|from|to|and|at|on|or|for|by|of|in)\b",
    re.I,
)
_CLAUSE_DATE_QUESTION = re.compile(
    r"哪天|日期|什么时候|何时|改期|延期|推迟|哪日|哪一日|\b(?:date|when|deadline)\b", re.I
)
_CLAUSE_DATE = re.compile(
    rf"(?:{_CALENDAR_DAY.pattern})|[0-9]{{1,2}}\s*月|[一二三四五六七八九十]{{1,3}}月",
    re.I,
)
_CLAUSE_TIME = re.compile(
    r"\d{1,4}\s*[日号点周季]|今天|明天|昨天|上周|下周|这周|月底|本月|"
    r"\b(?:today|tomorrow|yesterday|last week|next week)\b",
    re.I,
)
_CLAUSE_DECISION = re.compile(
    r"提前|延期|推迟|延后|改成|改到|改了|调整|挪到|重排|重新排|不变|没有改变|保留|"
    r"一起|确定|决定|就按|锁[定个]|不用|还是|再选|再定|备选|同时|"
    r"\b(?:reschedul\w*|delay\w*|postpon\w*|revis\w*|chang\w*|decid\w*|approv\w*|"
    r"confirm\w*|switch\w*|retain\w*|replac\w*|withdraw\w*|tentative|instead|unchanged|keep)\b",
    re.I,
)
_CLAUSE_CHANGE_QUESTION = re.compile(
    rf"{_CLAUSE_DECISION.pattern}|变更|更改|改期|改锁|改用|切换|定了|选定|改为|批准|重新安排",
    re.I,
)
_PHASE_STATE = re.compile(
    rf"{_CLAUSE_DECISION.pattern}|暂停|恢复|取消|停产|停线|"
    r"\b(?:paus\w*|resum\w*|cancel\w*|suspend\w*|halt\w*)\b",
    re.I,
)
_DECISION_STATUS = re.compile(
    rf"{_SCHEDULE_CHANGE.pattern}|确认|批准|通过|否决|暂停|恢复|取消|停产|停线|暂缓|终止|"
    r"\b(?:approv\w*|confirm\w*|reject\w*|paus\w*|resum\w*|cancel\w*|suspend\w*|halt\w*|deferr\w*)\b",
    re.I,
)
_ROLE_QUESTION = re.compile(
    r"分工|各自负责|谁负责|主备|主用|备用|\b(?:roles?|responsibilit\w*|primary|backup|fallback)\b",
    re.I,
)
_ROLE_STATEMENT = re.compile(
    r"主线|主用|优先|负责|备用|备选|兜底|保留|并行|同时|接手|"
    r"\b(?:primary|backup|fallback|parallel|retain\w*|own\w*|lead\w*|handl\w*)\b",
    re.I,
)
_HYPOTHETICAL_DATE = re.compile(
    r"假设|举例|比如|例如|\b(?:suppose|hypothetical|for example|for instance)\b", re.I
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
        "explain": {"type": "boolean"},
        "at_version": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1},
        "known_at": {"type": "string", "format": "date-time"},
    },
)
CONTEXT_REQUEST = {
    **object_schema(
        {},
        {
            **_CONTEXT_SCHEMA["properties"],
            "question": {**TEXT, "maxLength": 512},
            "compare_with": {**TEXT, "maxLength": 512},
            "compare_start_date": DATE,
            "compare_end_date": DATE,
            "from_version": _CONTEXT_SCHEMA["properties"]["at_version"],
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "max_bytes": {"type": "integer", "minimum": 512, "maximum": 65536},
            "neighbors": {"type": "integer", "minimum": 0, "maximum": 5},
        },
    ),
    "oneOf": [{"required": ["question"]}, {"required": ["terms"]}],
}
_WORD_FORMS = array(object_schema({"query_term": TEXT, "indexed_term": TEXT}), 1, 8)
_CHANGE_PHASES = array(TEXT, 2, 4)
CONTEXT_PLAN = object_schema(
    {
        "request": CONTEXT_REQUEST,
        "terms": _CONTEXT_SCHEMA["properties"]["terms"],
        "memory_version": {"type": "integer"},
    },
    {
        "comparison_terms": array(TEXT, 1, 8),
        "word_forms": _WORD_FORMS,
        "comparison_word_forms": _WORD_FORMS,
        "change_phases": _CHANGE_PHASES,
        "comparison_change_phases": _CHANGE_PHASES,
    },
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


def _english_word_forms(word: str) -> list[str]:
    if not _ENGLISH_WORD.fullmatch(word):
        return []
    bases = {word}
    if word.endswith(("ied", "ies")):
        bases.add(word[:-3] + "y")
    if word.endswith("es"):
        bases.add(word[:-2])
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        bases.add(word[:-1])
    for suffix in ("ed", "ing"):
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            bases.update((stem, stem + "e"))
            if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
                bases.add(stem[:-1])

    forms = set()
    for base in bases:
        if len(base) < 3:
            continue
        forms.add(base)
        if base.endswith("y") and base[-2] not in "aeiou":
            forms.update((base[:-1] + "ies", base[:-1] + "ied"))
        else:
            plural = "es" if base.endswith(("s", "x", "z", "ch", "sh")) else "s"
            forms.update((base + plural, base + ("d" if base.endswith("e") else "ed")))
        ing_base = (
            base[:-1] if base.endswith("e") and not base.endswith(("ee", "ye", "oe")) else base
        )
        forms.add(ing_base + "ing")
        if _FINAL_CONSONANT.search(base) and len(_VOWEL_GROUP.findall(base)) == 1:
            forms.update((base + base[-1] + "ed", base + base[-1] + "ing"))
    return sorted(forms - {word})


def _indexed_word_form(db, where, args, word: str) -> tuple[int, str] | None:
    # Confirm generated forms against the scoped original text before using them.
    matches = []
    for form in _english_word_forms(word):
        if len(form) > 128:
            continue
        frequency = db.execute(
            f"SELECT count(*) FROM (SELECT rowid FROM context_spans WHERE {where} LIMIT 32)",
            (f'text : "{form}"', *args),
        ).fetchone()[0]
        if frequency:
            matches.append((frequency, form))
    return min(matches, default=None)


def _repeated_han_terms(question: str, phrases) -> dict[str, int]:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return {
        phrase: count - 1
        for phrase in phrases
        if 2 <= len(phrase) <= 7
        and _HAN.fullmatch(phrase[0])
        and (count := normalized.count(phrase)) > 1
    }


def _compile_terms(db, plan: dict, question: str) -> tuple[list[str], list[dict]]:
    where, args = _search_scope(plan)
    quoted = _quoted_phrases(question)
    if plan.get("matching_lines") and quoted:
        return sorted(quoted)[:8], []
    acronyms = {name.casefold() for name in _ACRONYMS.findall(question)}
    literal_words = acronyms | {word for quote in quoted for word in _tokens(quote).split()}
    candidates = []
    for phrase in dict.fromkeys((*sorted(quoted), *_question_phrases(question))):
        if len(phrase) > 128:
            continue
        expression = f'"{_tokens(phrase)}"'
        text_expression = f"text : {expression}" if plan.get("matching_lines") else expression
        frequency = db.execute(
            f"SELECT count(*) FROM (SELECT rowid FROM context_spans WHERE {where} LIMIT 32)",
            (text_expression, *args),
        ).fetchone()[0]
        query_term = None
        if not frequency and not plan.get("matching_lines") and phrase not in literal_words:
            alternative = _indexed_word_form(db, where, args, phrase)
            if alternative:
                query_term = phrase
                frequency, phrase = alternative
                expression = f'"{phrase}"'
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
            candidates.append((score, phrase, query_term))
    repeats = (
        _repeated_han_terms(question, (phrase for _, phrase, _ in candidates))
        if not (quoted or plan.get("matching_lines"))
        else {}
    )

    def priority(row):
        score, phrase, form = row
        if phrase in repeats:
            # Keep a repeated short clue without letting it bury a more
            # specific indexed phrase that contains it.
            more_specific = any(
                phrase != other and phrase in other and score <= other_score
                for other_score, other, _ in candidates
            )
            score += (2 if more_specific else 4) * repeats[phrase]
        return -score, form is not None

    selected = []
    word_forms = []
    for _, phrase, query_term in sorted(candidates, key=priority):
        if not any(
            (phrase in current and repeats.get(phrase, 0) <= repeats.get(current, 0))
            or current in phrase
            for current in selected
        ):
            selected.append(phrase)
            if query_term is not None:
                word_forms.append({"query_term": query_term, "indexed_term": phrase})
        if len(selected) == 8:
            break
    return selected or [question[:128]], word_forms


def _question_scope(request: dict) -> tuple[dict, str]:
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


def _context_scope(request: dict) -> tuple[dict, str | None, str | None]:
    validate(CONTEXT_REQUEST, request, "context request")
    if _comparison_dates(request) and "compare_with" not in request:
        raise ToolError("invalid_input", "comparison recording dates require compare_with")
    if "terms" in request:
        if "compare_with" in request or "from_version" in request:
            raise ToolError("invalid_input", "a comparison requires a question")
        return request, None, None
    request, search = _question_scope(request)
    compare = request.get("compare_with")
    from_version = request.get("from_version")
    if compare is None and from_version is None:
        return request, search, None
    if request.get("limit", 8) < 2:
        raise ToolError("invalid_input", "a comparison needs at least two spans")
    if from_version is not None:
        if compare is not None:
            raise ToolError("invalid_input", "compare either workspace versions or questions")
        return request, search, search
    scoped, compare_search = _question_scope({**request, "question": compare})
    return {**scoped, "question": request["question"]}, search, compare_search


def _compile_questions(workspace, indexes, request, search, compare_search):
    index, comparison = indexes
    terms, forms = (
        (request["terms"], [])
        if search is None
        else _compile_terms(index.db, index.filters, search)
    )
    result = {
        "request": request,
        "terms": terms,
        "memory_version": workspace.version(),
    }
    if forms:
        result["word_forms"] = forms
    if phases := _compiled_change_phases(request, search):
        result["change_phases"] = phases
    if compare_search is not None:
        terms, forms = _compile_terms(comparison.db, comparison.filters, compare_search)
        result["comparison_terms"] = terms
        if forms:
            result["comparison_word_forms"] = forms
        if phases := _compiled_change_phases(request, compare_search):
            result["comparison_change_phases"] = phases
    return result


def compile_question(workspace, request: dict) -> dict:
    request, search, compare_search = _context_scope(request)
    with _comparison_indexes(workspace, request) as indexes:
        return _compile_questions(workspace, indexes, request, search, compare_search)


def question_pack(workspace, request: dict) -> dict:
    request, search, compare_search = _context_scope(request)
    with _comparison_indexes(workspace, request) as indexes:
        compiled = _compile_questions(workspace, indexes, request, search, compare_search)
        return _search_questions(workspace, compiled, indexes)


def search_question(workspace, compiled: dict) -> dict:
    if compiled["memory_version"] != workspace.version():
        raise ToolError("stale_snapshot", "memory changed; compile the question again")
    request = compiled["request"]
    with _comparison_indexes(workspace, request) as indexes:
        return _search_questions(workspace, compiled, indexes)


def _search_questions(workspace, compiled: dict, indexes) -> dict:
    request = compiled["request"]
    index, comparison = indexes
    filters = {key: request[key] for key in _CONTEXT_SCHEMA["properties"] if key in request}
    limit = request.get("limit", 8)
    question = request.get("question")
    budget = request.get("max_bytes", _QUESTION_BYTES if question is not None else _TERM_BYTES)
    compare_terms = compiled.get("comparison_terms")
    version_comparison = "from_version" in request
    if (compare_terms is not None) != ("compare_with" in request or version_comparison):
        raise ToolError("invalid_input", "compile both comparison questions before searching")

    def search(
        question, terms, span_limit, quote_budget, search_index, forms, phases, overrides=None
    ):
        plan = {**filters, **(overrides or {}), "terms": terms}
        if version_comparison:
            plan.pop("known_at", None)
            plan["at_version"] = search_index.history["version"]
        result = _context_evidence(
            workspace,
            plan,
            limit=span_limit,
            max_bytes=quote_budget,
            neighbors=request.get("neighbors", 0 if request.get("matching_lines") else 1),
            focus=question is not None,
            _index=search_index,
            _question=question,
            prioritize_first_lines=compare_terms is not None and "limit" not in request,
            word_forms=forms,
            change_phases=phases,
            reserve_change_phases=version_comparison,
            other_records=(
                (comparison if search_index is index else index).records
                if version_comparison
                else None
            ),
        )
        if question is not None:
            result["compilation"] = {"question": question, "terms": terms}
            if forms:
                result["compilation"]["word_forms"] = forms
            if phases:
                result["compilation"]["change_phases"] = phases
            if not (filters.get("matching_lines") or _quoted_phrases(question)):
                repeats = _repeated_han_terms(question, terms)
                if repeats:
                    result["compilation"]["repeated_terms"] = [
                        {"term": term, "occurrences": extra + 1} for term, extra in repeats.items()
                    ]
        return result

    forms = compiled.get("word_forms", ())
    phases = compiled.get("change_phases", ())
    if compare_terms is None:
        return search(question, compiled["terms"], limit, budget, index, forms, phases)
    first_limit, first_budget = (limit + 1) // 2, (budget + 1) // 2
    date_overrides = _comparison_dates(request)
    parts = [
        search(question, compiled["terms"], first_limit, first_budget, index, forms, phases),
        search(
            question if version_comparison else request["compare_with"],
            compare_terms,
            limit - first_limit,
            budget - first_budget,
            comparison,
            compiled.get("comparison_word_forms", ()),
            compiled.get("comparison_change_phases", ()),
            date_overrides,
        ),
    ]
    return {
        "memory_version": compiled["memory_version"],
        "quote_budget_bytes": budget,
        "quote_bytes": sum(part["quote_bytes"] for part in parts),
        "parts": parts,
        **({"compare_recording_overrides": date_overrides} if date_overrides else {}),
        **({"version_comparison": True} if version_comparison else {}),
        **(
            {"history": index.history}
            if index.history is not None and not version_comparison
            else {}
        ),
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
def _search_index(
    workspace, plan: dict, *, recording_windows: tuple[dict, ...] = (), history=None, previous=None
):
    if "at_version" not in plan and "known_at" not in plan:
        _require_context(workspace)
        yield _SearchIndex(workspace.db, plan)
        return
    _search_scope(plan)
    history = history if history is not None else _history_snapshot(workspace, plan)
    visible = workspace.records_at(
        history["version"], plan, reuse=previous.records if previous is not None else None
    )
    windows = [
        (window.get("start_date"), window.get("end_date"))
        for window in recording_windows or (plan,)
    ]
    if (None, None) not in windows:
        visible = [
            record
            for record in visible
            if (day := source_day(record["conversation"]))
            and any(
                (lower is None or lower <= day) and (upper is None or day <= upper)
                for lower, upper in windows
            )
        ]
    records = {record["checksum"]: record for record in visible}
    with closing(sqlite3.connect(":memory:")) as db:
        db.row_factory = sqlite3.Row
        # Historical queries admit only raw, tracked revisions. Current labels
        # and topic merges would introduce information unavailable at the cutoff.
        removed = previous.records.keys() - records.keys() if previous is not None else ()
        if previous is None or len(removed) > len(records):
            _create_context_table(db)
            added = records.values()
        else:
            # Start the second transient index from the first and replace only
            # the rows whose visibility changed between the two snapshots.
            previous.db.backup(db)
            if removed:
                db.execute(
                    "DELETE FROM context_spans WHERE checksum IN (SELECT value FROM json_each(?))",
                    (encode(sorted(removed)),),
                )
            added = (
                record for checksum, record in records.items() if checksum not in previous.records
            )
        db.executemany(
            "INSERT INTO context_spans VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            _index_rows(added, None),
        )
        db.commit()
        filters = {key: value for key, value in plan.items() if key != "conversation_id"}
        yield _SearchIndex(db, filters, history, records)


def _comparison_dates(request: dict) -> dict:
    return {
        key: request[f"compare_{key}"] for key in _RECORDING_DATES if f"compare_{key}" in request
    }


@contextmanager
def _comparison_indexes(workspace, request: dict):
    if "from_version" in request:
        after = (
            {**request, "at_version": workspace.version()}
            if "at_version" not in request and "known_at" not in request
            else request
        )
        before = {**after, "at_version": request["from_version"]}
        before.pop("known_at", None)
        history = _history_snapshot(workspace, after)
        if before["at_version"] >= history["version"]:
            raise ToolError(
                "invalid_input", "from version must precede the target workspace version"
            )
        with (
            _search_index(workspace, before) as first,
            _search_index(workspace, after, history=history, previous=first) as second,
        ):
            yield first, second
        return
    overrides = _comparison_dates(request)
    if not overrides:
        with _search_index(workspace, request) as index:
            yield index, index
        return
    comparison = {**request, **overrides}
    _search_scope(request)
    _search_scope(comparison)
    # The historical index holds both windows from the same snapshot. Each
    # search then restricts the indexed evidence to its own recording dates.
    with _search_index(workspace, request, recording_windows=(request, comparison)) as index:
        shared = {key: value for key, value in index.filters.items() if key not in _RECORDING_DATES}
        yield tuple(
            replace(
                index,
                filters={**shared, **{key: plan[key] for key in _RECORDING_DATES if key in plan}},
            )
            for plan in (request, comparison)
        )


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


def _prioritize_corroborated_passage(db, where, args, order, terms, ranked):
    if len(terms) < 3 or len(ranked) < 3:
        return ranked
    # A meeting can dominate separate term searches. Look for a passage
    # that joins the two leading clues with another clue in the original text.
    phrases = [f'"{term}"' for term in terms]
    expression = f"{phrases[0]} AND {phrases[1]} AND text : ({' OR '.join(phrases[2:])})"
    candidate = db.execute(
        f"SELECT checksum, start, stop, day, text FROM context_spans WHERE {where} "
        f"ORDER BY {order}, checksum, start LIMIT 1",
        (expression, *args),
    ).fetchone()
    if candidate is None or any(row["checksum"] == candidate["checksum"] for row in ranked[:2]):
        return ranked
    original = f" {candidate['text']} "
    original_clues = [term for term in terms[2:] if f" {term} " in original]
    if not original_clues:
        return ranked
    passage = {key: candidate[key] for key in ("checksum", "start", "stop", "day")}
    passage["corroborating_terms"] = [*terms[:2], *original_clues]
    return [
        passage,
        *(
            row
            for row in ranked
            if (row["checksum"], row["start"]) != (candidate["checksum"], candidate["start"])
        ),
    ]


def _candidates(
    db,
    plan: dict,
    limit: int,
    *,
    focus: bool = False,
    preferred_sources: set[str] | None = None,
    anchors: tuple[str, ...] = (),
    term_candidates: dict[str, int] | None = None,
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
    terms = list(dict.fromkeys(tokens for term in plan["terms"] if (tokens := _tokens(term))))
    for phrase in terms:
        expression = f'"{phrase}"'
        if matching_lines:
            expression = f"text : {expression}"
        rows = db.execute(
            f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} "
            f"ORDER BY {order}, checksum, start LIMIT ?",
            (expression, *args, stream_limit),
        ).fetchall()
        matches.append(rows)
        if term_candidates is not None:
            term_candidates[phrase] = len(rows)
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
            return (
                ranked
                if preferred_sources
                else _prioritize_corroborated_passage(db, where, args, order, terms, ranked)
            )
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
    repeated: dict[str, str],
):
    window = range(max(0, start - neighbors), min(len(segments), stop + neighbors))
    if phrases:
        hits = []
        exact = set()
        repeated_hit = False
        for pos in range(start, stop):
            text = f" {_tokens(segments[pos]['text'])} "
            if any(phrase in text for phrase in phrases):
                hits.append(pos)
                repeated_hit |= any(clue in text for clue in repeated)
                if any(anchor in text for anchor in anchors):
                    exact.add(pos)
        if matching_lines:
            return hits, None
        if hits:
            selected = sorted(
                (pos for pos in window if any(abs(pos - hit) <= neighbors for hit in hits)),
                key=lambda pos: (pos not in exact, min(abs(pos - hit) for hit in hits), pos),
            )
            if repeated_hit:
                for pos in range(stop, min(len(segments), stop + _FOLLOWUP_LINES)):
                    text = f" {_tokens(segments[pos]['text'])} "
                    clue = next((label for match, label in repeated.items() if match in text), None)
                    if clue is not None:
                        return [*hits, pos, *(i for i in selected if i not in hits and i != pos)], (
                            pos,
                            clue,
                        )
            return selected, None
    return [*range(start, stop), *(pos for pos in window if not start <= pos < stop)], None


def _promote_alternative(rows, row, reason, term, **details):
    promoted = {**dict(row), **details, "selection": {"reason": reason, "term": term}}
    key = (row["checksum"], row["start"])
    return [
        rows[0],
        promoted,
        *(item for item in rows[1:] if (item["checksum"], item["start"]) != key),
    ]


def _uncovered_clues(question, terms, leading_text):
    words = _tokens(question).split()
    occurrences = {}
    for term in terms:
        tokens = term.split()
        occurrences[term] = [
            (i, i + len(tokens))
            for i in range(len(words) - len(tokens) + 1)
            if words[i : i + len(tokens)] == tokens
        ]
    # Overlapping fragments such as "put the data" and "the data tracking"
    # can describe the same part of a question. Keep a slot for an independent
    # clue when the more specific wording is already present in the head.
    covered = [
        bounds
        for term, positions in occurrences.items()
        if any(f" {term} " in text for text in leading_text)
        for bounds in positions
    ]
    return [
        term
        for term, positions in occurrences.items()
        if not positions
        or not all(
            any(
                min(end, stop) - max(start, begin) >= min(2, end - start)
                and stop - begin >= end - start
                for begin, stop in covered
            )
            for start, end in positions
        )
    ]


def _balance_recordings(
    workspace, index, plan, question, rows, limit, max_bytes, neighbors, repeated
):
    records = {}

    def segments(row):
        checksum = row["checksum"]
        if checksum not in records:
            record = (
                index.records[checksum] if index.records is not None else workspace.record(checksum)
            )
            records[checksum] = record["conversation"]["segments"]
        original = records[checksum]
        if not 0 <= row["start"] < row["stop"] <= len(original):
            raise ToolError("integrity_error", "context index does not match its source revision")
        return original

    terms = {tokens: term for term in plan["terms"] if (tokens := _tokens(term))}
    phrases = tuple(f" {term} " for term in terms)
    if repeated and rows[0]["checksum"] == rows[1]["checksum"]:
        for row in rows[2:limit]:
            if row["checksum"] == rows[0]["checksum"]:
                continue
            _, followup = _positions(
                segments(row), row["start"], row["stop"], neighbors, phrases, (), False, repeated
            )
            if followup is not None:
                rows = _promote_alternative(rows, row, "other_recording_followup", followup[1])
                break

    leading = {row["checksum"] for row in rows[:2]}
    leading_text = [
        f" {_tokens(line['text'])} "
        for row in rows[:2]
        for line in segments(row)[row["start"] : row["stop"]]
    ]
    where, args = _search_scope(index.filters)
    best = None
    for term in _uncovered_clues(question, terms, leading_text):
        if len(term.replace(" ", "")) < 2:
            continue
        matches = index.db.execute(
            f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} "
            "ORDER BY bm25(context_spans, 1.0, 0.35, 1.2), day DESC, checksum, start LIMIT ?",
            (f'text : "{term}"', *args, _SELECTIVE_SPANS + 1),
        ).fetchall()
        if len(matches) > _SELECTIVE_SPANS:
            continue
        for row in matches:
            if row["checksum"] in leading:
                continue
            original = segments(row)
            hits = [
                pos
                for pos in range(row["start"], row["stop"])
                if f" {term} " in f" {_tokens(original[pos]['text'])} "
            ]
            size = sum(len(original[pos]["text"].encode("utf-8")) for pos in hits)
            score = (len(hits), -len(matches))
            if len(hits) >= 2 and size <= max_bytes // 8 and (best is None or score > best[0]):
                best = (score, row, terms[term], hits)
    if best is None:
        return rows
    _, row, term, hits = best
    return _promote_alternative(rows, row, "selective_original_clue", term, direct_positions=hits)


def _schedule_followup(workspace, index, plan, rows, limit, line_budget):
    records = {}

    def segments(row):
        checksum = row["checksum"]
        if checksum not in records:
            record = (
                index.records[checksum] if index.records is not None else workspace.record(checksum)
            )
            records[checksum] = record["conversation"]["segments"]
        return records[checksum]

    terms = set(map(_tokens, plan["terms"]))
    # A spoken recap can retain only the last part of a question phrase.
    # For example, "begin the review" may be shortened to "the review".
    clues = {
        clue
        for term in terms
        for clue in (term, " ".join(term.split()[-2:]))
        if len(clue.replace(" ", "")) >= 2
    }
    pivots = {}
    for row in rows[: min(limit, 5)]:
        if not row["day"]:
            continue
        for line in segments(row)[row["start"] : row["stop"]]:
            text = f" {_tokens(line['text'])} "
            if not any(f" {term} " in text for term in terms):
                continue
            for date in _CALENDAR_DAY.finditer(line["text"]):
                pivot = _tokens(date[0])
                if pivot not in pivots:
                    shared = tuple(clue for clue in clues if f" {clue} " in text)
                    pivots[pivot] = (date[0], row["day"], shared)
                if len(pivots) == 3:
                    break
            if len(pivots) == 3:
                break
        if len(pivots) == 3:
            break
    where, args = _search_scope(index.filters)
    for pivot, (display, day, shared) in pivots.items():
        matches = index.db.execute(
            f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} AND day > ? "
            "ORDER BY day DESC, checksum, start LIMIT 32",
            (f'text : "{pivot}"', *args, day),
        )
        for row in matches:
            original = segments(row)
            for pos in range(row["start"], row["stop"]):
                text = original[pos]["text"]
                if pivot not in {_tokens(date[0]) for date in _CALENDAR_DAY.finditer(text)}:
                    continue
                if not any(f" {clue} " in f" {_tokens(text)} " for clue in shared):
                    continue
                for changed in range(pos, min(pos + 3, len(original))):
                    statement = original[changed]["text"]
                    dates = {_tokens(date[0]) for date in _CALENDAR_DAY.finditer(statement)}
                    if (
                        dates - {pivot}
                        and _SCHEDULE_CHANGE.search(statement)
                        and len(statement.encode("utf-8")) <= line_budget
                    ):
                        return {
                            **dict(row),
                            "start": pos,
                            "stop": changed + 1,
                            "direct_positions": [changed],
                            "selection": {"reason": "dated_plan_followup", "term": display},
                        }
    return None


def _compiled_change_phases(request, question):
    if (
        not question
        or request.get("latest")
        or request.get("matching_lines")
        or question.count("?") + question.count("？") >= 2
        or _quoted_phrases(question)
    ):
        return []
    earlier = _EARLIER_PHASE.search(question)
    if not earlier or not _LATER_PHASE.search(question, earlier.end()):
        return []
    phases = list(
        dict.fromkeys(
            stripped
            for part in _PHASE_BREAK.split(question)
            if (stripped := part.strip(" \t\r\n，,、"))
        )
    )
    return phases if 2 <= len(phases) <= 4 else []


def _change_clause_candidates(
    workspace, index, clauses, slots, max_bytes, *, implicit=False, version_comparison=False
):
    clauses = list(dict.fromkeys(part.strip() for part in clauses if part.strip()))
    if not 2 <= len(clauses) <= min(4, slots):
        return []
    cleaned = [_CLAUSE_FILLERS.sub(" ", part) for part in clauses]
    words = [_tokens(part).split() for part in cleaned]
    terms = dict.fromkeys(
        _tokens(term)
        for part in cleaned
        for term in islice(dict.fromkeys(_question_phrases(part)), 96)
    )
    where, args = _search_scope(index.filters)
    matches = {}
    weights = {}
    for term in terms:
        rows = index.db.execute(
            f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} LIMIT ?",
            (f'text : "{term}"', *args, _CLAUSE_MATCH_LIMIT + 1),
        ).fetchall()
        if not 0 < len(rows) <= _CLAUSE_MATCH_LIMIT:
            continue
        if index.history is not None:
            rows.sort(key=lambda row: (row["checksum"], row["start"]))
        phrase = term.split()
        length = len(phrase)
        positions = [
            {
                offset
                for start in range(len(clause) - length + 1)
                if clause[start : start + length] == phrase
                for offset in range(start, start + length)
            }
            for clause in words
        ]
        weight = 1 + 0.28 * log(_CLAUSE_MATCH_LIMIT / len(rows))
        # Han matches cover at least two character tokens. Give a substantive
        # English word comparable weight in a mixed-language question.
        weights[term] = (2 * weight if _ENGLISH_WORD.fullmatch(term) else weight, positions)
        matches.update({(row["checksum"], row["start"]): row for row in rows})
        if len(matches) > _CLAUSE_SPAN_LIMIT:
            return []

    def coverage(hits):
        # Overlapping phrases cover the same question words only once.
        covered = [{} for _ in clauses]
        for term in hits:
            weight, positions = weights[term]
            for clause, offsets in zip(covered, positions, strict=True):
                for offset in offsets:
                    clause[offset] = max(clause.get(offset, 0), weight)
        return [sum(clause.values()) for clause in covered]

    records = {}
    line_terms = {}
    ranked = [[] for _ in clauses]
    line_budget = max_bytes // (2 if version_comparison else 4)
    decision = _PHASE_STATE if implicit else _CLAUSE_DECISION
    asks_date = [
        bool(
            _CLAUSE_DATE_QUESTION.search(clause) or (implicit and _PHASE_DATE_SUFFIX.search(clause))
        )
        for clause in clauses
    ]
    for row in matches.values():
        checksum, start, stop = row["checksum"], row["start"], row["stop"]
        if checksum not in records:
            record = (
                index.records[checksum] if index.records is not None else workspace.record(checksum)
            )
            records[checksum] = record["conversation"]["segments"]
        segments = records[checksum]
        for pos in range(max(0, start - 1), min(len(segments), stop + 1)):
            if (checksum, pos) not in line_terms:
                tokens = f" {_tokens(segments[pos]['text'])} "
                line_terms[checksum, pos] = {term for term in weights if f" {term} " in tokens}
        for pos in range(start, stop):
            text = segments[pos]["text"]
            size = len(text.encode("utf-8"))
            if size > line_budget:
                continue
            dates = min(2, len(set(_CLAUSE_DATE.findall(text))))
            changes = min(2, len(set(decision.findall(text))))
            if not (dates or changes or _CLAUSE_TIME.search(text)):
                continue
            direct = line_terms[checksum, pos]
            nearby = (
                direct
                | line_terms.get((checksum, pos - 1), set())
                | line_terms.get((checksum, pos + 1), set())
            )
            own, near = coverage(direct), coverage(nearby)
            for part, dated in enumerate(asks_date):
                if not near[part]:
                    continue
                score = own[part] + 0.45 * near[part] + 0.23 * max(own)
                score += (own[0] + 0.4 * near[0]) * (0.35 if part else 0.15)
                score += (2 * dates + 0.5 * changes) if dated else (2 * changes + 0.25 * dates)
                ranked[part].append((score - (0 if own[part] else 2), row, pos, size))

    selected = []
    seen = set()
    budget = max_bytes * 2 // 3 if version_comparison else max_bytes // 2
    for clause, options in zip(clauses, ranked, strict=True):
        for _, row, pos, size in sorted(options, key=lambda candidate: -candidate[0]):
            key = (row["checksum"], pos)
            if key in seen or size > budget:
                continue
            selected.append(
                {
                    **dict(row),
                    "start": pos,
                    "stop": pos + 1,
                    "direct_positions": [pos],
                    "selection": {
                        "reason": "change_phase" if implicit else "change_subquestion",
                        "term": clause,
                    },
                }
            )
            seen.add(key)
            budget -= size
            break
    return selected


def _phase_corroboration(index, phases, max_bytes, schedule_question):
    """Find one additional original line for a selected comparison phase."""
    where, scope = _search_scope(index.filters)
    for position, phase in enumerate(phases, 1):
        checksum = phase["checksum"]
        record = index.records[checksum]
        original = record["conversation"]["segments"][phase["start"]]["text"]
        normalized = f" {_tokens(original)} "
        term = phase["selection"]["term"]
        names = sorted({name.casefold() for name in _ACRONYMS.findall(term)})
        role_search = (
            len(names) == 2
            and _ROLE_QUESTION.search(term)
            and all(f" {name} " in normalized for name in names)
        )
        if role_search:
            expression = " AND ".join(f'text : "{name}"' for name in names)
            compare_source = "checksum=?"
            selection = {"reason": "role_corroboration", "term": term}
        elif schedule_question and _EARLIER_PHASE.search(term):
            dates = {_tokens(match[0]): match[0] for match in _CALENDAR_DAY.finditer(original)}
            clues = tuple(
                dict.fromkeys(
                    tokens
                    for clue in _question_phrases(_CLAUSE_FILLERS.sub(" ", term))
                    if (tokens := _tokens(clue)) and f" {tokens} " in normalized
                )
            )[:8]
            if len(dates) != 1 or not clues:
                continue
            date, display = next(iter(dates.items()))
            alternatives = " OR ".join(f'"{clue}"' for clue in clues)
            expression = f'text : "{date}" AND text : ({alternatives})'
            compare_source = "checksum<>?"
            selection = {"reason": "date_corroboration", "term": display}
        else:
            continue

        best = None
        best_score = None
        for row in index.db.execute(
            f"SELECT checksum, start, stop, day FROM context_spans WHERE {where} AND {compare_source} "
            "ORDER BY bm25(context_spans), checksum, start LIMIT 48",
            (expression, *scope, checksum),
        ):
            lines = index.records[row["checksum"]]["conversation"]["segments"]
            for pos in range(row["start"], row["stop"]):
                text = lines[pos]["text"]
                size = len(text.encode("utf-8"))
                if size > max_bytes // 3 or text == original:
                    continue
                tokens = f" {_tokens(text)} "
                if role_search:
                    roles = set(_ROLE_STATEMENT.findall(text.casefold()))
                    if not roles or not all(f" {name} " in tokens for name in names):
                        continue
                    score = (len(roles), -size)
                else:
                    mentioned = {_tokens(match[0]) for match in _CALENDAR_DAY.finditer(text)}
                    offsets = [tokens.find(f" {clue} ") for clue in clues if f" {clue} " in tokens]
                    date_offset = tokens.find(f" {date} ")
                    if date not in mentioned or date_offset < 0 or not offsets:
                        continue
                    distance = min(abs(date_offset - offset) for offset in offsets)
                    score = (not _HYPOTHETICAL_DATE.search(text), -len(mentioned), -distance, -size)
                if best_score is None or score > best_score:
                    best_score = score
                    best = {
                        **dict(row),
                        "start": pos,
                        "stop": pos + 1,
                        "direct_positions": [pos],
                        "selection": selection,
                    }
        if best is not None:
            return position, best
    return None


def _compact_sources(
    workspace, index: _SearchIndex, plan: dict, question: str, budget: int
) -> tuple[dict, dict]:
    quoted = {_tokens(phrase) for phrase in _quoted_phrases(question)}
    named = {_tokens(name) for name in _NAME_WORDS.findall(question)}
    where, args = _search_scope(index.filters)
    compact = {}
    copies = {}
    covered = set()
    # A short note can still be identified by name when several exports
    # preserve exactly the same text and recording day.
    max_sources = 1 + _TEXT_SOURCE_COPIES
    for phrase in dict.fromkeys(map(_tokens, plan["terms"])):
        if phrase not in quoted and phrase not in named:
            continue
        matches = index.db.execute(
            f"SELECT DISTINCT checksum FROM context_spans WHERE {where} LIMIT ?",
            (f'"{phrase}"', *args, max_sources + 1),
        ).fetchall()
        if not matches or len(matches) > max_sources:
            continue
        checksums = [row["checksum"] for row in matches]
        if index.history is not None:
            checksums.sort()
        if covered.intersection(checksums):
            continue
        checksum = checksums[0]
        record = (
            index.records[checksum] if index.records is not None else workspace.record(checksum)
        )
        conversation = record["conversation"]
        segments = conversation["segments"]
        if len(segments) > 12 or (len(matches) > 1 and len(segments) < 2):
            continue
        title = conversation.get("title", "")
        if segments[0]["text"].lstrip().startswith("# "):
            title += " " + segments[0]["text"]
        if phrase not in quoted and f" {phrase} " not in f" {_tokens(title)} ":
            continue
        size = sum(len(segment["text"].encode("utf-8")) for segment in segments)
        if size > budget:
            continue
        alternatives = [
            index.records[key] if index.records is not None else workspace.record(key)
            for key in checksums[1:]
        ]
        if any(
            other["conversation"]["source"] != conversation["source"]
            or source_day(other["conversation"]) != source_day(conversation)
            or other["conversation"]["segments"] != segments
            for other in alternatives
        ):
            continue
        compact[checksum] = record
        if alternatives:
            copies[checksum] = alternatives
        covered.update(checksums)
        budget -= size
    return compact, copies


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


def _quote_group(record, groups, members):
    conversation = record["conversation"]
    segments = conversation["segments"]
    checksum = record["checksum"]
    if len(segments) < 2:
        return checksum
    key = (
        conversation["source"],
        source_day(conversation),
        len(segments),
        segments[0]["text"],
        segments[-1]["text"],
    )
    # Compare the entire transcript, including segment IDs and offsets.
    # Keep separate source references and clocks even for identical text.
    for other in groups[key]:
        if segments == other["conversation"]["segments"]:
            group = other["checksum"]
            members[group].append(record)
            return group
    groups[key].append(record)
    members[checksum].append(record)
    return checksum


def _same_text_source(record):
    conversation = record["conversation"]
    return {
        "source": conversation["source"],
        "conversation_id": conversation["conversation_id"],
        "source_reference": conversation["metadata"]["source_reference"],
        "revision_checksum": record["checksum"],
        "source_revision_active": record["active"],
        "title": conversation.get("title", ""),
        "source_date": source_day(conversation),
        **{key: conversation[key] for key in RECORDING_CLOCK_FIELDS if key in conversation},
    }


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
    compact_copies: dict | None = None,
    trace: dict | None = None,
    prioritize_first_lines: bool = False,
    reuse_identical_text: bool = False,
    repeated: dict[str, str] | None = None,
):
    compact_sources = compact_sources or {}
    compact_copies = compact_copies or {}
    records = historical_records if historical_records is not None else dict(compact_sources)
    if compact_sources:
        covered = set(compact_sources)
        covered.update(copy["checksum"] for group in compact_copies.values() for copy in group)
        rows = [
            {
                "checksum": checksum,
                "start": 0,
                "stop": len(record["conversation"]["segments"]),
                "day": source_day(record["conversation"]),
            }
            for checksum, record in compact_sources.items()
        ] + [row for row in rows if row["checksum"] not in covered]
    traced = []
    if trace is not None:
        traced = [
            {
                "rank": rank,
                "revision_checksum": row["checksum"],
                "source_date": row["day"] or None,
                "start_offset": row["start"],
                "end_offset": row["stop"],
                "reason": "span_limit",
                "quoted_lines": 0,
                "lines_over_budget": 0,
                **(
                    {"corroborating_terms": row["corroborating_terms"]}
                    if isinstance(row, dict) and "corroborating_terms" in row
                    else {}
                ),
                **(
                    {"selection": row["selection"]}
                    if isinstance(row, dict) and "selection" in row
                    else {}
                ),
            }
            for rank, row in enumerate(rows[:_TRACE_CANDIDATES], 1)
        ]
        trace.update(total=len(rows), candidates=traced)
    seen = defaultdict(dict)
    groups = defaultdict(list)
    members = defaultdict(list)
    group_by_checksum = {}
    shared_groups = set()
    packed = []
    remaining = []
    size = omitted = 0
    anchored = tuple(f" {anchor} " for anchor in anchors)
    repeated = repeated or {}

    def take(checksum, segments, positions):
        nonlocal size, omitted
        chosen = []
        duplicate_lines = 0
        group = group_by_checksum.get(checksum, checksum)
        quoted = seen[group]
        for pos in positions:
            if pos in quoted:
                duplicate_lines += quoted[pos] != checksum
                continue
            weight = len(segments[pos]["text"].encode("utf-8"))
            if size + weight > max_bytes:
                omitted += 1
                continue
            size += weight
            chosen.append(pos)
            quoted[pos] = checksum
        if duplicate_lines:
            shared_groups.add(group)
        return chosen, duplicate_lines

    for index, row in enumerate(rows):
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
        if reuse_identical_text and checksum not in group_by_checksum:
            group_by_checksum[checksum] = _quote_group(record, groups, members)
        previously_omitted = omitted
        positions, followup = (
            (range(start, stop), None)
            if checksum in compact_sources
            else _positions(
                segments, start, stop, neighbors, phrases, anchored, matching_lines, repeated
            )
        )
        if isinstance(row, dict) and "direct_positions" in row:
            positions, followup = row["direct_positions"], None
        first_positions = positions
        if prioritize_first_lines:
            first = next((pos for pos in positions if start <= pos < stop), None)
            first_positions = () if first is None else (first,)
        chosen, duplicates = take(checksum, segments, first_positions)
        if index < len(traced):
            over_budget = omitted - previously_omitted
            reason = "already_quoted" if first_positions else "no_matching_line"
            if chosen:
                reason = "quoted"
            elif over_budget:
                reason = "quote_budget"
            elif duplicates:
                reason = "duplicate_text"
            traced[index].update(
                reason=reason, quoted_lines=len(chosen), lines_over_budget=over_budget
            )
            if duplicates:
                traced[index]["duplicate_lines"] = duplicates
        if chosen:
            packed.append((record, chosen, followup))
            if prioritize_first_lines:
                remaining.append((index, checksum, segments, positions, chosen))
    for index, checksum, segments, positions, chosen in remaining:
        previously_omitted = omitted
        extra, duplicates = take(checksum, segments, positions)
        chosen.extend(extra)
        if index < len(traced):
            traced[index]["quoted_lines"] = len(chosen)
            traced[index]["lines_over_budget"] += omitted - previously_omitted
            if duplicates:
                traced[index]["duplicate_lines"] = (
                    traced[index].get("duplicate_lines", 0) + duplicates
                )
    spans = []
    for record, chosen, followup in packed:
        conversation = record["conversation"]
        segments = conversation["segments"]
        revision = revision_id(record)
        group = group_by_checksum.get(record["checksum"])
        copies = list(compact_copies.get(record["checksum"], ()))
        if group in shared_groups:
            copies.extend(
                other for other in members[group] if other["checksum"] != record["checksum"]
            )
        followup_quote = {}
        if followup is not None and followup[0] in chosen:
            pos, clue = followup
            followup_quote = {
                "followup_quote": {"source_line_id": f"{revision}L{pos + 1}", "term": clue}
            }
        spans.append(
            {
                "source_date": source_day(conversation),
                "title": conversation.get("title", ""),
                **{key: conversation[key] for key in RECORDING_CLOCK_FIELDS if key in conversation},
                **(
                    {"source_revision_active": record["active"]}
                    if historical_records is not None
                    else {}
                ),
                **(
                    {"same_text_sources": [_same_text_source(other) for other in copies]}
                    if copies
                    else {}
                ),
                **followup_quote,
                "evidence": [
                    _citation(record, f"{revision}L{pos + 1}", segments[pos])
                    for pos in sorted(chosen)
                ],
            }
        )
    return spans, size, omitted


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


def _term_coverage(terms: list[str], candidates: dict[str, int], spans: list[dict]) -> list[dict]:
    quoted = [f" {_tokens(line['text'])} " for span in spans for line in span["evidence"]]
    coverage = {}
    for term in terms:
        phrase = _tokens(term)
        if phrase and phrase not in coverage:
            coverage[phrase] = {
                "term": term,
                "candidate_spans": candidates.get(phrase, 0),
                "quoted_lines": sum(f" {phrase} " in line for line in quoted),
            }
    return list(coverage.values())


def _trace_sources(workspace, trace: dict) -> None:
    checksums = tuple(dict.fromkeys(row["revision_checksum"] for row in trace["candidates"]))
    if not checksums:
        return
    sources = {
        row["checksum"]: {"source": row["source"], "conversation_id": row["conversation_id"]}
        for row in workspace.db.execute(
            f"SELECT checksum, source, conversation_id FROM revisions WHERE checksum IN ({','.join('?' for _ in checksums)})",
            checksums,
        )
    }
    if len(sources) != len(checksums):
        raise ToolError("integrity_error", "context candidate refers to an unavailable revision")
    for row in trace["candidates"]:
        row.update(sources[row["revision_checksum"]])


def _version_evidence(records, other_records, question, terms, max_bytes, compact):
    changed = records.keys() - other_records.keys()
    if not changed or len(changed) > _VERSION_CHANGE_SOURCE_LIMIT:
        return None
    sides = [records[checksum] for checksum in sorted(changed) if checksum not in compact]
    if (
        sum(len(record["conversation"]["segments"]) for record in sides)
        > _VERSION_CHANGE_LINE_LIMIT
    ):
        return None
    counterparts = {
        source_key(record["conversation"]): record["conversation"]["segments"]
        for checksum, record in other_records.items()
        if checksum not in records
    }
    head, *clues = [f" {value} " for term in terms if (value := _tokens(term))]
    phrases = (head, *clues)
    needs_clue = bool(clues and len(head.split()) == 1)
    asks_date = bool(_SCHEDULE_QUESTION.search(question))
    # A name can be punctuated or expanded in another recording. A partial
    # name only counts alongside a second clue in the selected line.
    components = [
        f" {word} "
        for word in head.split()
        if word not in _ENGLISH_FUNCTION_WORDS
        and (_ENGLISH_WORD.fullmatch(word) or re.fullmatch(r"\d+[a-z]+", word))
    ]
    best = None
    best_score = None
    for record in sides:
        conversation = record["conversation"]
        segments = conversation["segments"]
        previous = {
            line["segment_id"]: line["text"]
            for line in counterparts.get(source_key(conversation), ())
        }
        normalized = [f" {_tokens(line['text'])} " for line in segments]
        for pos, (line, text) in enumerate(zip(segments, normalized, strict=True)):
            if previous.get(line["segment_id"]) == line["text"]:
                continue
            matches = [phrase for phrase in phrases if phrase in text]
            if not matches or len(line["text"].encode("utf-8")) > max_bytes // 3:
                continue
            secondary = any(clue in matches for clue in clues)
            if needs_clue and not secondary:
                continue
            nearby = normalized[max(0, pos - 3) : pos + 4]
            anchor_window = normalized[max(0, pos - 6) : pos + 7] if secondary else nearby
            exact = any(head in neighbor for neighbor in anchor_window)
            partial = any(word in neighbor for word in components for neighbor in nearby)
            if not (exact or (partial and secondary)):
                continue
            score = (
                asks_date
                and bool(_CALENDAR_DAY.search(line["text"]) or _CLAUSE_TIME.search(line["text"])),
                bool(_DECISION_STATUS.search(line["text"])),
                head in matches,
                exact,
                len(matches),
                max(map(len, matches)),
            )
            if best_score is None or score > best_score:
                best_score = score
                best = {
                    "checksum": record["checksum"],
                    "start": pos,
                    "stop": pos + 1,
                    "day": source_day(conversation),
                    "direct_positions": [pos],
                    "selection": {"reason": "version_evidence", "term": terms[0]},
                }
    return best


def _context_evidence(
    workspace,
    plan,
    *,
    limit,
    max_bytes,
    neighbors,
    focus,
    _index,
    _question,
    prioritize_first_lines=False,
    word_forms=(),
    change_phases=(),
    reserve_change_phases=False,
    other_records=None,
):
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
    trace = {} if plan.get("explain") else None
    with nullcontext(_index) if _index is not None else _search_index(workspace, plan) as index:
        compact, compact_copies = (
            _compact_sources(workspace, index, plan, _question, max_bytes // 2)
            if _question and neighbors and not plan.get("latest")
            else ({}, {})
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
            if not preferred:
                anchors = tuple(_tokens(form["indexed_term"]) for form in word_forms)
        term_candidates = {} if matching_lines else None
        candidates = _candidates(
            index.db,
            {**index.filters, "terms": plan["terms"]},
            limit,
            focus=focus,
            preferred_sources=preferred,
            anchors=anchors,
            term_candidates=term_candidates,
        )
        repeated = {}
        if (
            _question
            and not (anchors or matching_lines or plan.get("latest") or _quoted_phrases(_question))
            and _question.count("?") + _question.count("？") >= 2
        ):
            if neighbors:
                clues = _repeated_han_terms(_question, _question_phrases(_question))
                repeated = {
                    f" {_tokens(clue)} ": clue for clue in sorted(clues, key=len, reverse=True)
                }
            if not preferred and limit >= 2 and len(candidates) >= 3:
                candidates = _balance_recordings(
                    workspace,
                    index,
                    plan,
                    _question,
                    candidates,
                    limit,
                    max_bytes,
                    neighbors,
                    repeated,
                )
        reserved_clauses = []
        followup = None
        if (
            _question
            and neighbors
            and candidates
            and not (preferred or anchors or matching_lines or plan.get("latest"))
        ):
            sequence = _SCHEDULE_SEQUENCE.search(_question)
            if limit >= 2 and sequence and _SCHEDULE_QUESTION.search(_question):
                followup = _schedule_followup(
                    workspace,
                    index,
                    plan,
                    candidates,
                    limit,
                    max_bytes // (2 if reserve_change_phases else 4),
                )
                if followup:
                    candidates.insert(1, followup)
            first = 2 if followup else 1
            phase_slots = limit - (first if reserve_change_phases else 3)
            if _question.count("?") + _question.count("？") >= 2:
                if not followup and sequence and _CLAUSE_CHANGE_QUESTION.search(_question):
                    reserved_clauses = _change_clause_candidates(
                        workspace,
                        index,
                        _CLAUSE_BREAK.split(_question),
                        phase_slots,
                        max_bytes,
                        version_comparison=reserve_change_phases,
                    )
            elif change_phases:
                reserved_clauses = _change_clause_candidates(
                    workspace,
                    index,
                    change_phases,
                    phase_slots,
                    max_bytes,
                    implicit=True,
                    version_comparison=reserve_change_phases,
                )
            if reserve_change_phases and reserved_clauses:
                corroboration = _phase_corroboration(
                    index, reserved_clauses, max_bytes, _SCHEDULE_QUESTION.search(_question)
                )
                if corroboration is not None:
                    position, candidate = corroboration
                    reserved_clauses.insert(position, candidate)
            candidates[first:first] = reserved_clauses
            if reserve_change_phases and (followup or reserved_clauses):
                # Protect the selected original lines from a long, general
                # match at the front of this version's half of the budget.
                leading = candidates.pop(0)
                candidates.insert(first + len(reserved_clauses) - 1, leading)
        selected_version = None
        if other_records is not None and not (
            reserved_clauses or matching_lines or plan.get("latest")
        ):
            selected_version = _version_evidence(
                index.records, other_records, _question, plan["terms"], max_bytes, compact
            )
            if selected_version is not None:
                candidates.insert(1 if limit > 1 else 0, selected_version)
        first_lines = (
            prioritize_first_lines
            and neighbors > 0
            and not (plan.get("latest") or matching_lines or anchors or compact)
            and len(candidates) > limit
            and len({row["checksum"] for row in candidates[: limit + 1]}) == 1
        )
        if first_lines:
            # One recording occupies the front of this comparison branch.
            # Give another passage room before expanding neighboring lines.
            limit += 1
        # Compact version comparisons still give reserved original lines space
        # before expanding the leading candidate with its surrounding turns.
        first_lines = (
            first_lines
            or (reserve_change_phases and bool(followup or reserved_clauses))
            or selected_version is not None
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
            compact_copies=compact_copies,
            trace=trace,
            prioritize_first_lines=first_lines,
            reuse_identical_text=focus and not (matching_lines or plan.get("latest")),
            repeated=repeated,
        )
        if trace is not None:
            _trace_sources(workspace, trace)
            display_terms = {_tokens(term): term for term in plan["terms"]}
            for candidate in trace["candidates"]:
                if "corroborating_terms" in candidate:
                    candidate["corroborating_terms"] = [
                        display_terms[term] for term in candidate["corroborating_terms"]
                    ]
        version_quote = None
        phase_evidence = []
        if reserve_change_phases:
            quoted = {line["source_line_id"] for span in spans for line in span["evidence"]}
            for row in (selected_version, followup, *reserved_clauses):
                if row is None:
                    continue
                revision = revision_id(index.records[row["checksum"]])
                candidate_id = f"{revision}L{row['direct_positions'][0] + 1}"
                if candidate_id not in quoted:
                    continue
                if row is selected_version:
                    version_quote = candidate_id
                else:
                    phase_evidence.append({**row["selection"], "source_line_id": candidate_id})
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
        **({"first_lines_prioritized": True} if first_lines else {}),
        **({"version_evidence": version_quote} if version_quote else {}),
        **({"phase_evidence": phase_evidence} if phase_evidence else {}),
        **({"packing_trace": trace} if trace is not None else {}),
        **(
            {"term_coverage": _term_coverage(plan["terms"], term_candidates, spans)}
            if term_candidates is not None
            else {}
        ),
        **({"anchor_sources": len(preferred)} if preferred else {}),
        **({"compact_sources": min(len(compact), len(spans))} if compact else {}),
        **({"history": index.history} if index.history is not None else {}),
    }


def _render_copy(source, namespaces, history):
    identity = source["conversation_id"]
    if len(namespaces[identity]) > 1:
        identity = f"{source['source']}/{identity}"
    clock = source.get("local_started_at")
    when = (
        f"{clock.replace('T', ' ')} (timezone unknown)"
        if clock
        else source.get("started_at", source["source_date"])
    )
    if when:
        identity += f" ({when})"
    if history:
        identity += f" [{source['revision_checksum']}]"
    return identity


def render_context(pack: dict) -> str:
    namespaces = defaultdict(set)
    parts = pack.get("parts", [pack])
    for part in parts:
        for span in part["spans"]:
            for line in span["evidence"]:
                namespaces[line["conversation_id"]].add(line["source"])
            for source in span.get("same_text_sources", ()):
                namespaces[source["conversation_id"]].add(source["source"])
    lines = []
    version_comparison = pack.get("version_comparison")

    def add_history(history):
        lines.append(f"Workspace v{history['version']} · {history['committed_at']}")
        if history["later_deletions"]:
            lines.append(
                f"Later deletions: {history['later_deletions']}. Deleted evidence is unavailable."
            )

    history = pack.get("history")
    if history:
        add_history(history)
    first_quote = {}
    for number, part in enumerate(parts, 1):
        if version_comparison:
            history = part["history"]
            add_history(history)
        elif len(parts) > 1:
            lines.append(f"Question {number}")
        if pack.get("compare_recording_overrides"):
            scope = part["plan"]
            lines.append(
                f"Recording dates: {scope.get('start_date', 'any')} – {scope.get('end_date', 'any')}"
            )
        previous_revision = None
        shown_copy_groups = set()
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
            copies = span.get("same_text_sources", ())
            if copies:
                group = frozenset([revision, *(source["revision_checksum"] for source in copies)])
                if group not in shown_copy_groups:
                    shown_copy_groups.add(group)
                    locations = [
                        _render_copy(source, namespaces, history)
                        for source in copies[:_TEXT_SOURCE_COPIES]
                    ]
                    if len(copies) > _TEXT_SOURCE_COPIES:
                        locations.append(f"{len(copies) - _TEXT_SOURCE_COPIES} more in JSON")
                    lines.append("Same text and segment IDs in: " + ", ".join(locations))
            for line in span["evidence"]:
                ref = f"{line['conversation_id']}#{line['segment_id']}"
                if len(namespaces[line["conversation_id"]]) > 1:
                    ref = f"{line['source']}/{ref}"
                speaker = f" {line['speaker_id']}:" if "speaker_id" in line else ""
                quote = f"{speaker} {line['text']}"
                if len(parts) > 1:
                    if version_comparison:
                        key = (line["source"], line["source_reference"], ref, quote)
                        earlier = first_quote.setdefault(key, number)
                        reference = f" (same text in v{parts[earlier - 1]['history']['version']})"
                    else:
                        earlier = first_quote.setdefault(line["source_line_id"], number)
                        reference = f" (quoted in Question {earlier})"
                    if earlier < number and len(reference.encode()) < len(quote.encode()):
                        quote = reference
                lines.append(f"[{ref}]{quote}")
            lines.append("")
        if not part["spans"]:
            lines.append("No evidence matched.")
        coverage = part.get("term_coverage", ())
        if len(coverage) > 1:
            missing = [" ".join(row["term"].split()) for row in coverage if not row["quoted_lines"]]
            if missing:
                lines.append("No cited lines for: " + ", ".join(missing))
    if run_id := pack.get("saved_run"):
        lines.extend(("", f"Saved search: {run_id}"))
    return "\n".join(lines).strip()
