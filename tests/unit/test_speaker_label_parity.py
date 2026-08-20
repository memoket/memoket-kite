"""With the label off, a binding renders and prices exactly what it did before.

The knob was added for a workload whose two parties are asymmetric. A workload
that leaves it off must be unable to tell the knob exists, so these compare the
switched-off path against the behaviour it replaced rather than against itself.
"""

from __future__ import annotations

from memoket_kite.core.algebra import Line, Store
from memoket_kite.pipeline.answer import _hydrate_sources, _render_evidence_row
from memoket_kite.pipeline.retrieve import _row_tokens

#: What `843f391` rendered, written out rather than imported: a copy of the old
#: code would drift with the new one and agree with any change to it.
LEGACY_ROW = "[2023-05-01 Monday] (u1:1, src=u1:1) I bought two"
LEGACY_QUOTE = "I bought two"


def _store():
    store = Store()
    store.lines["u1:1"] = Line(
        id="u1:1", unit="u1", unit_date="2023-05-01", who="user", text="I bought two"
    )
    return store


def _row():
    return {
        "type": "line",
        "id": "u1:1",
        "date": "2023-05-01",
        "who": "user",
        "src": "u1:1",
        "text": "I bought two",
    }


def test_a_dialogue_row_with_the_label_off_is_what_it_was():
    plain = _render_evidence_row(_row(), _store(), False, dual_date=False, speaker=False)
    assert plain == LEGACY_ROW
    assert _render_evidence_row(_row(), _store(), False, dual_date=False, speaker=True) != plain


def test_a_hydrated_quote_with_the_label_off_is_what_it_was():
    fact = {"type": "fact", "id": "u1F1", "src": "u1:1"}
    assert _hydrate_sources(fact, _store(), speaker=False) == [LEGACY_QUOTE]
    assert _hydrate_sources(fact, _store(), speaker=True) == ["user: I bought two"]


def test_pricing_with_the_label_off_is_what_it_was():
    from memoket_kite.pipeline import ledger

    store = _store()
    assert _row_tokens(_row(), store, speaker=False) == ledger.text_tokens(LEGACY_QUOTE) + 8
    assert _row_tokens(_row(), store, speaker=True) > _row_tokens(_row(), store, speaker=False)


def test_the_two_older_context_channels_name_the_speaker_either_way():
    """Neighbour and full-session context said who spoke before the knob existed.

    Both render straight from the store rather than through the shared helper,
    so the knob never reaches them: a binding that leaves the label off still
    gets the `(id) who: text` shape it always got.
    """
    from memoket_kite.pipeline.answer import _add_neighbor_context, _add_session_context

    store = _store()
    store.lines_by_unit["u1"] = ["u1:1"]
    for index in (2, 3, 4):
        line_id = f"u1:{index}"
        store.lines[line_id] = Line(
            id=line_id, unit="u1", unit_date="2023-05-01", who="assistant", text=f"turn {index}"
        )
        store.lines_by_unit["u1"].append(line_id)

    neighbours: list[str] = []
    _add_neighbor_context(neighbours, [_row()], store, radius=1)
    assert any("(u1:2) assistant: turn 2" in line for line in neighbours)

    session: list[str] = []
    _add_session_context(
        session, [{"type": "fact", "id": f"u1F{n}", "unit": "u1"} for n in range(3)], store, 400
    )
    assert "(u1:1) user: I bought two" in "\n".join(session)


def test_the_same_candidates_are_selected_when_the_label_is_off():
    """A pack the binding never asked to relabel is the pack it always had."""
    from memoket_kite.pipeline.retrieve import _select_evidence_rows_tokencap

    store = _store()
    for index in range(6):
        store.lines[f"u1:{index}"] = Line(
            id=f"u1:{index}", unit="u1", unit_date="2023-05-01", who="assistant", text="w " * 10
        )
    rows = [
        {"type": "line", "id": f"u1:{index}", "unit": "u1", "who": "assistant", "text": "w " * 10}
        for index in range(6)
    ]
    cap = sum(_row_tokens(row, store, speaker=False) for row in rows[:3])
    off = _select_evidence_rows_tokencap([], rows, store, cap=cap, unit_cap=99, speaker=False)
    on = _select_evidence_rows_tokencap([], rows, store, cap=cap, unit_cap=99, speaker=True)
    assert [r["id"] for r in off] == ["u1:0", "u1:1", "u1:2"]
    # The label costs tokens, so the same budget buys fewer rows when it is on.
    assert len(on) < len(off)


def test_round_two_evicts_what_the_binding_pays_for(monkeypatch):
    """Drive the production round-2 swap: a label LoCoMo never renders must not
    cost it a second evidence row."""
    from memoket_kite.pipeline import answer
    from memoket_kite.pipeline import retrieve as retrieval
    from memoket_kite.pipeline.answer import AnswerOptions, EvidencePack

    store = _store()
    packed = []
    for index in range(4):
        ident, line_id = f"u1F{index}", f"u1:{index}"
        # The first-round rows carry no speaker, so the label changes only what
        # the incoming row costs — which is exactly the leak being pinned.
        text = "ab"
        store.lines[line_id] = Line(
            id=line_id, unit="u1", unit_date="2023-05-01", who="", text=text
        )
        packed.append(
            {
                "type": "fact",
                "id": ident,
                "unit": "u1",
                "who": "assistant",
                "src": line_id,
                "date": "2023-05-01",
                "text": text,
                "score": float(index),
            }
        )
    incoming_id = "u1:new"
    store.lines[incoming_id] = Line(
        id=incoming_id,
        unit="u1",
        unit_date="2023-05-01",
        who="assistant",
        text="incoming " + "word " * 3,
    )

    class _P:
        TOKEN_CAP = 1500
        ROUND2 = True
        SPEAKER_LABEL = False
        HYDRATE = True
        ANSWER_PROMPT = "{policies}{today}{question}{evidence}"
        keywords = staticmethod(lambda question: ["fact"])

    def run(profile):
        options = AnswerOptions.from_profile(profile)
        pack = EvidencePack(
            rows=[dict(row) for row in packed],
            lines=[
                answer._render_evidence_row(
                    row,
                    store,
                    options.hydrate_sources,
                    dual_date=options.dual_date,
                    speaker=options.speaker_label,
                )
                for row in packed
            ],
            instances=[],
        )
        monkeypatch.setattr(
            answer,
            "llm_json",
            lambda prompt, model=None, **kw: (
                {"terms": ["incoming"]}
                if "search terms" in prompt
                else {"answer": "an answer", "evidence": []}
            ),
        )
        monkeypatch.setattr(
            retrieval,
            "_lexical_scores_v2",
            lambda store_, keywords, extra_stems=(): [(9.0, "line", incoming_id)],
        )
        answer._retry_with_refined_retrieval(
            {"answer": "No information"},
            pack,
            store,
            profile,
            "how many?",
            "pol",
            "2023-05-02",
            "m",
            options,
        )
        return [str(row.get("id")) for row in pack.rows]

    kept = run(_P)

    class _Labelled(_P):
        SPEAKER_LABEL = True

    inflated = run(_Labelled)

    # `843f391` priced without a label, so it freed one fact; the labelled
    # price is larger and reaches into a second.
    assert incoming_id in kept and incoming_id in inflated
    dropped_off = [row["id"] for row in packed if row["id"] not in kept]
    dropped_on = [row["id"] for row in packed if row["id"] not in inflated]
    assert dropped_off == ["u1F0"], dropped_off
    assert dropped_on == ["u1F0", "u1F1"], dropped_on


def test_a_helper_that_needs_the_binding_cannot_be_called_without_it():
    """No default to fall back on, so a forgotten switch fails loudly."""
    import pytest

    from memoket_kite.pipeline.answer import _render_evidence_row as render
    from memoket_kite.pipeline.retrieve import _select_evidence_rows_tokencap as select

    store = _store()
    for call in (
        lambda: _row_tokens(_row(), store),
        lambda: _hydrate_sources({"type": "fact", "id": "f", "src": "u1:1"}, store),
        lambda: render(_row(), store, False, dual_date=False),
        lambda: select([], [_row()], store, cap=99, unit_cap=9),
    ):
        with pytest.raises(TypeError, match="required keyword-only argument"):
            call()
