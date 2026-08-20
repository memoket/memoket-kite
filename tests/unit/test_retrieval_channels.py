"""Offline unit tests for the retrieval channels and evidence selection."""

from memoket_kite.core.algebra import (
    FactRecord,
    Store,
    SubQuery,
    Unit,
    Where,
    execute,
)
from memoket_kite.core.vocab import Vocab


def _fact(store, fid, unit, text, topics=(), who="a", kind="event", t=""):
    store.units.setdefault(unit, Unit(unit, "2026-01-01", "", "", 0, 0))
    record = FactRecord(
        id=fid,
        unit=unit,
        unit_date="2026-01-01",
        t=t,
        kind=kind,
        who=who,
        conf="med",
        topics=tuple(topics),
        entities=(),
        src=(),
        text=text,
    )
    if store.add_fact(record):
        store._index_text(record.text, key=("F", fid))


def _vocab_with_branches(children=6):
    vocab = Vocab()
    vocab.define_root("hobby")
    for index in range(children):
        code = f"branch_{index}"
        vocab.topics[code] = type(vocab.topics["hobby"])(code, parents={"hobby"})
        vocab.topics[code].status = "canonical"
    return vocab


# ---------------------------------------------------------------------------
# A store whose two facts share a topic and differ only in wording.


def _grep_store():
    store = Store()
    vocab = Vocab()
    vocab.define_root("hobby")
    _fact(store, "f_gold", "u1", "Melanie went camping at Yosemite", topics=("hobby",))
    _fact(store, "f_other", "u2", "Melanie mentioned her painting hobby", topics=("hobby",))
    return store, vocab


# ---------------------------------------------------------------------------
# The specialize step and its monotonicity guard. `_wide_store` spreads facts
# over six sibling branches of one root, so specializing away from the root
# has to pick branches rather than shrink to nothing.


def _wide_store(vocab, per_branch=70):
    store = Store()
    for branch in range(6):
        for index in range(per_branch):
            _fact(
                store,
                f"f{branch}_{index}",
                f"u{branch}_{index % 35}",
                f"branch {branch} item {index}",
                topics=(f"branch_{branch}",),
            )
    return store


def test_specialize_guard_keeps_pre_shrink_candidates():
    vocab = _vocab_with_branches()
    store = _wide_store(vocab)  # 420 facts, all on child topics only
    sub = SubQuery(select="facts", where=Where(topics=(("hobby", True),)))

    rows_base, trace_base = execute(store, vocab, [sub], budget=12, unit_cap=4)
    assert rows_base == []  # baseline pathology: depth-0 shrink drops every branch

    rows_v2, trace_v2 = execute(store, vocab, [sub], budget=12, unit_cap=4, pack_v2=True)
    assert len(rows_v2) == 12
    steps = [s for stage in trace_v2.stages if stage["stage"] == "refine" for s in stage["steps"]]
    assert any(step.startswith("specialize(stop@") for step in steps)


def test_coverage_pack_seats_every_branch():
    vocab = _vocab_with_branches()
    store = _wide_store(vocab)
    sub = SubQuery(select="facts", where=Where(topics=(("hobby", True),)))
    rows, _ = execute(store, vocab, [sub], budget=12, unit_cap=4, pack_v2=True, coverage=True)
    branches = {store.facts[row["id"]].topics[0] for row in rows}
    assert branches == {f"branch_{index}" for index in range(6)}


# ---------------------------------------------------------------------------
# pack v2: subquery quota + unit-cap relief


def test_subquery_quota_reserves_each_subquerys_best_rows():
    store = Store()
    vocab = Vocab()
    vocab.define_root("hobby")
    vocab.define_root("travel")
    # Subquery 1 matches many high-scoring hobby facts in one unit …
    for index in range(10):
        _fact(store, f"h{index}", "u_h", f"hobby detail {index}", topics=("hobby",))
    # … subquery 2 matches one travel fact that global order would starve.
    _fact(store, "t0", "u_t", "travel note", topics=("travel",))
    subs = [
        SubQuery(select="facts", where=Where(topics=(("hobby", True),), grep="hobby")),
        SubQuery(select="facts", where=Where(topics=(("travel", True),))),
    ]
    rows, _ = execute(store, vocab, subs, budget=4, unit_cap=4, pack_v2=True)
    assert "t0" in {row["id"] for row in rows}


def test_unit_cap_relief_fills_budget():
    store = Store()
    vocab = Vocab()
    vocab.define_root("hobby")
    for index in range(8):  # single unit: baseline caps the pack at unit_cap
        _fact(store, f"f{index}", "u_only", f"hobby item {index}", topics=("hobby",))
    sub = SubQuery(select="facts", where=Where(topics=(("hobby", True),)))
    rows_base, _ = execute(store, vocab, [sub], budget=6, unit_cap=3)
    assert len(rows_base) == 3
    rows_v2, _ = execute(store, vocab, [sub], budget=6, unit_cap=3, pack_v2=True)
    assert len(rows_v2) == 6


# ---------------------------------------------------------------------------
# BM25-lite lexical channel


def test_lexical_v2_prefers_short_precise_rows():
    from benchmarks.locomo import profile
    from memoket_kite.pipeline.retrieve import _retrieve_lexically

    store = Store()
    filler = " ".join(f"filler{index}" for index in range(60))
    _fact(store, "f_long", "u1", f"Yosemite camping {filler}")
    _fact(store, "f_short", "u2", "Yosemite camping weekend")
    question = "When did they go camping at Yosemite?"

    rows = _retrieve_lexically(store, profile, question, cap=4, v2=True)
    assert rows and rows[0]["id"] == "f_short"
    # both rows match; the long one must still be reachable
    assert {row["id"] for row in rows} == {"f_long", "f_short"}


def test_lexical_v2_matches_v1_candidates():
    from benchmarks.locomo import profile
    from memoket_kite.pipeline.retrieve import _retrieve_lexically

    store = Store()
    _fact(store, "f1", "u1", "Melanie painted a sunset landscape")
    _fact(store, "f2", "u2", "Caroline adopted a rescue dog")
    question = "What did Melanie paint?"
    ids_v1 = {row["id"] for row in _retrieve_lexically(store, profile, question, v2=False)}
    ids_v2 = {row["id"] for row in _retrieve_lexically(store, profile, question, v2=True)}
    assert ids_v1 == ids_v2 == {"f1"}


# ---------------------------------------------------------------------------
# The compile cache: a plan that cannot execute must not be pinned into it


def test_crashing_plans_never_win_or_get_cached(tmp_path, monkeypatch):
    import memoket_kite.pipeline.compile_plan as compile_plan_module
    from memoket_kite.pipeline.compile_plan import compile_plan

    store = Store()
    vocab = Vocab()

    monkeypatch.setattr(
        compile_plan_module,
        "llm_json",
        lambda *args, **kwargs: {"queries": [{"select": "facts"}], "intent": "factual"},
    )

    class _Profile:
        COMPILE_PROMPT = "{tree}{entities}{kinds}{units}{today}{question}"
        KINDS = ("event",)

    plan = compile_plan(
        "q",
        vocab,
        store,
        _Profile,
        scorer=lambda candidate: float("-inf"),  # every candidate "crashes"
        cache_dir=str(tmp_path),
        scorer_cache_key="test-schema",
    )
    assert plan == {
        "queries": [{"select": "facts", "where": {"grep": "(?!)"}}],
        "intent": "factual",
    }
    # The fallback must survive the public result normalization used by
    # Reasoner.retrieve()/answer(), not merely the internal executor.
    from memoket_kite.research import QueryPlan

    assert QueryPlan.from_dict(plan).to_dict() == plan
    assert list(tmp_path.glob("*.json")) == []  # nothing was pinned


# ---------------------------------------------------------------------------
# Aggregate members and the date-window channel


def test_count_aggregate_carries_members():
    store = Store()
    vocab = Vocab()
    vocab.define_root("travel")
    for index in range(3):
        _fact(store, f"t{index}", f"u{index}", f"trip number {index}", topics=("travel",))
    sub = SubQuery(
        select="facts",
        where=Where(topics=(("travel", True),)),
        pipe=(("count",),),
    )
    rows, _ = execute(store, vocab, [sub])
    counts = [row for row in rows if row["type"] == "count"]
    assert counts and len(counts[0]["members"]) == 3


def test_date_window_channel_event_hit_ranks_first():
    from memoket_kite.pipeline.retrieve import QuestionAnchors, _retrieve_by_date_window

    store = Store()
    _fact(store, "f_event", "u1", "attended the workshop", t="2023-07-12")
    _fact(store, "f_session", "u2", "talked about plans")
    store.units["u2"].date = "2023-07-12"
    store.facts["f_session"].unit_date = "2023-07-12"
    _fact(store, "f_other", "u3", "unrelated fact", t="2023-09-01")

    rows = _retrieve_by_date_window(store, QuestionAnchors(day="2023-07-12"))
    ids = [row["id"] for row in rows]
    assert ids[0] == "f_event"  # event-date match outranks session-date match
    assert "f_session" in ids and "f_other" not in ids

    month_rows = _retrieve_by_date_window(store, QuestionAnchors(month="2023-09"))
    assert [row["id"] for row in month_rows] == ["f_other"]


# ---------------------------------------------------------------------------
# Evidence selection under a token cap


def test_tokencap_packs_short_rows_deeper_and_stops_at_cap():
    from memoket_kite.pipeline.retrieve import _select_evidence_rows_tokencap

    store = Store()
    short = [
        {"type": "fact", "id": f"s{i}", "unit": f"u{i}", "src": "", "score": 1.0, "text": "tiny"}
        for i in range(30)
    ]
    long_rows = [
        {
            "type": "fact",
            "id": f"L{i}",
            "unit": f"v{i}",
            "src": "",
            "score": 1.0,
            "text": "word " * 300,
        }
        for i in range(30)
    ]
    packed_short = _select_evidence_rows_tokencap(
        [], short, store, cap=400, unit_cap=5, speaker=False
    )
    packed_long = _select_evidence_rows_tokencap(
        [], long_rows, store, cap=400, unit_cap=5, speaker=False
    )
    assert len(packed_short) > len(packed_long)  # short rows: more evidence per token
    assert len(packed_long) >= 1


def test_tokencap_bound_rows_exempt_but_seed_truncated():
    from memoket_kite.pipeline.retrieve import _select_evidence_rows_tokencap

    store = Store()
    bound = [
        {
            "type": "fact",
            "id": f"b{i}",
            "unit": "u1",
            "src": "",
            "bound": True,
            "score": 9.0,
            "text": "word " * 200,
        }
        for i in range(50)
    ]
    packed = _select_evidence_rows_tokencap(bound, [], store, cap=100, unit_cap=5, speaker=False)
    assert len(packed) == 40  # exempt from cap, truncated at seed cap 40
