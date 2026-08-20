"""What reaches the two refusal rules, on every path an answer can leave by."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from memoket_kite.pipeline import answer as answer_pipeline
from memoket_kite.pipeline import ledger, postproc
from memoket_kite.pipeline.verdicts import GateContext

BOTH = "premise,hedge"


def _finalize(record, *, answerable, risk=True, rules=BOTH, advice=None):
    ledger.begin()
    try:
        return answer_pipeline.finalize(
            record,
            policy=postproc.PostprocPolicy.parse(rules),
            typed_verdicts={"premise": GateContext(subjects=["nessie"], premise_risk=risk)},
            advice=advice,
            answerable=answerable,
        )
    finally:
        ledger.end()


def test_a_question_the_workload_says_has_an_answer_keeps_its_answer():
    """A refusal on an answerable question is a certain miss, so no rule makes one."""
    record = {"question": "What colour was the Plesiosaur?", "answer": "It was blue."}
    assert _finalize(dict(record), answerable=True)["answer"] == "It was blue."
    assert _finalize(dict(record), answerable=False)["answer"] == postproc.CANONICAL_REFUSAL


def test_an_answerable_question_keeps_a_hedged_answer_too():
    record = {
        "question": "Where did I go?",
        "answer": "You went to Rome, though there is no evidence about this in the history.",
    }
    assert _finalize(dict(record), answerable=True, risk=False)["answer"].startswith("You went")
    assert (
        _finalize(dict(record), answerable=False, risk=False)["answer"]
        == postproc.CANONICAL_REFUSAL
    )


def _drive(monkeypatch, exit_at: str):
    """Run `_answer` to one of its three exits, counting predicate calls."""
    from memoket_kite.core.algebra import Store

    asked: list[str] = []

    class _P:
        POSTPROC_RULES = BOTH

        @staticmethod
        def ANSWERABLE_BY_CONSTRUCTION(question):
            asked.append(question)
            return True

    retrieval = SimpleNamespace(
        question="What did Nessie say?", plan={}, trace=[], used_fallback=False, intent="factual"
    )
    shortcut = {"question": retrieval.question, "answer": "It was blue."}
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *a, **k: retrieval)
    monkeypatch.setattr(
        answer_pipeline,
        "_answer_attribution",
        lambda r, p: dict(shortcut) if exit_at == "attribution" else None,
    )
    monkeypatch.setattr(
        answer_pipeline,
        "_answer_aggregate",
        lambda r, p: dict(shortcut) if exit_at == "aggregate" else None,
    )
    monkeypatch.setattr(
        answer_pipeline,
        "_build_evidence_pack",
        lambda *a, **k: answer_pipeline.EvidencePack([], [], []),
    )
    monkeypatch.setattr(
        answer_pipeline,
        "_generate_answer",
        lambda *a, **k: {"answer": shortcut["answer"], "evidence": []},
    )

    ledger.begin()
    try:
        record = answer_pipeline._answer(
            Store(),
            None,
            _P,
            retrieval.question,
            "m",
            "m",
            "",
            0,
            None,
            answer_pipeline.AnswerOptions.from_profile(_P),
        )
    finally:
        ledger.end()
    return asked, record


@pytest.mark.parametrize("exit_at", ["attribution", "aggregate", "generated"])
def test_one_question_asks_the_workload_predicate_once(monkeypatch, exit_at):
    """Every exit records the same verdict, and each costs one call to get it."""
    asked, record = _drive(monkeypatch, exit_at)
    assert asked == ["What did Nessie say?"]
    # The verdict travels on the result, so a consumer reads what the rules
    # were judged under instead of asking the predicate a second time.
    assert record["answerable_by_construction"] is True
    assert record["answer"] == "It was blue."  # answerable, so no rule refused it
