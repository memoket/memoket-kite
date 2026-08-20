"""Offline unit tests for evidence packing and the answer-stage retries."""

from memoket_kite.core.algebra import (
    FactRecord,
    Store,
    Unit,
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
# A store wide enough that a topic closure has to choose between sibling
# branches: six branches, each spread over many units.


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


def _line(store, lid, unit, who, text, date="2026-01-01"):
    from memoket_kite.core.algebra import Line

    store.units.setdefault(unit, Unit(unit, date, "", "", 0, 0))
    store.lines[lid] = Line(lid, unit, date, who, text)
    store.lines_by_unit.setdefault(unit, []).append(lid)
    store._index_text(text, key=("L", lid))


def test_neighbor_hydration_pulls_adjacent_lines():
    from memoket_kite.pipeline.answer import _add_neighbor_context

    store = Store()
    for index in range(5):
        _line(store, f"L{index}", "u1", "a" if index % 2 else "b", f"turn {index}")
    # pack cites only L2; neighbors L1 and L3 must appear, L0/L4 must not.
    rows = [{"type": "fact", "src": "L2", "id": "f1", "unit": "u1"}]
    lines: list[str] = []
    _add_neighbor_context(lines, rows, store, radius=1)
    joined = "\n".join(lines)
    assert "(L1)" in joined and "(L3)" in joined
    assert "(L0)" not in joined and "(L4)" not in joined


def test_neighbor_hydration_respects_cap_and_dedup():
    from memoket_kite.pipeline.answer import _add_neighbor_context

    store = Store()
    for index in range(30):
        _line(store, f"L{index}", "u1", "a", f"turn {index}")
    rows = [
        {"type": "fact", "src": f"L{index}", "id": f"f{index}", "unit": "u1"}
        for index in range(0, 30, 2)  # every even line cited
    ]
    lines: list[str] = []
    _add_neighbor_context(lines, rows, store, radius=1, cap=5)
    rendered = [line for line in lines if line.startswith("  [")]
    assert len(rendered) == 5  # capped
    # cited lines are never duplicated as neighbors
    assert not any(f"(L{index})" in "\n".join(rendered) for index in range(0, 30, 2))


def test_enumeration_basis_renders_numbered_members():
    from memoket_kite.pipeline.answer import _add_enumeration_basis

    rows = [
        {
            "type": "count",
            "of": "facts",
            "n": 3,
            "text": "count = 3 matching FACT ROWS",
            "members": [
                {"id": "f1", "date": "2023-05-01", "text": "first trip"},
                {"id": "f2", "date": "2023-05-09", "text": "second trip"},
                {"id": "f3", "date": "2023-05-20", "text": "third trip"},
            ],
        }
    ]
    lines: list[str] = []
    shown_ids = _add_enumeration_basis(lines, rows)
    joined = "\n".join(lines)
    assert "1. (f1) (2023-05-01) first trip" in joined
    assert "3. (f3) (2023-05-20) third trip" in joined
    assert "MENTIONS" in joined
    assert shown_ids == frozenset({"f1", "f2", "f3"})


def test_all_rendered_context_channels_contribute_citable_provenance():
    from memoket_kite.pipeline.answer import (
        AnswerOptions,
        _build_evidence_pack,
        _validate_citations,
    )
    from memoket_kite.pipeline.retrieve import RetrievalResult

    store = Store()
    _line(store, "L1", "u1", "alice", "the retrieved anchor")
    _line(store, "L2", "u1", "bob", "the supporting full-dialogue detail")
    profile_fact = FactRecord(
        id="P1",
        unit="u1",
        unit_date="2026-01-01",
        t="",
        kind="preference",
        who="alice",
        conf="high",
        topics=("food",),
        entities=(),
        src=("PL1",),
        text="Alice prefers noodles",
    )
    store.add_fact(profile_fact)
    fact_rows = [
        {
            "type": "fact",
            "id": f"R{index}",
            "unit": "u1",
            "date": "2026-01-01",
            "src": "L1",
            "score": 1.0,
            "text": f"retrieved row {index}",
        }
        for index in range(1, 4)
    ]
    count_row = {
        "type": "count",
        "of": "facts",
        "members": [{"id": "M1", "date": "2025-12-01", "text": "enumerated event"}],
    }
    retrieval = RetrievalResult(
        question="What dinner would suit Alice?",
        plan={},
        rows=[count_row, *fact_rows],
        lexical_rows=[],
        aggregates=[count_row],
        trace=[],
        used_fallback=False,
        intent="speculative",
        budget=12,
        unit_cap=4,
    )

    class ContextProfile:
        WHOLESALE_CHARS = 1000
        PROFILE_PACK = 1
        ENUM_BASIS = True

        @staticmethod
        def keywords(question):
            return []

    pack = _build_evidence_pack(
        retrieval,
        store,
        ContextProfile,
        retrieval.question,
        AnswerOptions.from_profile(ContextProfile),
    )
    rendered = "\n".join(pack.lines)

    assert "(L2) bob: the supporting full-dialogue detail" in rendered
    assert "[profile] (P1, src=PL1)" in rendered
    assert "1. (M1) (2025-12-01) enumerated event" in rendered
    claimed = ["L2", "P1", "PL1", "M1", "not-shown"]
    assert _validate_citations(claimed, pack) == claimed[:-1]


def test_session_context_does_not_allow_ids_truncated_out_of_the_prompt():
    from memoket_kite.pipeline.answer import _add_session_context

    store = Store()
    _line(store, "L1", "u1", "alice", "first visible detail")
    _line(store, "L2", "u1", "bob", "second truncated detail")
    rows = [{"type": "fact", "id": f"R{index}", "unit": "u1", "src": "L1"} for index in range(3)]
    lines = []
    first_render = "(L1) alice: first visible detail"

    shown_ids = _add_session_context(lines, rows, store, len(first_render))

    assert shown_ids == frozenset({"L1"})
    assert "(L1)" in lines[0]
    assert "(L2)" not in lines[0]


def test_how_often_never_shortcircuits_to_unit_count():
    from memoket_kite.pipeline.answer import _answer_aggregate
    from memoket_kite.pipeline.retrieve import RetrievalResult

    retrieval = RetrievalResult(
        question="How often does Audrey take her pups to the park?",
        plan={},
        rows=[{"type": "count", "of": "units", "n": 9, "text": "count = 9"}],
        lexical_rows=[],
        aggregates=[{"type": "count", "of": "units", "n": 9, "text": "count = 9"}],
        trace=[],
        used_fallback=False,
        intent="aggregate",
        budget=20,
        unit_cap=5,
    )

    class _P:
        AGGREGATE_SHORTCIRCUIT = True

    assert _answer_aggregate(retrieval, _P) is None  # frequency: no parroting


def test_aggregate_shortcircuit_never_emits_empty_or_duplicate_citations():
    from memoket_kite.pipeline.answer import _answer_aggregate
    from memoket_kite.pipeline.retrieve import RetrievalResult

    retrieval = RetrievalResult(
        question="How many meetings were there?",
        plan={},
        rows=[
            {"type": "count", "of": "units", "n": 2, "text": "count = 2"},
            {"type": "count", "of": "facts", "n": 3, "text": "count = 3"},
            {"type": "unit", "id": "u1", "src": "", "text": "meeting one"},
            {"type": "unit", "id": "u1", "src": "", "text": "meeting one"},
        ],
        lexical_rows=[],
        aggregates=[{"type": "count", "of": "units", "n": 2, "text": "count = 2"}],
        trace=[],
        used_fallback=False,
        intent="aggregate",
        budget=20,
        unit_cap=5,
    )

    class _P:
        AGGREGATE_SHORTCIRCUIT = True

    assert _answer_aggregate(retrieval, _P)["cited"] == ["u1"]


def test_round2_trigger_fires_on_vague_and_unsupported():
    from memoket_kite.pipeline.answer import EvidencePack, _round2_trigger

    pack = EvidencePack(
        rows=[{"type": "fact", "id": "f1", "text": "Tim wears Under Armour shoes"}],
        lines=["..."],
        instances=[],
    )
    assert _round2_trigger({"answer": "a renowned outdoor gear company"}, pack)
    assert _round2_trigger({"answer": "No information"}, pack)
    assert _round2_trigger({"answer": "not specified in the evidence"}, pack)
    # supported answer sharing >=2 stems with a pack row: no trigger
    assert not _round2_trigger({"answer": "Under Armour shoes"}, pack)


def test_round2_appends_and_reanswers(monkeypatch):
    import memoket_kite.pipeline.answer as answer_module
    from memoket_kite.pipeline.answer import (
        AnswerOptions,
        EvidencePack,
        _retry_with_refined_retrieval,
    )

    store = Store()
    _fact(store, "f_gold", "u1", "Dave found the car at a scrapyard near Boston")
    _fact(store, "f_noise", "u2", "Dave enjoys fixing cars on weekends")

    calls = []

    def fake_llm(prompt, model=None, **kw):
        calls.append(prompt)
        if "search terms" in prompt:
            return {"gap": "where the car came from", "terms": ["scrapyard", "Boston"]}
        return {"answer": "a scrapyard near Boston", "evidence": ["f_gold"]}

    monkeypatch.setattr(answer_module, "llm_json", fake_llm)

    class _P:
        ANSWER_PROMPT = "{policies}{today}{question}{evidence}"
        keywords = staticmethod(lambda q: ["where", "dave"])

    options = AnswerOptions(
        hydrate_sources=False,
        profile_rows=0,
        instance_alignment=False,
        self_consistency=0,
        support_check=False,
        second_pass=False,
        refusal_replan=False,
        inference_pass=False,
        wholesale_chars=0,
        neighbor_radius=0,
        enum_basis=False,
        round2=True,
        infer_v2=False,
        dual_date=False,
    )
    pack = EvidencePack(
        rows=[{"type": "fact", "id": "f_noise", "src": "", "text": "Dave enjoys fixing cars"}],
        lines=["row"],
        instances=[],
    )
    data = _retry_with_refined_retrieval(
        {"answer": "No information"},
        pack,
        store,
        _P,
        "Where did Dave find the car?",
        "pol",
        "2024-01-01",
        "m",
        options,
    )
    assert "scrapyard" in str(data.get("answer", ""))
    assert any(r["id"] == "f_gold" for r in pack.rows)  # appended, not replaced
    assert any(r["id"] == "f_noise" for r in pack.rows)


def test_dual_date_render_annotates_relative_phrases():
    from memoket_kite.pipeline.answer import _render_evidence_row

    store = Store()
    store.units["u1"] = Unit("u1", "2023-01-10", "", "", 0, 0)
    row = {
        "type": "fact",
        "id": "f1",
        "unit": "u1",
        "date": "2022",
        "src": "",
        "text": "Melanie visited Bali last year",
    }
    plain = _render_evidence_row(row, store, False, dual_date=False, speaker=False)
    dual = _render_evidence_row(row, store, False, dual_date=True, speaker=False)
    assert "said 2023-01-10" not in plain
    assert "said 2023-01-10" in dual


def test_citation_validation_accepts_neighbor_lines_and_rejects_empty_ids():
    """The whitelist must cover every id the prompt actually showed.

    Both benchmarks render ADJACENT DIALOG CONTEXT by default, so a neighbour
    line is legitimately citable even though it is not a pack row; a whitelist
    built from rows and instances alone drops exactly those receipts. In the
    other direction, a row without an id contributes an empty string to the
    whitelist, which would then admit "" and "None" as valid citations.
    """
    from memoket_kite.pipeline.answer import EvidencePack, _validate_citations

    pack = EvidencePack(
        rows=[{"type": "fact", "id": "f1", "src": "L1", "text": "…"}, {"type": "count"}],
        lines=["…"],
        instances=[],
        shown_context_ids=frozenset({"L0", "L2"}),
    )
    assert _validate_citations(["L2", "f1", "L0"], pack) == ["L2", "f1", "L0"]
    assert _validate_citations(["", "None", "f1"], pack) == ["f1"]

    malformed = EvidencePack(
        rows=[{"type": "fact", "id": None, "src": None, "text": "…"}],
        lines=["…"],
        instances=[],
    )
    assert _validate_citations(["None"], malformed) == []
