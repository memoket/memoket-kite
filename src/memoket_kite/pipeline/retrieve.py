"""Retrieve evidence rows from a KITE Codebook.

The retrieval flow is:

1. compile and normalize a symbolic query plan;
2. execute the plan, with one optional repair;
3. run the deterministic lexical channel;
4. fuse both channels and select a bounded evidence set; and
5. add profile context for recency-sensitive questions.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field

from memoket_kite.core.algebra import execute_plan, fact_row, line_row
from memoket_kite.pipeline import ledger
from memoket_kite.pipeline.compile_plan import compile_plan
from memoket_kite.pipeline.patterns import advice_predicate as _advice_predicate
from memoket_kite.pipeline.render import spoken

_MONTHS = {
    month: index
    for index, month in enumerate(
        "january february march april may june july august september october "
        "november december".split(),
        1,
    )
}
_GREP_MODIFIERS = {
    "new",
    "old",
    "first",
    "last",
    "next",
    "more",
    "most",
    "many",
    "much",
    "some",
    "good",
    "best",
    "recent",
    "recently",
    "other",
    "another",
}
_DIRECTIONAL_TIME = re.compile(
    r"\b(after|before|since|until|between|ago|passed|lapsed|之前|之后|以来)\b",
    re.I,
)
_PLAN_SCORER_SCHEMA = "symbolic-execution-v1"


# ---------------------------------------------------------------------------
# Retrieval configuration and internal result


@dataclass(frozen=True)
class RetrievalOptions:
    """Profile settings that affect the standard retrieval flow."""

    enumeration_budget: int
    enumeration_unit_cap: int
    repair_plan: bool
    recency_anchor: int
    lexical_v2: bool
    pack_v2: bool
    date_channel: bool
    enum_basis: bool
    token_cap: int
    speaker_label: bool = False
    rank_stage_rows: bool = False
    bind_stage_dates: bool = True
    reserve_stage_units: bool = False

    @classmethod
    def from_profile(cls, profile) -> "RetrievalOptions":
        return cls(
            enumeration_budget=int(getattr(profile, "ENUM_BUDGET", 28)),
            enumeration_unit_cap=int(getattr(profile, "ENUM_UNIT_CAP", 3)),
            repair_plan=bool(getattr(profile, "PLAN_REPAIR", False)),
            recency_anchor=int(getattr(profile, "RECENCY_ANCHOR", 0) or 0),
            lexical_v2=bool(getattr(profile, "LEXICAL_V2", False)),
            pack_v2=bool(getattr(profile, "PACK_V2", False)),
            date_channel=bool(getattr(profile, "DATE_CHANNEL", False)),
            enum_basis=bool(getattr(profile, "ENUM_BASIS", False)),
            token_cap=_checked_token_cap(profile),
            speaker_label=bool(getattr(profile, "SPEAKER_LABEL", False)),
            rank_stage_rows=bool(getattr(profile, "RANK_STAGE_ROWS", False)),
            bind_stage_dates=bool(getattr(profile, "BIND_STAGE_DATES", True)),
            reserve_stage_units=bool(getattr(profile, "RESERVE_STAGE_UNITS", False)),
        )


@dataclass(frozen=True)
class QuestionAnchors:
    day: str = ""
    month: str = ""
    speakers: tuple[str, ...] = ()


@dataclass
class RetrievalResult:
    question: str
    plan: dict
    rows: list[dict]
    lexical_rows: list[dict]
    aggregates: list[dict]
    trace: list[dict]
    used_fallback: bool
    intent: str
    budget: int
    unit_cap: int
    candidate_rows: list[dict] = field(default_factory=list, repr=False, compare=False)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "plan": self.plan,
            "rows": self.rows,
            "used_fallback": self.used_fallback,
            "trace": self.trace,
        }


# ---------------------------------------------------------------------------
# Public retrieval flow


def retrieve(
    store,
    vocab,
    profile,
    question: str,
    *,
    model: str = "gpt-4.1-mini",
    reference_date: str = "",
    budget: int = 0,
    plan_cache: str | None = None,
) -> dict:
    """Compile and execute a retrieval request, returning selected rows."""

    return _run_retrieval(
        store,
        vocab,
        profile,
        question,
        model=model,
        reference_date=reference_date,
        budget=budget,
        plan_cache=plan_cache,
    ).as_dict()


def _run_retrieval(
    store,
    vocab,
    profile,
    question: str,
    *,
    model: str,
    reference_date: str = "",
    budget: int = 0,
    plan_cache: str | None = None,
) -> RetrievalResult:
    """Run native retrieval, with at most three explicitly opted-in compilations."""
    limit = int(getattr(profile, "COMPILE_MAX_ROUNDS", 1))
    if not 1 <= limit <= 3:
        raise ValueError("COMPILE_MAX_ROUNDS must be between 1 and 3")
    if limit > 1 and (int(getattr(profile, "COMPILE_CANDIDATES", 3)) != 1 or any(
            getattr(profile, key, False) for key in ("PLAN_REPAIR", "REFUSAL_REPLAN", "SECOND_PASS", "ROUND2"))):
        raise ValueError("bounded compile feedback requires one candidate and no extra replanning passes")
    current = _run_retrieval_once(store, vocab, profile, question, model=model,
                                  reference_date=reference_date, budget=budget, plan_cache=plan_cache)
    if limit == 1:
        return current
    history = [{"stage": "compile_round", "round": 1, "plan": deepcopy(current.plan),
                "retrieval_executed": True}] + [dict(e, compile_round=1) for e in current.trace]
    review_subject_scope = getattr(profile, "COMPILE_REVIEW_SUBJECT_SCOPE", False)
    retain_previous_candidates = getattr(profile, "COMPILE_RETAIN_PREVIOUS_CANDIDATES", False)
    # The whole-passage renderer delivers selected units as complete source text.
    evidence_types = {"fact", "line", "unit"} if review_subject_scope else {"fact", "line"}
    seen_rows = {(r["type"], r["id"]) for r in current.rows if r["type"] in evidence_types}
    seen_plans = {json.dumps(current.plan, sort_keys=True)}
    rounds = 1
    stop_reason = "round_limit"
    while rounds < limit:
        trigger = _compile_feedback_trigger(current)
        if not trigger and rounds == 1 and review_subject_scope:
            trigger = "review_subject_scope"
        if not trigger:
            stop_reason = "no_unresolved_plan_structure"
            break
        context = _compile_feedback_context(current, store, profile)
        rounds += 1
        try:
            plan = compile_plan(question, vocab, store, profile, model, n_candidates=1,
                                today=reference_date, evidence_context=context)
        except Exception as error:
            history.append({"stage": "compile_round", "round": rounds, "trigger": trigger,
                            "retrieval_executed": False, "error_type": type(error).__name__,
                            "error": str(error)})
            stop_reason = "compiler_error_previous_evidence_retained"
            break
        if plan == {"stop": True}:
            history.append({"stage": "compile_round", "round": rounds, "trigger": trigger,
                            "retrieval_executed": False, "stop": True})
            stop_reason = "compiler_stop"
            break
        key = json.dumps(plan, sort_keys=True)
        if key in seen_plans:
            history.append({"stage": "compile_round", "round": rounds, "trigger": trigger,
                            "retrieval_executed": False, "plan": deepcopy(plan)})
            stop_reason = "repeated_plan"
            break
        seen_plans.add(key)
        following = _run_retrieval_once(store, vocab, profile, question, model=model,
                                        reference_date=reference_date, budget=budget,
                                        plan_cache=plan_cache, compiled_plan=plan)
        history.append({"stage": "compile_round", "round": rounds, "trigger": trigger,
                        "retrieval_executed": True, "plan": deepcopy(following.plan)})
        history.extend(dict(e, compile_round=rounds) for e in following.trace)
        evidence_ids = {(r["type"], r["id"]) for r in following.rows if r["type"] in evidence_types}
        if not evidence_ids:
            stop_reason = "empty_retrieval_previous_pack_retained"
            break
        if retain_previous_candidates:
            # A revised plan owns the pack. Keep earlier candidates as fallback
            # without changing the revised plan's candidate order or selection.
            keys = {(row["type"], row["id"]) for row in following.candidate_rows}
            following.candidate_rows.extend(
                row for row in current.candidate_rows if (row["type"], row["id"]) not in keys
            )
        if not evidence_ids - seen_rows:
            # A narrower valid plan may remove distracting evidence without
            # discovering a new fact. Use that executed plan, but do not spend
            # another compiler call on an unchanged evidence universe.
            current = following
            stop_reason = "no_new_evidence"
            break
        seen_rows.update(evidence_ids)
        current = following
    history.append({"stage": "compile_feedback_stop", "reason": stop_reason,
                    "compile_rounds": rounds, "round_limit": limit,
                    "final_evidence_budget": current.budget})
    current.trace = history
    return current


def _compile_feedback_trigger(result: RetrievalResult) -> str:
    """Observable planning gaps; never inspect answers or benchmark labels."""
    for entry in result.trace:
        stage = str(entry.get("stage", ""))
        if (stage == "stage" and (entry.get("skipped") or not entry.get("hits"))) or (
                stage.endswith("refine") and entry.get("steps")):
            return "empty_stage_or_relaxed_filters"
    if not result.rows or not any(r["type"] in {"fact", "line"} for r in result.rows):
        return "no_property_evidence"
    nodes = result.plan.get("stages") or result.plan.get("queries") or []
    for node in nodes:
        where = node.get("where") or {}
        units = where.get("units", [])
        values = [units] if isinstance(units, str) else units
        if any(isinstance(v, str) and v.startswith("$") for v in values):
            return "dependent_document_scope_needs_chain_check"
        if (len(nodes) > 1 and node.get("select", "facts") == "facts"
                and not where.get("entities") and not where.get("units")):
            return "unanchored_complementary_property"
    return ""


def _compile_feedback_context(result: RetrievalResult, store, profile) -> str:
    renderer = getattr(profile, "COMPILE_FEEDBACK_RENDERER", None)
    if renderer is None:
        from memoket_kite.pipeline.answer import AnswerOptions, _render_compact_sources

        evidence = _render_compact_sources(result.rows, store, AnswerOptions.from_profile(profile))
    else:
        evidence = renderer(result.rows, store)
    outcomes = [e for e in result.trace if e.get("stage") == "stage"
                or str(e.get("stage", "")).endswith("refine")]
    return ("Previously executed plan:\n" + json.dumps(result.plan, ensure_ascii=False)
            + "\nNative execution outcomes:\n" + json.dumps(outcomes, ensure_ascii=False)
            + "\nActually retrieved evidence:\n" + "\n".join(evidence))


def _run_retrieval_once(
    store,
    vocab,
    profile,
    question: str,
    *,
    model: str,
    reference_date: str = "",
    budget: int = 0,
    plan_cache: str | None = None,
    compiled_plan: dict | None = None,
) -> RetrievalResult:
    """The unchanged native plan execution, lexical fusion and evidence packing."""

    options = RetrievalOptions.from_profile(profile)
    legal_kinds = set(getattr(profile, "KINDS", ()) or ())

    def scorer(candidate):
        return _score_plan(store, vocab, candidate, legal_kinds)

    scorer_cache_key = _plan_scorer_cache_key(legal_kinds)
    candidates = int(getattr(profile, "COMPILE_CANDIDATES", 3))
    if candidates < 1:
        raise ValueError("COMPILE_CANDIDATES must be positive")

    plan = deepcopy(compiled_plan) if compiled_plan is not None else compile_plan(
        question,
        vocab,
        store,
        profile,
        model,
        scorer=scorer if candidates > 1 else None,
        n_candidates=candidates,
        today=reference_date,
        cache_dir=plan_cache,
        scorer_cache_key=scorer_cache_key,
    )

    intent = plan.get("intent", "factual")
    anchors = _extract_question_anchors(question, store.speakers)
    _normalize_plan(plan, question, vocab, profile, anchors)

    if not budget:
        default_budget = int(getattr(profile, "DEFAULT_BUDGET", 20) or 20)
        budget = (
            max(options.enumeration_budget, default_budget)
            if intent in ("enumeration", "aggregate")
            else default_budget
        )
    unit_cap = options.enumeration_unit_cap if intent == "enumeration" else 5

    plan, plan_rows, trace = _execute_with_plan_repair(
        store,
        vocab,
        profile,
        question,
        plan,
        model=model,
        reference_date=reference_date,
        budget=budget,
        unit_cap=unit_cap,
        options=options,
        scorer=scorer,
        legal_kinds=legal_kinds,
        plan_cache=plan_cache,
        scorer_cache_key=scorer_cache_key,
        coverage=options.pack_v2 and intent == "enumeration",
    )
    lexical_rows = _retrieve_lexically(
        store, profile, question, cap=budget * 2, v2=options.lexical_v2
    )
    date_rows = _retrieve_by_date_window(store, anchors) if options.date_channel else []
    aggregates = [row for row in plan_rows if row["type"] == "count"]
    fused_rows = _fuse_retrieval_channels(
        plan_rows,
        lexical_rows,
        intent=intent,
        date_rows=date_rows,
    )
    if options.token_cap:
        rows = _select_evidence_rows_tokencap(
            plan_rows,
            fused_rows,
            store,
            cap=options.token_cap,
            unit_cap=unit_cap,
            row_cap=budget,
            speaker=options.speaker_label,
        )
    else:
        rows = _select_evidence_rows(
            plan_rows,
            fused_rows,
            budget=budget,
            unit_cap=unit_cap,
            pack_v2=options.pack_v2,
        )
    seen = {(row["type"], row["id"]) for row in rows}
    _add_recency_context(
        rows,
        seen,
        store,
        profile,
        question,
        intent,
        options.recency_anchor,
    )
    rows.sort(key=lambda row: (row.get("date") or "", row.get("id") or ""))
    if aggregates and aggregates[0].get("of") == "units":
        # A counted answer is only as good as the rows the reader can see, so
        # a pack that carries a unit count is trimmed to an inspectable basis.
        # The wider slice exists because the default cap can cut a pack below
        # the number of instances the question asks it to count.
        rows = rows[: 20 if options.enum_basis else 8]
    rows = aggregates + rows

    return RetrievalResult(
        question=question,
        plan=plan,
        rows=rows,
        lexical_rows=lexical_rows,
        aggregates=aggregates,
        trace=trace,
        used_fallback=not plan_rows and bool(lexical_rows),
        intent=intent,
        budget=budget,
        unit_cap=unit_cap,
        candidate_rows=fused_rows,
    )


# ---------------------------------------------------------------------------
# Question anchors, plan normalization, and execution


def _extract_question_anchors(question: str, speakers) -> QuestionAnchors:
    day = ""
    match = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日|"
        r"(\d{4})-(\d{2})-(\d{2})",
        question,
    )
    if match:
        parts = [part for part in match.groups() if part]
        day = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    else:
        day_first = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})",
            question,
        )
        month_first = re.search(
            r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
            question,
        )
        if day_first and day_first.group(2).lower() in _MONTHS:
            day = (
                f"{day_first.group(3)}-{_MONTHS[day_first.group(2).lower()]:02d}-"
                f"{int(day_first.group(1)):02d}"
            )
        elif month_first and month_first.group(1).lower() in _MONTHS:
            day = (
                f"{month_first.group(3)}-{_MONTHS[month_first.group(1).lower()]:02d}-"
                f"{int(month_first.group(2)):02d}"
            )

    month = ""
    month_match = re.search(r"\b([A-Za-z]+)\s+(\d{4})", question)
    if not day and month_match and month_match.group(1).lower() in _MONTHS:
        month = f"{month_match.group(2)}-{_MONTHS[month_match.group(1).lower()]:02d}"

    named_speakers = tuple(
        speaker
        for speaker in speakers
        if len(speaker) >= 3
        and speaker not in ("user", "assistant")
        and re.search(r"\b" + re.escape(speaker) + r"\b", question, re.I)
    )

    return QuestionAnchors(day, month, named_speakers)


def _normalize_plan(
    plan: dict,
    question: str,
    vocab,
    profile,
    anchors: QuestionAnchors,
) -> None:
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    direct = plan.get("queries") if isinstance(plan.get("queries"), list) else []
    queries = []
    for query in stages + direct:
        if not isinstance(query, dict):
            continue
        where = query.get("where", {})
        pipe = query.get("pipe", [])
        if not isinstance(where, dict) or not isinstance(pipe, list):
            continue
        if any(not isinstance(operation, dict) for operation in pipe):
            continue
        query["where"] = where
        query["pipe"] = pipe
        queries.append(query)
    plan_greps = [
        query.get("where", {}).get("grep", "")
        for query in queries
        if query.get("where", {}).get("grep")
    ]
    _normalize_speaker_filters(queries, anchors.speakers)
    _normalize_grep_filters(queries)
    _normalize_time_filters(queries, question, anchors)
    _normalize_anchor_queries(queries, plan_greps, vocab, profile, question)
    if plan.get("intent", "factual") == "enumeration":
        _normalize_enumeration_plan(queries)


def _normalize_speaker_filters(queries: list[dict], speakers: tuple[str, ...]) -> None:
    if not speakers:
        return
    for query in queries:
        where = query.setdefault("where", {})
        if query.get("select", "facts") == "facts" and not where.get("who"):
            where["who"] = list(speakers)


def _normalize_grep_filters(queries: list[dict]) -> None:
    for query in queries:
        where = query.get("where") or {}
        grep = where.get("grep", "")
        if not grep or "|" not in grep or re.search(r"[()\[\]?*+{]", grep):
            continue
        terms = grep.split("|")
        kept = [term for term in terms if term.strip().lower() not in _GREP_MODIFIERS]
        if kept and len(kept) < len(terms):
            where["grep"] = "|".join(kept)


def _normalize_time_filters(
    queries: list[dict],
    question: str,
    anchors: QuestionAnchors,
) -> None:
    for query in queries:
        where = query.setdefault("where", {})
        time_filter = where.get("time")
        # The compiler is allowed to emit a single-endpoint window — the
        # executor documents `["2023-05"]` as legal — so the missing end is
        # read as empty rather than indexed into.
        end = (time_filter[1] if time_filter and len(time_filter) > 1 else "") or ""
        if anchors.day and time_filter and time_filter[0] == anchors.day and not end.strip():
            where["time"] = [anchors.day, anchors.day]
        elif (
            anchors.day
            and not time_filter
            and query.get("select", "facts") != "units"
            and not _DIRECTIONAL_TIME.search(question)
        ):
            where["time"] = [anchors.day, anchors.day]
        elif (
            anchors.month
            and time_filter
            and time_filter[0].startswith(anchors.month)
            and not end.strip()
        ):
            where["time"] = [time_filter[0], anchors.month]


def _normalize_anchor_queries(
    queries: list[dict],
    plan_greps: list[str],
    vocab,
    profile,
    question: str,
) -> None:
    for query in queries:
        where = query.setdefault("where", {})
        heads = [
            operation.get("n", 99)
            for operation in query.get("pipe") or []
            if operation.get("op") == "head"
        ]
        if not (heads and min(heads) <= 2) or where.get("grep"):
            continue
        has_topic = any(
            vocab.resolve_topic(topic if isinstance(topic, str) else topic.get("code", ""))
            for topic in where.get("topics") or []
        )
        has_entity = any(vocab.resolve_entity(entity) for entity in where.get("entities") or [])
        if has_topic or has_entity:
            continue
        grep = plan_greps[0] if plan_greps else "|".join(profile.keywords(question)[:6])
        if grep:
            where["grep"] = grep


def _normalize_enumeration_plan(queries: list[dict]) -> None:
    for query in queries:
        query["pipe"] = [
            operation for operation in query.get("pipe") or [] if operation.get("op") != "count"
        ]


def _score_plan(store, vocab, plan: dict, legal_kinds: set[str]) -> float:
    try:
        rows, _ = execute_plan(
            store,
            vocab,
            plan,
            speakers=store.speakers,
            budget=12,
            unit_cap=4,
            legal_kinds=legal_kinds,
        )
    except Exception:
        # A plan that crashes the executor must lose to any plan that runs —
        # and must never be pinned by the plan cache (see compile_plan).
        return float("-inf")

    data_rows = [row for row in rows if row.get("type") != "count"]
    serialized = json.dumps(plan, ensure_ascii=False)
    if '"count"' in serialized:
        score = 0.0
        for query in (plan.get("queries") or []) + (plan.get("stages") or []):
            where = query.get("where") or {}
            time_filter = where.get("time") or ["", ""]
            score += 2.0 * bool(time_filter[0])
            score += 2.0 * bool(len(time_filter) > 1 and time_filter[1])
            score += bool(where.get("topics") or where.get("entities") or where.get("grep"))
        return score

    score = min(len(data_rows), 12)
    score += sum(row.get("score", 0) for row in data_rows[:5]) * 0.2
    score += 2.0 * any(field in serialized for field in ('"topics"', '"entities"', '"kind"'))
    score += 1.0 * ('"grep"' in serialized)
    if not data_rows and not any(row.get("type") == "count" for row in rows):
        score -= 5.0
    return score


def _plan_scorer_cache_key(legal_kinds: set[str]) -> str:
    return _PLAN_SCORER_SCHEMA + ":" + ",".join(sorted(legal_kinds))


def _execute_with_plan_repair(
    store,
    vocab,
    profile,
    question: str,
    plan: dict,
    *,
    model: str,
    reference_date: str,
    budget: int,
    unit_cap: int,
    options: RetrievalOptions,
    scorer,
    legal_kinds: set[str],
    plan_cache: str | None,
    scorer_cache_key: str,
    coverage: bool = False,
) -> tuple[dict, list[dict], list[dict]]:
    rows, trace = execute_plan(
        store,
        vocab,
        plan,
        speakers=store.speakers,
        budget=budget * 2,
        unit_cap=unit_cap + 2,
        legal_kinds=legal_kinds,
        pack_v2=options.pack_v2,
        coverage=coverage,
        rank_stage_rows=options.rank_stage_rows,
        bind_stage_dates=options.bind_stage_dates,
        reserve_stage_units=options.reserve_stage_units,
    )
    if rows or not options.repair_plan:
        return plan, rows, trace.stages

    feedback = (
        f"{question}\n(NOTE: a previous plan for this question matched ZERO "
        "rows — its filters were too narrow or its anchor keywords missed. "
        "Use different anchor keywords, broader codes, or the parallel shape.)"
    )
    repaired = compile_plan(
        feedback,
        vocab,
        store,
        profile,
        model=model,
        scorer=scorer,
        today=reference_date,
        cache_dir=plan_cache,
        scorer_cache_key=scorer_cache_key,
    )
    repaired_rows, repaired_trace = execute_plan(
        store,
        vocab,
        repaired,
        speakers=store.speakers,
        budget=budget * 2,
        unit_cap=unit_cap + 2,
        legal_kinds=legal_kinds,
        pack_v2=options.pack_v2,
        coverage=coverage,
        rank_stage_rows=options.rank_stage_rows,
        bind_stage_dates=options.bind_stage_dates,
        reserve_stage_units=options.reserve_stage_units,
    )
    if not repaired_rows:
        return plan, rows, trace.stages
    repaired_trace.stages.append({"stage": "plan_repair"})
    return repaired, repaired_rows, repaired_trace.stages


# ---------------------------------------------------------------------------
# Retrieval channels and evidence selection


def _lexical_scores_v2(
    store, keywords: list[str], extra_stems: list[str] | None = None
) -> list[tuple]:
    """BM25-lite over the token posting index: candidate rows come from
    posting unions (no corpus scan); each row scores
    sum(idf(stem)) damped by a document-length norm so short precise rows
    outrank long lines with incidental matches. Deterministic, vector-free."""
    stems = sorted(
        {stem for keyword in keywords for stem in _question_stems(keyword)} | set(extra_stems or ())
    )
    if not stems:
        return []
    candidates: dict[tuple, set[str]] = {}
    for stem in stems:
        for key in store.by_token.get(stem, ()):
            candidates.setdefault(key, set()).add(stem)
    if not candidates:
        return []
    average_tokens = store.avg_doc_tokens() or 1.0
    scored = []
    for key, matched in candidates.items():
        doc_tokens = store._doc_tokens.get(key, 0)
        length_norm = 0.75 + 0.25 * (doc_tokens / average_tokens)
        score = sum(store.idf(stem) for stem in matched) / length_norm
        row_type = "fact" if key[0] == "F" else "line"
        scored.append((score, row_type, key[1]))
    return scored


def _question_stems(keyword: str) -> list[str]:
    """Project a profile keyword onto the store's token space (6-char stems)."""
    from memoket_kite.core.algebra import _tokens

    stems = _tokens(keyword)
    return stems if stems else [keyword.lower()[:6]]


def _retrieve_by_date_window(store, anchors: QuestionAnchors, cap: int = 12) -> list[dict]:
    """Deterministic calendar channel: facts whose event date OR session date
    falls inside the question's day/month window, ranked by anchor precision
    then corpus specificity.

    A row can match a question's date anchor while sharing none of its content
    words — the date lives in the fact's metadata, not in its text — so the
    plan and lexical channels, which are both keyed on content, have no way to
    reach it. This channel is the only one that can."""
    day, month = anchors.day, anchors.month
    if not day and not month:
        return []
    matched = []
    for fact in store.facts.values():
        if day:
            event_hit = bool(fact.t) and fact.t[:10] == day
            session_hit = fact.unit_date == day
        else:
            event_hit = bool(fact.t) and fact.t[:7] == month
            session_hit = (fact.unit_date or "").startswith(month)
        if event_hit or session_hit:
            matched.append((0 if event_hit else 1, fact))
    matched.sort(key=lambda pair: (pair[0], -store.fact_specificity(pair[1].id), pair[1].id))
    return [fact_row(fact, 3.0 - 0.1 * rank) for rank, (_, fact) in enumerate(matched[:cap])]


def _retrieve_lexically(
    store, profile, question: str, cap: int = 24, v2: bool = False
) -> list[dict]:
    keywords = profile.keywords(question)
    extra: list[str] = []
    if v2 and getattr(profile, "LEXICAL_EXTRA", False):
        # df-guarded extras: only stems with small posting lists may join,
        # so rare anchors (names, short nouns, inflections) become
        # reachable without re-scoring the whole candidate field. They also
        # rescue questions whose every content word falls below the profile's
        # keyword-length floor ("How old is Max?"), which would otherwise
        # reach this channel with no keywords at all.
        extra = [
            stem
            for stem in getattr(profile, "keywords_extra", lambda _q: [])(question)
            if 0 < len(store.by_token.get(stem, ())) <= 10
        ]
    if not keywords and not extra:
        return []
    if v2:
        scored = _lexical_scores_v2(store, keywords, extra_stems=extra)
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    else:
        patterns = [(keyword, re.compile(re.escape(keyword), re.I)) for keyword in keywords]
        scored = []
        for collection, row_type in ((store.facts, "fact"), (store.lines, "line")):
            for identifier, item in collection.items():
                score = sum(
                    store.idf(keyword) for keyword, pattern in patterns if pattern.search(item.text)
                )
                if score > 0:
                    scored.append((score, row_type, identifier))
        scored.sort(key=lambda item: -item[0])

    return [
        fact_row(store.facts[identifier], score)
        if row_type == "fact"
        else line_row(store.lines[identifier], score)
        for score, row_type, identifier in scored[:cap]
    ]


def _fuse_retrieval_channels(
    plan_rows: list[dict],
    lexical_rows: list[dict],
    *,
    intent: str,
    date_rows: list[dict] | None = None,
) -> list[dict]:
    if intent in ("enumeration", "aggregate", "attribution"):
        plan_ranked = [row for row in plan_rows if row["type"] != "count"]
    else:
        plan_ranked = sorted(
            (row for row in plan_rows if row["type"] != "count"),
            key=lambda row: -row.get("score", 0.0),
        )

    fused: dict[tuple, dict] = {}
    # The date channel competes through RRF like any other channel — calendar
    # proximity earns rank, never a direct seat in the pack.
    channels = [plan_ranked, lexical_rows] + ([date_rows] if date_rows else [])
    for channel in channels:
        for rank, row in enumerate(item for item in channel if item["type"] != "count"):
            key = (row["type"], row["id"])
            entry = fused.setdefault(key, {"row": row, "rrf": 0.0})
            entry["rrf"] += 1.0 / (60 + rank)
    ranked = sorted(fused.values(), key=lambda entry: -entry["rrf"])
    return [entry["row"] for entry in ranked]


def _checked_token_cap(profile) -> int:
    """The row-admission budget, refusing to run it on an estimated counter."""
    cap = int(getattr(profile, "TOKEN_CAP", 0) or 0)
    if cap:
        ledger.require_exact_tokenizer(f"a row-admission budget of {cap} tokens")
    return cap


def _row_tokens(row: dict, store, *, speaker: bool) -> int:
    """Admission price of one row, in the compact index rendering.

    `speaker` has no default: a caller that forgets it would price a labelled
    row for a binding that renders none, and admit a different pack than it
    shows. Rows are priced as they appear in the index (text plus up to two
    short source quotes), not as the answer prompt finally renders them; the two
    differ because hydration and adjacent context are added after selection.
    Falls back to a character estimate when no tokenizer is installed."""
    text = str(row.get("text", ""))
    if row.get("type") == "line":
        text = spoken(row.get("who", ""), text, enabled=speaker)
    if row.get("type") == "fact" and row.get("src"):
        quotes = [
            spoken(store.lines[source_id].who, store.lines[source_id].text, enabled=speaker)[:120]
            for source_id in str(row.get("src", "")).split()[:2]
            if source_id in store.lines
        ]
        if quotes:
            text += " | verbatim: " + " / ".join(quotes)
    return ledger.text_tokens(text) + 8


#: How many anchored plan rows may seed a pack before the budget applies.
_BOUND_SEED_CAP = 40
#: The plan scorer's confidence above which an unanchored row is "precise"
#: enough to be offered ahead of the fused tail, and how many such rows are
#: offered. Both selectors read these, so the two stay in step by construction.
_PRECISE_SCORE = 4.5
_PRECISE_ROWS = 8


def _precise_rows(plan_rows: list[dict]) -> list[dict]:
    """Unanchored plan rows the scorer was confident about, best first."""
    return sorted(
        (
            row
            for row in plan_rows
            if not row.get("bound")
            and row.get("type") != "count"
            and row.get("score", 0) >= _PRECISE_SCORE
        ),
        key=lambda row: -row.get("score", 0),
    )[:_PRECISE_ROWS]


def _select_evidence_rows_tokencap(
    plan_rows: list[dict],
    fused_rows: list[dict],
    store,
    *,
    cap: int,
    unit_cap: int,
    row_cap: int = 0,
    speaker: bool,
) -> list[dict]:
    """Token-cap packing: rows are admitted in the row-budget selector's
    order, but the stop condition is a per-question TOKEN cap over the logged
    render instead of a row count. Questions whose evidence rows are short
    therefore hold more of them at the same token spend, while verbose ones
    trim the fused tail. Bound (plan-anchored) rows stay cap-exempt, since
    they are what the plan asked for; the seed is still truncated at
    ``_BOUND_SEED_CAP`` so a very broad plan cannot fill the pack by itself."""
    bound = [row for row in plan_rows if row.get("bound") and row.get("type") != "count"]
    bound = bound[:_BOUND_SEED_CAP]
    precise = _precise_rows(plan_rows)
    if any(row.get("_plan_reserved") for row in plan_rows):
        eligible = {(row["type"], row["id"]) for row in precise}
        precise = [row for row in plan_rows if row.get("type") != "count"
                   and (row.get("_plan_reserved")
                        or (row["type"], row["id"]) in eligible)]
    rows: list[dict] = []
    per_unit: dict[str, int] = {}
    total = 0
    for row in bound:  # cap-exempt: the plan's own anchored intent
        unit = row.get("unit", row.get("id"))
        per_unit[unit] = per_unit.get(unit, 0) + 1
        rows.append(row)
        total += _row_tokens(row, store, speaker=speaker)
    seen = {(row["type"], row["id"]) for row in rows}
    for row in precise:
        key = (row["type"], row["id"])
        if key in seen:
            continue
        if row_cap and len(rows) >= row_cap:
            break
        cost = _row_tokens(row, store, speaker=speaker)
        if total + cost > cap:
            # Skip it, do not stop. A row that overruns what is left of the
            # budget says nothing about the shorter rows behind it, and
            # stopping here would drop those rows for their position in the
            # ranking rather than for their size. The fused loop below makes
            # the opposite choice, and says why.
            continue
        seen.add(key)
        unit = row.get("unit", row.get("id"))
        per_unit[unit] = per_unit.get(unit, 0) + 1
        rows.append(row)
        total += cost
    for row in fused_rows:
        key = (row["type"], row["id"])
        if key in seen:
            continue
        unit = row.get("unit", row["id"])
        if per_unit.get(unit, 0) >= unit_cap:
            continue
        if row_cap and len(rows) >= row_cap:
            break
        cost = _row_tokens(row, store, speaker=speaker)
        if cost > cap:
            # This row would not fit even an empty pack, so its size says
            # nothing about the rows behind it. Stopping here would return an
            # empty pack whenever such a row ranks first and neither a bound
            # nor a precise row has already seeded one.
            continue
        if total + cost > cap:
            # Stop here, unlike the precise loop above, and deliberately.
            # `precise` is at most eight high-scoring plan rows, so skipping
            # one that does not fit rescues the few behind it. The fused tail
            # is the whole fused ranking: scanning past the cap for anything
            # that still fits would admit rows for being short rather than
            # for being relevant. Only the impossible row above is skipped.
            break
        seen.add(key)
        per_unit[unit] = per_unit.get(unit, 0) + 1
        rows.append(row)
        total += cost
    return rows


def _select_evidence_rows(
    plan_rows: list[dict],
    fused_rows: list[dict],
    *,
    budget: int,
    unit_cap: int,
    pack_v2: bool = False,
) -> list[dict]:
    bound = [row for row in plan_rows if row.get("bound") and row.get("type") != "count"]
    precise = _precise_rows(plan_rows)

    rows = []
    per_unit: dict[str, int] = {}
    seeds = bound + precise
    if pack_v2:
        # execute_plan already reserves each query's leading evidence. A
        # document-scope reference is not a ranking signal for its entire
        # result set: pre-admitting every bound row can fill the whole budget
        # and prevent the fused ranking from contributing any evidence.
        eligible = {(row["type"], row["id"]) for row in precise}
        seeds = [row for row in plan_rows if row.get("type") != "count"
                 and (row.get("_plan_reserved")
                      or (row["type"], row["id"]) in eligible)]
    seeded = set()
    for row in seeds:
        if len(rows) >= budget:
            break
        key = (row["type"], row["id"])
        unit = row.get("unit", row.get("id"))
        if pack_v2:
            if key in seeded:
                continue
            # Preserve explicit query reservations; other seed rows obey the
            # same diversity cap as fused rows before the relief pass.
            if not row.get("_plan_reserved") and per_unit.get(unit, 0) >= unit_cap:
                continue
        seeded.add(key)
        per_unit[unit] = per_unit.get(unit, 0) + 1
        rows.append(row)

    context_cap = max(2, budget // 3) if bound else budget
    seen = {(row["type"], row["id"]) for row in rows}
    added = 0
    for row in fused_rows:
        # Checked before the append, not after: a trailing check admits the row
        # first and only then notices the budget, so a caller asking for one
        # row would receive two.
        if len(rows) >= budget or added >= context_cap:
            break
        key = (row["type"], row["id"])
        if key in seen:
            continue
        unit = row.get("unit", row["id"])
        if per_unit.get(unit, 0) >= unit_cap:
            continue
        seen.add(key)
        per_unit[unit] = per_unit.get(unit, 0) + 1
        rows.append(row)
        added += 1
    if pack_v2 and len(rows) < budget:
        # Relief pass: the loop above can leave the budget unfilled because of
        # the per-unit cap or the context throttle rather than because the
        # fused ranking ran out. Those two limits exist to shape the mix of a
        # full pack, not to hand back one with empty slots, so the ranking is
        # walked again with only duplicates excluded.
        for row in fused_rows:
            if len(rows) >= budget:
                break
            key = (row["type"], row["id"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Recency context


def _add_recency_context(
    rows: list[dict],
    seen: set[tuple],
    store,
    profile,
    question: str,
    intent: str,
    limit: int,
) -> None:
    if not limit or not (intent == "speculative" or _advice_predicate(profile).search(question)):
        return
    preferred_speakers = getattr(profile, "ANCHOR_WHO", None)
    facts = sorted(
        (
            fact
            for fact in store.facts.values()
            if not preferred_speakers or fact.who in preferred_speakers
        ),
        key=lambda fact: fact.unit_date or "",
        reverse=True,
    )
    added = 0
    for fact in facts:
        if added >= limit:
            break
        if ("fact", fact.id) in seen:
            continue
        seen.add(("fact", fact.id))
        rows.append(fact_row(fact, 0.0))
        added += 1
