"""Generate an evidence-backed answer from retrieved Codebook rows.

The answer flow is:

1. retrieve evidence rows;
2. resolve deterministic attribution or aggregate answers;
3. build a chronological evidence pack;
4. generate an answer, including explicitly enabled lookup passes; and
5. validate or recover the generated answer before formatting the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from memoket_kite.core.algebra import execute_plan, fact_row, line_row
from memoket_kite.pipeline import ledger, postproc, verdicts
from memoket_kite.pipeline.compile_plan import compile_plan
from memoket_kite.pipeline.patterns import (
    COUNT_QUESTION as _COUNT_QUESTION,
)
from memoket_kite.pipeline.patterns import (
    REFUSAL_LIKE as _REFUSAL_LIKE,
)
from memoket_kite.pipeline.patterns import (
    RELATIVE_PHRASE as _RELATIVE_PHRASE,
)
from memoket_kite.pipeline.patterns import (
    VAGUE_ANSWER as _VAGUE_ANSWER,
)
from memoket_kite.pipeline.patterns import (
    advice_predicate as _advice_predicate,
)
from memoket_kite.pipeline.render import spoken
from memoket_kite.pipeline.retrieve import (
    RetrievalResult,
    _plan_scorer_cache_key,
    _row_tokens,
    _run_retrieval,
    _score_plan,
)
from memoket_kite.prompts.answer import (
    KERNEL_POLICIES,
    REFINE_PROMPT,
    REFUSAL,
    SELF_CONSISTENCY_PICK_PROMPT,
    SUPPORT_CHECK_PROMPT,
)
from memoket_kite.providers.llm import llm_json

# ---------------------------------------------------------------------------
# Answer configuration and internal state


@dataclass(frozen=True)
class AnswerOptions:
    """Profile settings used by the standard answer flow."""

    hydrate_sources: bool
    profile_rows: int
    instance_alignment: bool
    self_consistency: int
    support_check: bool
    second_pass: bool
    refusal_replan: bool
    inference_pass: bool
    wholesale_chars: int
    neighbor_radius: int
    enum_basis: bool
    round2: bool
    infer_v2: bool
    dual_date: bool
    speaker_label: bool = False

    @classmethod
    def from_profile(cls, profile) -> "AnswerOptions":
        return cls(
            hydrate_sources=bool(getattr(profile, "HYDRATE", False)),
            profile_rows=int(getattr(profile, "PROFILE_PACK", 0) or 0),
            instance_alignment=bool(getattr(profile, "INSTANCE_ALIGNMENT", False)),
            self_consistency=int(getattr(profile, "SELF_CONSIST", 0) or 0),
            support_check=bool(getattr(profile, "SUPPORT_CHECK", False)),
            second_pass=bool(getattr(profile, "SECOND_PASS", False)),
            refusal_replan=bool(getattr(profile, "REFUSAL_REPLAN", False)),
            inference_pass=bool(getattr(profile, "INFER_PASS", False)),
            wholesale_chars=int(getattr(profile, "WHOLESALE_CHARS", 0) or 0),
            neighbor_radius=int(getattr(profile, "NEIGHBOR_RADIUS", 0) or 0),
            enum_basis=bool(getattr(profile, "ENUM_BASIS", False)),
            round2=bool(getattr(profile, "ROUND2", False)),
            infer_v2=bool(getattr(profile, "INFER_V2", False)),
            dual_date=bool(getattr(profile, "DUAL_DATE", False)),
            speaker_label=bool(getattr(profile, "SPEAKER_LABEL", False)),
        )


@dataclass
class EvidencePack:
    rows: list[dict]
    lines: list[str]
    instances: list[dict]
    #: Provenance ids rendered by context channels outside the ordinary row and
    #: instance renderers. Keeping one set here prevents a new context channel
    #: from becoming visible to the reader but invisible to citation checking.
    shown_context_ids: frozenset[str] = frozenset()

    def allowed_citation_ids(self) -> frozenset[str]:
        """Every non-empty provenance id visible in the current winning pack."""
        known = {str(value) for value in self.shown_context_ids if value}
        for row in self.rows:
            identifier = row.get("id")
            if identifier:
                known.add(str(identifier))
            known.update(str(row.get("src") or "").split())
        for instance in self.instances:
            identifier = instance.get("id")
            if identifier:
                known.add(str(identifier))
            known.update(str(value) for value in (instance.get("mentions") or ()) if value)
        return frozenset(known)

    def replace_with_rows(self, rows: list[dict], lines: list[str]) -> None:
        """Install a retry's row-only pack and discard provenance it replaced."""
        self.rows = _resort_pack(rows)
        self.lines = lines
        self.instances = []
        self.shown_context_ids = frozenset()


# ---------------------------------------------------------------------------
# Public answer flow


def answer(
    store,
    vocab,
    profile,
    question: str,
    *,
    model: str = "gpt-4.1-mini",
    answer_model: str | None = None,
    reference_date: str = "",
    budget: int = 0,
    plan_cache: str | None = None,
    postproc_policy: "postproc.PostprocPolicy | None" = None,
) -> dict:
    """Retrieve evidence and generate an answer with citations and trace."""

    answer_model = answer_model or model
    options = AnswerOptions.from_profile(profile)
    ledger.begin()
    try:
        return _answer(
            store,
            vocab,
            profile,
            question,
            model,
            answer_model,
            reference_date,
            budget,
            plan_cache,
            options,
            postproc_policy,
        )
    finally:
        ledger.end()


def finalize(
    record: dict,
    *,
    policy: postproc.PostprocPolicy,
    typed_verdicts: dict | None = None,
    advice=None,
    answerable: bool = False,
) -> dict:
    """The single exit every answer leaves through.

    Applies the declared policy and then snapshots the ledger, so a recorded
    cost describes the answer that was returned. Every path uses it, including
    the ones that return before a reader is called.

    `answerable` is the caller's verdict that this question has an answer. It
    is recorded on the result and is what stands the refusal rules down, so a
    consumer reads the same verdict the rules were judged under instead of
    recomputing one.
    """
    record["answerable_by_construction"] = answerable
    rewritten, fired = postproc.apply(
        postproc.PostprocInput(
            question=str(record.get("question", "")),
            answer=str(record.get("answer", "")),
            typed_verdicts=typed_verdicts or {},
            advice=advice,
            allow_refusal=not answerable,
        ),
        policy,
    )
    if fired:
        record["answer_pre_postproc"] = record.get("answer", "")
        record["answer"] = rewritten
        record["postproc"] = fired
        if rewritten == postproc.CANONICAL_REFUSAL:
            record["cited"] = []  # the refusal rests on nothing
    record["telemetry"] = ledger.snapshot()
    return record


def _answer(
    store,
    vocab,
    profile,
    question: str,
    model: str,
    answer_model: str,
    reference_date: str,
    budget: int,
    plan_cache: str | None,
    options: "AnswerOptions",
    postproc_policy: "postproc.PostprocPolicy | None" = None,
) -> dict:
    """Body of :func:`answer`, wrapped so the ledger always closes."""
    retrieval = _run_retrieval(
        store,
        vocab,
        profile,
        question,
        model=model,
        reference_date=reference_date,
        budget=budget,
        plan_cache=plan_cache,
    )

    policy = (
        postproc_policy
        if postproc_policy is not None
        else postproc.PostprocPolicy.resolve(getattr(profile, "POSTPROC_RULES", ""))
    )
    advice = _advice_predicate(profile)
    # A workload may declare a question answerable: there is an answer in the
    # store, so a refusal is a certain miss and no rule may produce one. The
    # verdicts are computed once, before any exit, so every path is judged on
    # the same inputs rather than on whichever branch happened to fire.
    answerable = bool(
        getattr(profile, "ANSWERABLE_BY_CONSTRUCTION", None)
        and profile.ANSWERABLE_BY_CONSTRUCTION(question)
    )
    gate = verdicts.GateContext.build(question, store, intent=retrieval.intent, advice=advice)
    ledger.note("premise", {"subjects": gate.subjects, "risk": gate.premise_risk})
    exit_kwargs = {
        "policy": policy,
        "advice": advice,
        "answerable": answerable,
        "typed_verdicts": {"premise": gate},
    }

    direct = _answer_attribution(retrieval, profile)
    if direct is not None:
        return finalize(direct, **exit_kwargs)
    direct = _answer_aggregate(retrieval, profile)
    if direct is not None:
        return finalize(direct, **exit_kwargs)

    pack = _build_evidence_pack(
        retrieval,
        store,
        profile,
        question,
        options,
    )
    policies = KERNEL_POLICIES.format(refusal=REFUSAL)
    today = _reference_date(store, reference_date)

    data = _generate_answer(
        pack,
        profile,
        question,
        policies,
        today,
        answer_model,
        options,
    )
    data = _normalize_answer_payload(data)
    if _requires_support_check(data, pack, question, options):
        if not _check_answer_support(
            str(data["answer"]), question, "\n".join(pack.lines), answer_model
        ):
            # Refusal conversion is defined only where a question may have no
            # answer. A caller that declares this class answerable by
            # construction is stating that every such question has an answer in
            # the store, so the conversion has no case to apply to and is
            # disabled. The override is recorded rather than silent.
            if answerable:
                ledger.note("support_check_override", True)
            else:
                data["answer"] = REFUSAL
                data["evidence"] = []

    data = _retry_with_refined_retrieval(
        data,
        pack,
        store,
        profile,
        question,
        policies,
        today,
        answer_model,
        options,
    )
    data = _retry_with_lexical_evidence(
        data,
        pack,
        retrieval,
        store,
        profile,
        question,
        policies,
        today,
        answer_model,
        options,
    )
    data = _retry_with_recompiled_plan(
        data,
        pack,
        retrieval,
        store,
        vocab,
        profile,
        question,
        policies,
        today,
        model,
        answer_model,
        reference_date,
        options,
        plan_cache,
    )
    data = _retry_with_inference(
        data,
        pack,
        profile,
        question,
        today,
        answer_model,
        options,
    )
    record = _answer_record(retrieval, pack, data)
    return finalize(record, **exit_kwargs)


def _resort_pack(rows: list[dict]) -> list[dict]:
    """Restore the chronological invariant the answer prompt asserts.

    Retrieval sorts rows by (date, id) and prepends dateless aggregate rows.
    The retry chain appends or replaces rows afterwards, so the merged pack is
    no longer ordered and has to be re-sorted here. Aggregates are held out of
    the sort and re-attached in front: they carry no date, so a whole-list sort
    would keep them there only by accident of the empty-string key."""
    head = [row for row in rows if row.get("type") == "count"]
    tail = [row for row in rows if row.get("type") != "count"]
    tail.sort(key=lambda row: (row.get("date") or "", row.get("id") or ""))
    return head + tail


def _answer_attribution(
    retrieval: RetrievalResult,
    profile,
) -> dict | None:
    """Answer "who said X" from the top-scoring dialog line, without a reader.

    The speaker is an attribute of the retrieved line, so the answer is already
    determined once retrieval ranks the lines; sending the pack to a model can
    only add a chance to misattribute it. Both conditions are required: the
    compiled intent alone would also capture questions that name a speaker
    without asking for one.
    """
    asks_who = re.search(
        r"谁.{0,4}(说|讲|提出|提到|拍板|决定|问)|哪位.{0,6}(说|提|拍板|决定)"
        r"|who\s+(said|mentioned|suggested|decided|told|asked|proposed)",
        retrieval.question,
        re.I,
    )
    if retrieval.intent != "attribution" or not asks_who:
        return None
    top_line = next(
        iter(
            sorted(
                (row for row in retrieval.rows if row["type"] == "line"),
                key=lambda row: -row.get("score", 0),
            )
        ),
        None,
    )
    if top_line is None:
        return None
    who = top_line.get("who", "?").upper()
    return {
        "question": retrieval.question,
        "plan": retrieval.plan,
        "n_evidence_rows": len(retrieval.rows),
        "pack_src": [top_line["id"]],
        "pack_rows": [{"id": top_line["id"], "type": "line", "src": top_line["id"]}],
        "used_fallback": retrieval.used_fallback,
        "answer": profile.ATTRIBUTION_ANSWER.format(
            who=who,
            quote=top_line["text"][:60],
        ),
        "cited": [top_line["id"]],
        "trace": retrieval.trace + [{"stage": "attribution_shortcircuit"}],
    }


def _answer_aggregate(
    retrieval: RetrievalResult,
    profile,
) -> dict | None:
    """Answer a session-count question from the executed plan's own aggregate.

    The plan already counted the units its filters selected, and that count is
    exact over the whole store, whereas the reader would have to re-derive it
    from a pack that retrieval truncated to a budget. Only a count `of` units
    qualifies: a fact count counts mentions, which are not distinct events.
    """
    asks_count = bool(_COUNT_QUESTION.search(retrieval.question))
    if (
        not retrieval.aggregates
        or retrieval.aggregates[0].get("of") != "units"
        or (retrieval.intent != "aggregate" and not asks_count)
        or not getattr(profile, "AGGREGATE_SHORTCIRCUIT", True)
    ):
        return None
    # Frequency questions ("how often") ask for a RATE; a unit count is
    # tautologically wrong for them — let the reader answer from evidence.
    if re.search(r"how\s+(often|frequently)|多久|几天一次", retrieval.question, re.I):
        return None
    sources = _source_ids(retrieval.rows)
    cited = list(dict.fromkeys(str(row["id"]) for row in retrieval.rows[1:6] if row.get("id")))
    return {
        "question": retrieval.question,
        "plan": retrieval.plan,
        "n_evidence_rows": len(retrieval.rows),
        "pack_src": sources,
        "pack_rows": [_pack_row(row) for row in retrieval.rows],
        "used_fallback": False,
        "answer": str(retrieval.aggregates[0]["n"]),
        "cited": cited,
        "trace": retrieval.trace + [{"stage": "aggregate_shortcircuit"}],
    }


# ---------------------------------------------------------------------------
# Evidence pack construction


def _build_evidence_pack(
    retrieval: RetrievalResult,
    store,
    profile,
    question: str,
    options: AnswerOptions,
) -> EvidencePack:
    rows = list(retrieval.rows)

    def _book(channel: str, start: int) -> None:
        # Observation-only ledger charge for the block appended since `start`.
        if ledger.current() is not None and len(lines) > start:
            ledger.charge(channel, real=ledger.text_tokens("\n".join(lines[start:])))

    shown_context_ids: set[str] = set()
    lines = [
        f"[aggregate] {row['text']}"
        for row in rows
        if row["type"] == "count" and row.get("of") != "facts"
    ]
    _book("aggregate_lines", 0)
    mark = len(lines)
    if options.enum_basis:
        shown_context_ids.update(_add_enumeration_basis(lines, rows))
    _book("enum_basis", mark)
    mark = len(lines)
    shown_context_ids.update(_add_session_context(lines, rows, store, options.wholesale_chars))
    _book("session_block", mark)
    mark = len(lines)
    shown_context_ids.update(
        _add_profile_context(lines, store, profile, question, retrieval.intent, options)
    )
    _book("profile_block", mark)
    mark = len(lines)
    instances = _add_instance_context(
        lines,
        store,
        profile,
        question,
        retrieval.intent,
        options.instance_alignment,
    )
    _book("instance_index", mark)
    mark = len(lines)
    lines.extend(
        _render_evidence_row(
            row,
            store,
            options.hydrate_sources,
            dual_date=options.dual_date,
            speaker=options.speaker_label,
        )
        for row in rows
        if row["type"] != "count"
    )
    if ledger.current() is not None:
        ledger.charge(
            "row_renders",
            compact=sum(
                _row_tokens(row, store, speaker=options.speaker_label)
                for row in rows
                if row["type"] != "count"
            ),
            real=ledger.text_tokens("\n".join(lines[mark:])),
            n=sum(1 for row in rows if row["type"] != "count"),
        )
        ledger.charge(
            "bound_rows",
            compact=sum(
                _row_tokens(row, store, speaker=options.speaker_label)
                for row in rows
                if row["type"] != "count" and row.get("bound")
            ),
            n=sum(1 for row in rows if row["type"] != "count" and row.get("bound")),
        )
    mark = len(lines)
    if options.neighbor_radius:
        shown_context_ids.update(
            _add_neighbor_context(
                lines,
                rows,
                store,
                options.neighbor_radius,
            )
        )
    _book("neighbor_context", mark)
    return EvidencePack(
        rows=rows,
        lines=lines,
        instances=instances,
        shown_context_ids=frozenset(shown_context_ids),
    )


def _add_enumeration_basis(lines: list[str], rows: list[dict]) -> frozenset[str]:
    """Render a facts-count aggregate as an enumerable numbered member list.

    The bare mention-count misleads (rows are mentions, not instances) and the
    truncated pack undercounts; a numbered basis lets the reader deduplicate
    distinct events itself."""
    shown_ids: set[str] = set()
    for row in rows:
        if row.get("type") != "count" or row.get("of") != "facts":
            continue
        members = row.get("members") or []
        if not members:
            continue
        lines.append(
            f"[aggregate basis] {len(members)} candidate rows matched the "
            "counting filter (MENTIONS, not deduplicated events — count "
            "DISTINCT real-world events across them):"
        )
        for index, member in enumerate(members, 1):
            identifier = str(member.get("id") or "")
            id_note = f" ({identifier})" if identifier else ""
            lines.append(
                f"  {index}.{id_note} ({member.get('date', '')}) {member.get('text', '')[:160]}"
            )
            if identifier:
                shown_ids.add(identifier)
    return frozenset(shown_ids)


def _add_neighbor_context(
    lines: list[str],
    rows: list[dict],
    store,
    radius: int,
    cap: int = 20,
    text_limit: int = 200,
) -> frozenset:
    """Pull dialog lines adjacent (±radius, same session) to packed evidence.

    A missing answer often sits next to a retrieved anchor rather than in it,
    so each packed row contributes its neighbouring turns. Neighbours are
    truncated and globally capped, and the base pack is never altered."""
    anchor_radius: dict[str, int] = {}
    scored_rows = sorted(
        (row for row in rows if row.get("type") in ("fact", "line")),
        key=lambda row: (-(row.get("score") or 0.0), str(row.get("id"))),
    )
    for row in scored_rows:
        if row.get("type") == "fact":
            for source_id in (row.get("src") or "").split():
                anchor_radius[source_id] = max(anchor_radius.get(source_id, 0), radius)
        else:
            line_id = row.get("id", "")
            anchor_radius[line_id] = max(anchor_radius.get(line_id, 0), radius)
    if not anchor_radius:
        return frozenset()
    covered = set(anchor_radius)
    position_of: dict[str, tuple[str, int]] = {}
    for unit_id in {store.lines[i].unit for i in covered if i in store.lines}:
        for position, line_id in enumerate(store.lines_by_unit.get(unit_id, ())):
            position_of[line_id] = (unit_id, position)
    neighbors: list[tuple[str, str, str]] = []  # (sort key, id, rendered)
    seen: set[str] = set(covered)
    for line_id in sorted(covered):
        located = position_of.get(line_id)
        if located is None:
            continue
        unit_id, position = located
        session_lines = store.lines_by_unit.get(unit_id, ())
        line_radius = anchor_radius[line_id]
        for offset in range(-line_radius, line_radius + 1):
            neighbor_position = position + offset
            if offset == 0 or not 0 <= neighbor_position < len(session_lines):
                continue
            neighbor_id = session_lines[neighbor_position]
            if neighbor_id in seen:
                continue
            seen.add(neighbor_id)
            line = store.lines[neighbor_id]
            neighbors.append(
                (
                    f"{line.unit_date}|{unit_id}|{neighbor_position:05d}",
                    neighbor_id,
                    f"  [{line.unit_date}] ({neighbor_id}) {line.who}: {line.text[:text_limit]}",
                )
            )
    if not neighbors:
        return frozenset()
    neighbors.sort()
    kept = neighbors[:cap]
    lines.append(
        "\nADJACENT DIALOG CONTEXT (lines immediately surrounding the "
        "evidence rows above, same conversation moment):"
    )
    lines.extend(rendered for _, _, rendered in kept)
    return frozenset(identifier for _, identifier, _ in kept)


def _render_evidence_row(
    row: dict,
    store,
    hydrate: bool,
    *,
    dual_date: bool,
    speaker: bool,
) -> str:
    """Render one evidence row exactly as the reader will see it.

    `dual_date` and `speaker` are the binding's, and neither has a default: a
    caller that omits one would render a row for some other deployment.
    """
    source_text = ""
    source_limit, text_limit = (4, 400) if hydrate else (2, 120)
    if row["type"] == "fact" and row.get("src"):
        quotes = _hydrate_sources(row, store, speaker=speaker)[:source_limit]
        if quotes:
            source_text = " | verbatim: " + " / ".join(quote[:text_limit] for quote in quotes)
    anchored = "[anchored] " if row.get("bound") else ""
    date_value = row.get("date")
    said_note = ""
    if (
        dual_date
        and row.get("type") == "fact"
        and _RELATIVE_PHRASE.search(str(row.get("text", "")))
    ):
        # A relative phrase in the fact text plus a resolved event date invites
        # double-resolution ("last year" re-subtracted from 2022). Show when it
        # was SAID so the reader anchors once.
        unit = row.get("unit", "")
        said = store.units[unit].date if unit in store.units else ""
        if said and said != date_value:
            said_note = f" (said {said}; the date tag already resolves the relative phrase)"
    body = row["text"]
    if row["type"] == "line":
        body = spoken(row.get("who", ""), body, enabled=speaker)
    return (
        f"{anchored}[{date_value} {_weekday(date_value)}]{said_note} "
        f"({row['id']}, src={row.get('src', '')}) {body}{source_text}"
    )


def _hydrate_sources(row: dict, store, *, speaker: bool) -> list[str]:
    # Each quote carries the speaker of the line it came from: one fact may be
    # sourced from turns by different people.
    return [
        spoken(store.lines[source_id].who, store.lines[source_id].text, enabled=speaker)
        for source_id in (row.get("src") or "").split()
        if source_id in store.lines
    ]


def _add_session_context(
    lines: list[str],
    rows: list[dict],
    store,
    limit: int,
) -> frozenset[str]:
    """Render the full dialogue of the unit most of the evidence points into.

    The block is only worth its tokens when the evidence is concentrated, so
    the dominant unit must supply at least three fact rows and at least half
    of them; a pack spread across sessions pulls in no dialogue at all.

    Returns the line ids that survive truncation. The body is cut at ``limit``
    characters, and a line whose ``(id) who:`` prefix falls past the cut was
    never shown to the reader, so admitting it to the citation whitelist would
    let a citation point at text nobody saw.
    """
    if not limit:
        return frozenset()
    unit_counts: dict[str, int] = {}
    for row in rows:
        if row.get("type") == "fact" and row.get("unit"):
            unit_counts[row["unit"]] = unit_counts.get(row["unit"], 0) + 1
    if not unit_counts:
        return frozenset()
    unit_id = max(unit_counts, key=lambda identifier: unit_counts[identifier])
    fact_count = sum(unit_counts.values())
    if unit_counts[unit_id] < 3 or unit_counts[unit_id] * 2 < fact_count:
        return frozenset()
    rendered_lines = [
        (
            line_id,
            f"({line_id}) {store.lines[line_id].who}: {store.lines[line_id].text}",
        )
        for line_id in store.lines_by_unit.get(unit_id, ())
    ]
    body = "\n".join(rendered for _, rendered in rendered_lines)[:limit]
    shown_ids: set[str] = set()
    offset = 0
    for index, (line_id, rendered) in enumerate(rendered_lines):
        offset += 1 if index else 0  # the newline that joined this row on
        # A line counts as shown once the truncation reaches past everything
        # that precedes its text: the id, and the speaker label if it has one.
        prefix = f"({line_id}) {store.lines[line_id].who}: "
        if offset + len(prefix) < len(body):
            shown_ids.add(str(line_id))
        offset += len(rendered)
    unit_date = store.units[unit_id].date if unit_id in store.units else ""
    lines.append(
        f"\nFULL SOURCE DIALOGUE of {unit_id} [{unit_date}] "
        f"(the unit most of the evidence points into):\n{body}"
    )
    return frozenset(shown_ids)


def _add_profile_context(
    lines: list[str],
    store,
    profile,
    question: str,
    intent: str,
    options: AnswerOptions,
) -> frozenset[str]:
    """Add standing preferences and traits for advice-shaped questions.

    A recommendation has to be grounded in who is asking, and that grounding
    is rarely among the rows retrieved for the question itself. Only
    speculative and advice questions get this block: on a factual question the
    same rows are unrelated evidence competing for the reader's attention.

    One fact per topic, latest first, so the block spans the asker's interests
    instead of repeating the best-covered one.
    """
    if not options.profile_rows or not (
        intent == "speculative" or _advice_predicate(profile).search(question)
    ):
        return frozenset()
    preferred_speakers = getattr(profile, "ANCHOR_WHO", None)
    facts_by_topic: dict[str, list] = {}
    facts = sorted(
        (
            fact
            for fact in store.facts.values()
            if fact.kind in ("preference", "identity")
            and (not preferred_speakers or fact.who in preferred_speakers)
        ),
        key=lambda fact: fact.unit_date or "",
        reverse=True,
    )
    for fact in facts:
        topic = fact.topics[0] if fact.topics else "misc"
        facts_by_topic.setdefault(topic, []).append(fact)
    profile_lines = []
    shown_ids: set[str] = set()
    for topic, topic_facts in list(facts_by_topic.items())[: options.profile_rows]:
        fact = topic_facts[0]
        sources = " ".join(str(source_id) for source_id in fact.src if source_id)
        src_note = f", src={sources}" if sources else ""
        profile_lines.append(
            f"[profile] ({fact.id}{src_note}) ({topic}) {fact.text[:150]} [{fact.unit_date}]"
        )
        if fact.id:
            shown_ids.add(str(fact.id))
        shown_ids.update(str(source_id) for source_id in fact.src if source_id)
    if profile_lines:
        lines.append(
            "ASKER PROFILE (standing preferences/traits, one per topic, "
            "latest first — pick the most relevant as your personalization anchor):"
        )
        lines.extend(profile_lines)
    return frozenset(shown_ids)


def _add_instance_context(
    lines: list[str],
    store,
    profile,
    question: str,
    intent: str,
    enabled: bool,
) -> list[dict]:
    asks_count = bool(_COUNT_QUESTION.search(question))
    if not enabled or not store.instances or not (asks_count or intent == "enumeration"):
        return []
    keywords = profile.keywords(question)
    scored = []
    for instance_id, instance in store.instances.items():
        text = (
            instance["label"]
            + " "
            + " ".join(
                store.facts[fact_id].text
                for fact_id in instance["mentions"]
                if fact_id in store.facts
            )
        )
        score = sum(
            store.idf(keyword) for keyword in keywords if re.search(re.escape(keyword), text, re.I)
        )
        if score > 0:
            scored.append((score, instance_id))
    scored.sort(reverse=True)
    instances = []
    instance_lines = []
    for _, instance_id in scored[:12]:
        instance = store.instances[instance_id]
        dates = sorted(
            {
                store.facts[fact_id].t or store.facts[fact_id].unit_date
                for fact_id in instance["mentions"]
                if fact_id in store.facts
            }
        )
        instances.append(instance)
        instance_lines.append(
            f"[instance] ({instance['id']}, {instance['kind']}) "
            f"{instance['label']} — mentioned "
            f"{', '.join(value for value in dates if value)} "
            f"(src={' '.join(instance['mentions'])})"
        )
    if instance_lines:
        lines.append(
            "INSTANCE INDEX — each [instance] row is ONE distinct real-world "
            "item/event, already deduplicated across sessions. For counting, "
            "prefer counting qualifying [instance] rows over re-deriving "
            "from the rows below:"
        )
        lines.extend(instance_lines)
    return instances


# ---------------------------------------------------------------------------
# Answer generation


def _generate_answer(
    pack: EvidencePack,
    profile,
    question: str,
    policies: str,
    today: str,
    model: str,
    options: AnswerOptions,
) -> dict:
    if not pack.lines:
        return {"answer": REFUSAL, "evidence": []}
    prompt = profile.ANSWER_PROMPT.format(
        policies=policies,
        today=today,
        question=question,
        evidence="\n".join(pack.lines),
    )
    ledger.call("answer", prompt)
    data = llm_json(prompt, model=model)
    data = _select_consistent_answer(
        data,
        prompt,
        question,
        model,
        options.self_consistency,
    )
    return data


def _normalize_answer_payload(data: dict) -> dict:
    """Turn a malformed or empty reader answer into the refusal contract."""
    answer_text = data.get("answer")
    if not isinstance(answer_text, str) or not answer_text.strip():
        return {"answer": REFUSAL, "evidence": []}
    normalized = dict(data)
    normalized["answer"] = answer_text.strip()
    return normalized


def _select_consistent_answer(
    data: dict,
    prompt: str,
    question: str,
    model: str,
    samples: int,
) -> dict:
    if samples <= 1:
        return data
    for _ in range(samples - 1):
        ledger.call("self_consistency", prompt)
    try:
        attempts = [data] + [
            llm_json(prompt, model=model, temperature=0.7) for _ in range(samples - 1)
        ]
    except Exception:
        # A recovery pass exists to improve an answer that already exists;
        # letting a provider failure here destroy it inverts the point.
        return data
    answers = [str(item.get("answer", "")).strip() for item in attempts]
    if len({item.lower() for item in answers if item}) <= 1:
        return data
    listing = "\n".join(f"[{index + 1}] {answer}" for index, answer in enumerate(answers))
    try:
        pick_prompt = SELF_CONSISTENCY_PICK_PROMPT.format(
            question=question,
            attempts=listing,
        )
        ledger.call("consistency_pick", pick_prompt)
        choice = llm_json(pick_prompt, model=model)
        index = int(choice.get("pick", 1))
    except Exception:
        return data
    return attempts[index - 1] if 1 <= index <= len(attempts) else data


# ---------------------------------------------------------------------------
# Validation and recovery


def _requires_support_check(
    data: dict,
    pack: EvidencePack,
    question: str,
    options: AnswerOptions,
) -> bool:
    return bool(
        pack.lines
        and options.support_check
        and REFUSAL.lower() not in str(data.get("answer", "")).lower()
        and not re.search(
            r"suggest|recommend|would|could|help me|how many|how much|how long",
            question,
            re.I,
        )
    )


def _check_answer_support(
    answer_text: str,
    question: str,
    evidence: str,
    model: str,
) -> bool:
    try:
        support_prompt = SUPPORT_CHECK_PROMPT.format(
            question=question,
            answer=answer_text,
            evidence=evidence,
        )
        ledger.call("support_check", support_prompt)
        result = llm_json(support_prompt, model=model)
        return bool(result.get("supported", True))
    except RuntimeError:
        return True


def _round2_trigger(data: dict, pack: EvidencePack) -> bool:
    """Fire round-2 when the draft looks unsupported or unspecific:
    refusal-shaped, vague indefinite NP, or sharing no content stems with any
    pack row (a cheap deterministic support check)."""
    answer_text = str(data.get("answer", "")).strip()
    if not answer_text or _REFUSAL_LIKE.search(answer_text):
        return True
    if _VAGUE_ANSWER.match(answer_text) and not re.search(r"\d", answer_text):
        return True
    from memoket_kite.core.algebra import _tokens

    answer_stems = set(_tokens(answer_text))
    if not answer_stems:
        return False
    for row in pack.rows:
        if row.get("type") == "count":
            continue
        if len(answer_stems & set(_tokens(str(row.get("text", ""))))) >= 2:
            return False
    return True


def _retry_with_refined_retrieval(
    data: dict,
    pack: EvidencePack,
    store,
    profile,
    question: str,
    policies: str,
    today: str,
    model: str,
    options: AnswerOptions,
) -> dict:
    """Feedback-driven second retrieval round.

    The refine model sees the question and the system's own first-round
    evidence, names the gap, and emits search terms. The rows those terms
    score are added to the first-round pack rather than substituted for it, so
    the reader keeps the evidence it already had, and the question is
    re-answered over the union. The new answer is adopted only if it is not
    refusal-shaped; otherwise the pack and the draft are both left untouched.
    """
    if not options.round2 or not pack.lines:
        return data
    if not _round2_trigger(data, pack):
        return data
    try:
        refine_prompt = REFINE_PROMPT.format(
            draft=str(data.get("answer", ""))[:120],
            question=question,
            evidence="\n".join(pack.lines)[:8000],
        )
        ledger.call("refine", refine_prompt)
        refined = llm_json(refine_prompt, model=model)
    except Exception:
        return data
    terms = [str(t).strip() for t in (refined.get("terms") or [])[:5] if str(t).strip()]
    if not terms:
        return data
    from memoket_kite.core.algebra import _tokens
    from memoket_kite.pipeline.retrieve import _lexical_scores_v2

    term_stems = sorted({stem for term in terms for stem in _tokens(term)})
    base_keywords = profile.keywords(question)
    scored = _lexical_scores_v2(store, base_keywords, extra_stems=term_stems)
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    packed = {(row.get("type"), row.get("id")) for row in pack.rows}
    appended = []
    for _score_value, row_type, identifier in scored:
        if len(appended) >= 8:
            break
        if (row_type, identifier) in packed:
            continue
        packed.add((row_type, identifier))
        if row_type == "fact" and identifier in store.facts:
            appended.append(fact_row(store.facts[identifier], 0.0))
        elif row_type == "line" and identifier in store.lines:
            appended.append(line_row(store.lines[identifier], 0.0))
    if not appended:
        return data
    kept_rows, kept_lines = pack.rows, pack.lines
    if getattr(profile, "TOKEN_CAP", 0):
        # Token-neutral swap: the targeted second-round rows displace an equal
        # token mass of the LOWEST-scoring unbound first-round rows, so the
        # pack budget holds instead of growing (round-2 adds signal, not size).
        to_free = sum(_row_tokens(row, store, speaker=options.speaker_label) for row in appended)
        removable = sorted(
            (row for row in kept_rows if row.get("type") == "fact" and not row.get("bound")),
            key=lambda row: (row.get("score") or 0.0, str(row.get("id"))),
        )
        dropped_ids: set[str] = set()
        freed = 0
        for row in removable:
            if freed >= to_free:
                break
            dropped_ids.add(str(row.get("id")))
            freed += _row_tokens(row, store, speaker=options.speaker_label)
        if dropped_ids:
            dropped_renders = {
                _render_evidence_row(
                    row,
                    store,
                    options.hydrate_sources,
                    dual_date=options.dual_date,
                    speaker=options.speaker_label,
                )
                for row in kept_rows
                if str(row.get("id")) in dropped_ids
            }
            kept_rows = [row for row in kept_rows if str(row.get("id")) not in dropped_ids]
            kept_lines = [line for line in kept_lines if line not in dropped_renders]
    extra_lines = ["\nSECOND-ROUND RETRIEVAL (targeted at the gap above):"] + [
        _render_evidence_row(
            row,
            store,
            options.hydrate_sources,
            dual_date=options.dual_date,
            speaker=options.speaker_label,
        )
        for row in appended
    ]
    prompt = profile.ANSWER_PROMPT.format(
        policies=policies,
        today=today,
        question=question,
        evidence="\n".join(kept_lines + extra_lines),
    )
    try:
        ledger.call("round2_answer", prompt)
        candidate = llm_json(prompt, model=model)
    except Exception:
        return data
    candidate_answer = str(candidate.get("answer", ""))
    if not candidate_answer or _REFUSAL_LIKE.search(candidate_answer):
        return data
    pack.rows = _resort_pack(kept_rows + appended)
    pack.lines = kept_lines + extra_lines
    return candidate


def _retry_with_lexical_evidence(
    data: dict,
    pack: EvidencePack,
    retrieval: RetrievalResult,
    store,
    profile,
    question: str,
    policies: str,
    today: str,
    model: str,
    options: AnswerOptions,
) -> dict:
    if (
        not options.second_pass
        or REFUSAL.lower() not in str(data.get("answer", "")).lower()
        or not retrieval.lexical_rows
    ):
        return data
    alternate = []
    per_unit: dict[str, int] = {}
    for row in retrieval.lexical_rows:
        if row["type"] == "count":
            continue
        unit_id = row.get("unit", row["id"])
        if per_unit.get(unit_id, 0) >= retrieval.unit_cap:
            continue
        per_unit[unit_id] = per_unit.get(unit_id, 0) + 1
        alternate.append(row)
        if len(alternate) >= retrieval.budget:
            break
    alternate.sort(key=lambda row: (row.get("date") or "", row.get("id") or ""))
    lines = [
        _render_evidence_row(
            row,
            store,
            options.hydrate_sources,
            dual_date=options.dual_date,
            speaker=options.speaker_label,
        )
        for row in alternate
    ]
    prompt = profile.ANSWER_PROMPT.format(
        policies=policies,
        today=today,
        question=question,
        evidence="\n".join(lines),
    )
    ledger.call("second_pass", prompt)
    try:
        candidate = llm_json(prompt, model=model)
    except Exception:
        return data  # a provider failure must not destroy the answer in hand
    candidate_answer = str(candidate.get("answer", ""))
    if (
        not candidate_answer
        or REFUSAL.lower() in candidate_answer.lower()
        or (
            options.support_check
            and not _check_answer_support(candidate_answer, question, "\n".join(lines), model)
        )
    ):
        return data
    pack.replace_with_rows(alternate, lines)
    return candidate


def _retry_with_recompiled_plan(
    data: dict,
    pack: EvidencePack,
    retrieval: RetrievalResult,
    store,
    vocab,
    profile,
    question: str,
    policies: str,
    today: str,
    compile_model: str,
    answer_model: str,
    reference_date: str,
    options: AnswerOptions,
    plan_cache: str | None,
) -> dict:
    if not options.refusal_replan or REFUSAL.lower() not in str(data.get("answer", "")).lower():
        return data
    feedback = (
        f"{question}\n(NOTE: a direct search found nothing. The question's "
        "category words may not appear verbatim in the history — enumerate "
        "4-8 CONCRETE instances, brand names or hyponyms of them in the grep net.)"
    )
    legal_kinds = set(getattr(profile, "KINDS", ()) or ())

    def scorer(candidate):
        return _score_plan(store, vocab, candidate, legal_kinds)

    try:
        plan = compile_plan(
            feedback,
            vocab,
            store,
            profile,
            model=compile_model,
            scorer=scorer,
            today=reference_date,
            cache_dir=plan_cache,
            scorer_cache_key=_plan_scorer_cache_key(legal_kinds),
        )
        rows, _ = execute_plan(
            store,
            vocab,
            plan,
            speakers=store.speakers,
            budget=retrieval.budget,
            unit_cap=retrieval.unit_cap,
            legal_kinds=legal_kinds,
        )
        rows = [row for row in rows if row.get("type") != "count"][: retrieval.budget]
    except Exception:
        rows = []
    if not rows:
        return data
    rows.sort(key=lambda row: (row.get("date") or "", row.get("id") or ""))
    lines = [
        _render_evidence_row(
            row,
            store,
            options.hydrate_sources,
            dual_date=options.dual_date,
            speaker=options.speaker_label,
        )
        for row in rows
    ]
    prompt = profile.ANSWER_PROMPT.format(
        policies=policies,
        today=today,
        question=question,
        evidence="\n".join(lines),
    )
    ledger.call("replan_answer", prompt)
    try:
        candidate = llm_json(prompt, model=answer_model)
    except Exception:
        return data  # a provider failure must not destroy the answer in hand
    candidate_answer = str(candidate.get("answer", ""))
    if (
        not candidate_answer
        or REFUSAL.lower() in candidate_answer.lower()
        or (
            options.support_check
            and not _check_answer_support(
                candidate_answer, question, "\n".join(lines), answer_model
            )
        )
    ):
        return data
    pack.replace_with_rows(rows, lines)
    return candidate


def _retry_with_inference(
    data: dict,
    pack: EvidencePack,
    profile,
    question: str,
    today: str,
    model: str,
    options: AnswerOptions,
) -> dict:
    answer_text = str(data.get("answer", ""))
    refusal_shaped = (
        bool(_REFUSAL_LIKE.search(answer_text))
        if options.infer_v2
        else REFUSAL.lower() in answer_text.lower()
    )
    if (
        not options.inference_pass
        or not getattr(profile, "INFER_PROMPT", None)
        or not refusal_shaped
        or not pack.lines
    ):
        return data
    try:
        infer_prompt = profile.INFER_PROMPT.format(
            today=today,
            question=question,
            evidence="\n".join(pack.lines),
        )
        ledger.call("infer", infer_prompt)
        candidate = llm_json(infer_prompt, model=model)
    except Exception:
        return data
    candidate_answer = str(candidate.get("answer", ""))
    if candidate_answer and REFUSAL.lower() not in candidate_answer.lower():
        return candidate
    return data


# ---------------------------------------------------------------------------
# Result formatting and shared utilities


def _answer_record(
    retrieval: RetrievalResult,
    pack: EvidencePack,
    data: dict,
) -> dict:
    return {
        "question": retrieval.question,
        "plan": retrieval.plan,
        "n_evidence_rows": len(pack.rows),
        "pack_src": _source_ids(pack.rows),
        "pack_rows": [_pack_row(row) for row in pack.rows]
        + [
            {
                "id": instance["id"],
                "type": "instance",
                "src": " ".join(instance["mentions"]),
            }
            for instance in pack.instances
        ],
        "used_fallback": retrieval.used_fallback,
        "answer": str(data.get("answer", "")),
        "cited": _validate_citations(data.get("evidence", []), pack),
        "trace": retrieval.trace,
    }


def _validate_citations(claimed, pack: EvidencePack) -> list[str]:
    """Keep only citations naming something the reader was actually shown.

    The claimed list is model output, so it can name an id the reader never
    saw or one it invented outright, and whatever survives here is what the
    public API returns as `citations`. A citation is a receipt: one that names
    a row absent from the pack is decoration, not evidence, so an id is kept
    only when the pack rendered it. Order and first occurrence are preserved.
    """
    if not isinstance(claimed, (list, tuple)):
        return []
    known = pack.allowed_citation_ids()
    seen: set[str] = set()
    kept: list[str] = []
    for item in claimed if isinstance(claimed, (list, tuple)) else ():
        cited = str(item)
        if cited in known and cited not in seen:
            seen.add(cited)
            kept.append(cited)
    return kept


def _pack_row(row: dict) -> dict:
    return {
        "id": row.get("id", ""),
        "type": row["type"],
        "src": row.get("src", ""),
    }


def _source_ids(rows: list[dict]) -> list[str]:
    sources = set()
    for row in rows:
        sources.update((row.get("src") or "").split())
    return sorted(sources)


def _reference_date(store, reference_date: str) -> str:
    return reference_date or max(
        (unit.date for unit in store.units.values()),
        default="",
    )


def _weekday(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%A")
    except (ValueError, TypeError):
        return "?"
