"""Offline unit tests for the deterministic post-processing stage (no LLM)."""

import dataclasses
import inspect

import pytest

from memoket_kite.core.algebra import FactRecord, Store, Unit
from memoket_kite.pipeline import postproc
from memoket_kite.pipeline.verdicts import GateContext


def _store_with(texts):
    store = Store()
    for index, text in enumerate(texts):
        unit = f"u{index}"
        store.units.setdefault(unit, Unit(unit, "2023-05-01", "", "", 0, 0))
        record = FactRecord(
            id=f"f{index}",
            unit=unit,
            unit_date="2023-05-01",
            t="",
            kind="event",
            who="user",
            conf="med",
            topics=(),
            entities=(),
            src=(),
            text=text,
        )
        store.add_fact(record)
        store._index_text(text, key=("F", f"f{index}"))
    return store


def _input(question, answer, **kwargs):
    return postproc.PostprocInput(question=question, answer=answer, **kwargs)


# ---------------------------------------------------------------------------
# The capability boundary


def test_a_rule_cannot_reach_a_gold_answer_or_a_benchmark_label():
    """The boundary is the type: a field a rule must not read is absent."""
    fields = {field.name for field in dataclasses.fields(postproc.PostprocInput)}
    forbidden = {
        "gold",
        "gold_evidence",
        "question_id",
        "qa_idx",
        "question_type",
        "category",
        "judge",
        "judged",
        "answerable_by_construction",
    }
    assert not fields & forbidden
    # A caller may say whether a refusal is permitted, as a plain boolean; it
    # may not hand the stage the workload concept that decided it.
    assert fields == {"question", "answer", "typed_verdicts", "advice", "allow_refusal"}


def test_the_stage_reads_no_harness_field_and_imports_no_harness_module():
    """Checked against the syntax tree rather than the module text."""
    import ast

    tree = ast.parse(inspect.getsource(postproc))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith("benchmarks") for name in imported)

    forbidden = {"gold", "gold_evidence", "question_id", "qa_idx", "question_type", "judge"}
    named = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    named |= {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not named & forbidden


# ---------------------------------------------------------------------------
# Policy resolution


def test_the_default_policy_rewrites_nothing():
    data = _input("Where did I go?", "Rome. There is no evidence about this in the history.")
    assert postproc.apply(data, postproc.PostprocPolicy()) == (data.answer, [])


def test_the_registry_is_the_only_list_of_rules():
    """One table names the rules, orders them, and supplies their code."""
    assert postproc.RULES == {name for name, _ in postproc._REGISTRY}
    for name, rule in postproc._REGISTRY:
        assert rule.__name__ == f"{name}_refusal"


def test_an_unknown_rule_name_is_rejected_rather_than_ignored():
    """An unrecognised rule name raises, however the policy was built."""
    for name in ("premis", "hedeg", "nonesuch"):
        with pytest.raises(ValueError, match="unknown post-processing rule"):
            postproc.PostprocPolicy.parse(name)
    # Parsing is not the only way in: a policy assembled by hand is checked
    # against the same registry, so it cannot name a rule that never runs.
    with pytest.raises(ValueError, match="unknown post-processing rule"):
        postproc.PostprocPolicy(rules=frozenset({"premise", "nonesuch"}))
    assert postproc.PostprocPolicy(rules=[" Hedge "]).rules == {"hedge"}


def test_an_override_replaces_the_declaration_rather_than_extending_it():
    policy = postproc.PostprocPolicy.resolve("premise,hedge", "hedge")
    assert policy.rules == {"hedge"}
    # Declaring nothing is a decision; only an absent override defers.
    assert postproc.PostprocPolicy.resolve("premise", "").rules == frozenset()
    assert postproc.PostprocPolicy.resolve("premise", None).rules == {"premise"}


# ---------------------------------------------------------------------------
# The two rules


def test_premise_refusal_fires_only_on_an_absent_subject_and_a_confident_answer():
    store = _store_with(["moved into the Harajuku apartment"])
    risky = GateContext.build("How long in Shinjuku's apartment?", store)
    verdicts = {"premise": risky}
    hit = postproc.premise_refusal(
        _input("How long in Shinjuku's apartment?", "Two years.", typed_verdicts=verdicts)
    )
    assert hit is not None and hit[0] == postproc.CANONICAL_REFUSAL

    known = GateContext.build("How long in Harajuku's apartment?", store)
    assert (
        postproc.premise_refusal(
            _input(
                "How long in Harajuku's apartment?",
                "Two years.",
                typed_verdicts={"premise": known},
            )
        )
        is None
    )
    # Already a refusal, and a solicited judgement, are both left alone.
    assert (
        postproc.premise_refusal(
            _input("How long in Shinjuku's apartment?", "No information", typed_verdicts=verdicts)
        )
        is None
    )
    assert (
        postproc.premise_refusal(
            _input("What should I cook this weekend?", "Something light.", typed_verdicts=verdicts)
        )
        is None
    )


def test_a_rule_sees_a_boolean_and_never_the_workload_concept_behind_it():
    """`allow_refusal` is a decision the caller made, not a benchmark label."""
    data = postproc.PostprocInput(question="q", answer="a", allow_refusal=False)
    assert data.allow_refusal is False
    assert not hasattr(data, "answerable_by_construction")
    assert not hasattr(data, "question_type")


def test_hedge_refusal_fires_where_an_answer_asserts_and_disclaims_at_once():
    hit = postproc.hedge_refusal(
        _input(
            "When did the user start the model?",
            "The user started the Ferrari model on 2023-03-01, "
            "but there is no evidence about this in the history.",
        )
    )
    assert hit is not None and hit[0] == postproc.CANONICAL_REFUSAL
    # A bare disclaimer is already a refusal; a clean answer is untouched.
    assert postproc.hedge_refusal(_input("q", "There is no evidence about this.")) is None
    assert (
        postproc.hedge_refusal(_input("q", "The user started the Ferrari model on 2023-03-01."))
        is None
    )


def test_the_first_rule_to_fire_ends_the_pass_and_is_logged():
    store = _store_with(["moved into the Harajuku apartment"])
    risky = GateContext.build("How long in Shinjuku's apartment?", store)
    answer, fired = postproc.apply(
        _input(
            "How long in Shinjuku's apartment?",
            "Two years, though there is no evidence about this in the stored history.",
            typed_verdicts={"premise": risky},
        ),
        postproc.PostprocPolicy.parse("premise,hedge"),
    )
    assert answer == postproc.CANONICAL_REFUSAL
    assert [entry["rule"] for entry in fired] == ["premise_refusal"]
    assert fired[0]["subjects"], "the verdict's subjects are recorded with the rewrite"
    # Both rules were eligible here, so the one that ran is the one the
    # registry lists first: dispatch order is the registry's order.
    assert [name for name, _ in postproc._REGISTRY][0] == "premise"


def test_only_declared_rules_run():
    store = _store_with(["moved into the Harajuku apartment"])
    risky = GateContext.build("How long in Shinjuku's apartment?", store)
    data = _input(
        "How long in Shinjuku's apartment?", "Two years.", typed_verdicts={"premise": risky}
    )
    assert postproc.apply(data, postproc.PostprocPolicy.parse("hedge")) == (data.answer, [])


def test_a_rewritten_refusal_carries_no_citations(monkeypatch):
    """The refusal replaced the claim, so the claim's provenance goes with it."""
    from memoket_kite.pipeline import answer as answer_pipeline
    from memoket_kite.pipeline import ledger

    ledger.begin()
    try:
        record = {
            "question": "Where did Priya go on holiday?",
            "answer": "Rome. There is no evidence about this in the history.",
            "cited": ["F1", "F2"],
        }
        finalized = answer_pipeline.finalize(record, policy=postproc.PostprocPolicy.parse("hedge"))
        assert finalized["answer"] == postproc.CANONICAL_REFUSAL
        assert finalized["cited"] == []
        assert finalized["answer_pre_postproc"].startswith("Rome")
    finally:
        ledger.end()
