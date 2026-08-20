"""Whose words a row carries, and what that costs."""

from __future__ import annotations

from memoket_kite.core.algebra import Line, Store
from memoket_kite.pipeline.answer import _hydrate_sources, _render_evidence_row
from memoket_kite.pipeline.render import SPEAKER_LABEL_CAP, spoken
from memoket_kite.pipeline.retrieve import _row_tokens


def _line(store, ident, who, text, unit="u1"):
    store.lines[ident] = Line(id=ident, unit=unit, unit_date="2023-05-01", who=who, text=text)


def test_a_dialogue_row_says_who_said_it():
    row = {
        "type": "line",
        "id": "u1:1",
        "date": "2023-05-01",
        "who": "user",
        "text": "I bought two",
    }
    assert "user: I bought two" in _render_evidence_row(
        row, Store(), False, dual_date=False, speaker=True
    )


def test_a_speaker_that_normalises_to_nothing_leaves_the_text_alone():
    for who in ("", "   ", "\n\t "):
        row = {"type": "line", "id": "u1:1", "date": "2023-05-01", "who": who, "text": "hello"}
        rendered = _render_evidence_row(row, Store(), False, dual_date=False, speaker=True)
        assert ": hello" not in rendered.split(") ", 1)[1]
        assert rendered.endswith("hello")


def test_a_speaker_is_a_label_not_prose():
    assert spoken("  Dr.  Smith \n\n", "x") == "Dr. Smith: x"
    long_name = "n" * (SPEAKER_LABEL_CAP + 40)
    assert spoken(long_name, "x") == f"{'n' * SPEAKER_LABEL_CAP}: x"


def test_a_named_speaker_is_carried_like_any_other():
    row = {"type": "line", "id": "u1:1", "date": "2023-05-01", "who": "Priya", "text": "hi"}
    assert "Priya: hi" in _render_evidence_row(row, Store(), False, dual_date=False, speaker=True)


def test_each_quote_of_a_mixed_source_fact_keeps_its_own_speaker():
    """One fact may rest on turns by different people."""
    store = Store()
    _line(store, "u1:1", "user", "I use grapefruit")
    _line(store, "u1:2", "assistant", "You could try grapefruit")
    row = {"type": "fact", "id": "u1F1", "src": "u1:1 u1:2"}
    assert _hydrate_sources(row, store, speaker=True) == [
        "user: I use grapefruit",
        "assistant: You could try grapefruit",
    ]


def test_a_row_is_priced_at_exactly_what_it_renders():
    """The pricer reads the same string the index shows, not an estimate of it."""
    from memoket_kite.pipeline import ledger

    store = Store()
    for who in ("user", "assistant", "", "  ", "Dr. Smith"):
        row = {"type": "line", "id": "u1:1", "who": who, "text": "some words here"}
        rendered = spoken(who, row["text"])
        assert _row_tokens(row, store, speaker=True) == ledger.text_tokens(rendered) + 8


def test_a_label_can_only_cost_a_row_its_place_never_the_budget():
    """Near the cap the label evicts rows deterministically; it never overruns."""
    from memoket_kite.pipeline.retrieve import _select_evidence_rows_tokencap

    store = Store()
    rows = [
        {"type": "line", "id": f"u1:{n}", "unit": "u1", "who": "assistant", "text": "w " * 12}
        for n in range(8)
    ]
    priced = sum(_row_tokens(row, store, speaker=True) for row in rows)
    for cap in (priced, priced // 2, priced // 4, 1):
        picked = _select_evidence_rows_tokencap(
            [], rows, store, cap=cap, unit_cap=99, row_cap=0, speaker=True
        )
        assert sum(_row_tokens(row, store, speaker=True) for row in picked) <= cap


def test_a_quoted_fact_is_priced_with_its_speakers():
    store = Store()
    _line(store, "u1:1", "assistant", "a quoted sentence")
    row = {"type": "fact", "id": "u1F1", "src": "u1:1", "text": "t"}
    unquoted = {"type": "fact", "id": "u1F2", "src": "", "text": "t"}
    assert _row_tokens(row, store, speaker=True) > _row_tokens(unquoted, store, speaker=True)


def test_a_label_that_normalises_away_leaves_the_citation_prefix_alone():
    """The whitelist prefix is what the body rendered, so both agree on width."""
    from memoket_kite.pipeline.render import speaker_label, spoken

    for who in ("", "   ", "[]", "[[|]]", ":::"):
        assert speaker_label(who) == ""
        assert spoken(who, "a line") == "a line"


def test_a_label_that_survives_normalisation_prefixes_both_alike():
    from memoket_kite.pipeline.render import speaker_label, spoken

    rendered = spoken("user", "a line")
    assert rendered == f"{speaker_label('user')}: a line"
    assert rendered.split("a line", 1)[0] == "user: "


def _session_store(rows):
    """A store whose dominant unit supplies the fact rows session context needs."""
    store = Store()
    store.lines_by_unit["u1"] = []
    for ident, who, text in rows:
        _line(store, ident, who, text)
        store.lines_by_unit["u1"].append(ident)
    return store


def _session_lines(store, limit):
    from memoket_kite.pipeline.answer import _add_session_context

    facts = [{"type": "fact", "id": f"u1F{n}", "unit": "u1"} for n in range(3)]
    rendered: list[str] = []
    shown = _add_session_context(rendered, facts, store, limit)
    return rendered, shown


def test_session_context_survives_a_line_with_no_text():
    """An empty turn is still a turn, and must not take the block down with it."""
    store = _session_store([("u1:1", "user", ""), ("u1:2", "assistant", "a reply")])
    rendered, shown = _session_lines(store, 400)
    assert rendered and "u1:2" in shown


def test_a_line_whose_text_repeats_its_own_label_is_measured_once():
    """The prefix is built, not recovered by searching the body for the text."""
    store = _session_store([("u1:1", "user", "user: user: user:")])
    _, shown = _session_lines(store, 400)
    assert shown == frozenset({"u1:1"})


def test_a_second_line_cut_inside_its_prefix_does_not_count_as_shown():
    """A row is shown only once the cut clears its id and its speaker label."""
    store = _session_store([("u1:1", "user", "x" * 40), ("u1:2", "assistant", "y" * 40)])
    first = len("(u1:1) user: " + "x" * 40)
    prefix = len("(u1:2) assistant: ")
    assert "u1:2" not in _session_lines(store, first + prefix)[1]
    assert "u1:2" in _session_lines(store, first + prefix + 8)[1]


def test_a_binding_whose_parties_are_symmetric_can_leave_the_label_off():
    """Two people talking need no label; a user and an assistant do."""
    from benchmarks.locomo import profile as locomo
    from benchmarks.longmemeval import profile as longmemeval
    from memoket_kite.pipeline.answer import AnswerOptions
    from memoket_kite.pipeline.retrieve import RetrievalOptions

    row = {"type": "line", "id": "u1:1", "date": "2023-05-01", "who": "assistant", "text": "try it"}
    store = Store()

    lme = AnswerOptions.from_profile(longmemeval)
    loc = AnswerOptions.from_profile(locomo)
    assert lme.speaker_label and not loc.speaker_label

    labelled = _render_evidence_row(
        row, store, lme.hydrate_sources, dual_date=lme.dual_date, speaker=lme.speaker_label
    )
    plain = _render_evidence_row(
        row, store, loc.hydrate_sources, dual_date=loc.dual_date, speaker=loc.speaker_label
    )
    assert "assistant: try it" in labelled
    assert "assistant:" not in plain and plain.endswith("try it")

    # Pricing follows the switch, so the pack a binding admits matches what it renders.
    assert RetrievalOptions.from_profile(longmemeval).speaker_label is True
    assert RetrievalOptions.from_profile(locomo).speaker_label is False
    assert _row_tokens(row, store, speaker=True) > _row_tokens(row, store, speaker=False)
