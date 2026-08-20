"""Offline unit tests for the benchmark bindings' query expansion."""

import pytest

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


def test_keywords_extra_emits_short_and_inflected_forms():
    from benchmarks.locomo import profile

    extra = profile.keywords_extra("How old is Max? When did Maria join a gym?")
    assert "max" in extra and "gym" in extra and "old" in extra  # short tokens
    extra2 = profile.keywords_extra("What are John's suspected health problems?")
    assert "john" in extra2  # possessive base
    extra3 = profile.keywords_extra("When did Andrew finish adopting the dogs he bought?")
    assert "adopt" in extra3 or "adopte" in extra3  # -ing both-forms


def test_lexical_extra_df_guard_only_admits_rare_stems():
    from benchmarks.locomo import profile
    from memoket_kite.pipeline.retrieve import _retrieve_lexically

    store = Store()
    _fact(store, "f_gold", "u1", "Max is 8 years old")
    for index in range(30):  # make 'dog' a high-df stem
        _fact(store, f"f{index}", f"u{index + 2}", f"a dog story number {index}")

    class _P:
        LEXICAL_EXTRA = True
        keywords = staticmethod(profile.keywords)
        keywords_extra = staticmethod(profile.keywords_extra)

    rows = _retrieve_lexically(store, _P, "How old is Max?", cap=5, v2=True)
    assert rows and rows[0]["id"] == "f_gold"  # 'max'/'old' admitted via guard


def test_a_binding_declares_only_what_its_workload_admits():
    """Refusal rules belong where a question may have no answer."""
    from benchmarks.locomo import profile as locomo
    from benchmarks.longmemeval import profile as longmemeval
    from memoket_kite.pipeline import postproc

    assert postproc.PostprocPolicy.parse(longmemeval.POSTPROC_RULES).rules == {
        "premise",
        "hedge",
    }
    # Every LoCoMo question has an answer, so a refusal there is a certain miss.
    assert postproc.PostprocPolicy.parse(locomo.POSTPROC_RULES).rules == frozenset()
    for binding in (locomo, longmemeval):
        assert callable(getattr(binding, "ANSWERABLE_BY_CONSTRUCTION", None))


def test_longmemeval_answerability_predicate_separates_its_two_classes():
    """The predicate must discriminate, not merely return a bool.

    A constant `False` satisfies a type check while disabling the support-check
    override and both refusal rules, all of which consult it. Only a two-sided
    assertion — questions that must be answerable and questions that must not
    be — can tell a working predicate from a degenerate one.
    """
    from benchmarks.longmemeval import profile

    answerable = profile.ANSWERABLE_BY_CONSTRUCTION
    for question in (
        "I'm going back to our previous conversation about DIY decor. Can you remind me?",
        "I remember you told me to dilute tea tree oil. What was the ratio?",
        "Any recommendations for a slow cooker based on what I like?",
    ):
        assert answerable(question), question
    for question in (
        "How many times did I bake egg tarts in the past two weeks?",
        "Who became a parent first, Tom or Alex?",
        "What did I say about my new job in March?",
    ):
        assert not answerable(question), question


def test_the_pipeline_reads_no_benchmark_label():
    """A dataset's own question_type must never reach a decision.

    The labels are supplied by the benchmark, not derived from the question, so
    a branch on one is a per-benchmark fork: it cannot run outside that dataset
    and it makes the measured pipeline different from the shipped one. Every
    such decision has to be reconstructible from the question text alone, which
    is what the refusal rules do with their advice pattern.

    The check is a source scan because a branch like this fails no test — it
    simply never fires on the other dataset.
    """
    import pathlib

    labels = ("single-session", "multi-session", "knowledge-update", "temporal-reasoning")
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "memoket_kite"
    sources = sorted(root.rglob("*.py"))
    # rglob over a path that does not exist yields nothing, and a scan of zero
    # files passes vacuously, so the tree has to be found before it is scanned.
    assert len(sources) > 20, f"pipeline sources not found under {root}"
    offenders = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if any(f'"{label}' in code or f"'{label}" in code for label in labels):
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    assert not offenders, "benchmark labels reached the pipeline:\n" + "\n".join(offenders)


def test_every_binding_supplies_the_declared_vocabularies():
    """A binding that omits a controlled vocabulary would extract into an
    unbounded label space, and the omission would be invisible: the pipeline
    falls back to an empty tuple. The slots are declared once, so the check is
    mechanical, and each vocabulary is fingerprinted in the run manifest."""
    from benchmarks.common import manifest, settings
    from benchmarks.locomo import profile as locomo
    from benchmarks.longmemeval import profile as longmemeval

    for binding in (locomo, longmemeval):
        settings.require_vocabulary(binding)
        recorded = manifest.describe(binding, model="gpt-4.1-mini")["prompt_sha"]
        assert set(settings.FINGERPRINTED) <= set(recorded), binding.__name__

    # EVENTS is shared verbatim; the taxonomies and fact kinds are not, because
    # the two corpora describe different things.
    assert locomo.EVENTS == longmemeval.EVENTS == settings.VOCABULARY["EVENTS"].shared
    assert longmemeval.STOPWORDS - locomo.STOPWORDS == {"tell", "told"}
    assert set(locomo.KINDS) != set(longmemeval.KINDS)
    assert set(locomo.SEED_ROOTS) != set(longmemeval.SEED_ROOTS)


#: The configuration each binding publishes, knob by knob, with no `KITE_*` set.
#: This is the table the released scores were produced under.
_PUBLISHED = {
    "locomo": {
        "AGGREGATE_SHORTCIRCUIT": True,
        "DATE_CHANNEL": True,
        "DEFAULT_BUDGET": 40,
        "DUAL_DATE": False,
        "ENUM_BASIS": True,
        "ENUM_BUDGET": 28,
        "ENUM_UNIT_CAP": 6,
        "HYDRATE": True,
        "INFER_PASS": True,
        "INFER_V2": False,
        "INSTANCE_ALIGNMENT": False,
        "LEXICAL_EXTRA": True,
        "LEXICAL_V2": True,
        "NEIGHBOR_RADIUS": 1,
        "PACK_V2": True,
        "SPEAKER_LABEL": False,
        "PLAN_REPAIR": True,
        "PROFILE_PACK": 0,
        "RECENCY_ANCHOR": 8,
        "REFUSAL_REPLAN": True,
        "ROUND2": True,
        "SECOND_PASS": True,
        "SELF_CONSIST": 0,
        "SUPPORT_CHECK": False,
        "TOKEN_CAP": 1500,
        "TOPIC_REFINEMENT": False,
        "WHOLESALE_CHARS": 6000,
    },
    "longmemeval": {
        "AGGREGATE_SHORTCIRCUIT": False,
        "DATE_CHANNEL": True,
        "DEFAULT_BUDGET": 40,
        "DUAL_DATE": True,
        "ENUM_BASIS": True,
        "ENUM_BUDGET": 44,
        "ENUM_UNIT_CAP": 8,
        "HYDRATE": True,
        "INFER_PASS": False,
        "INFER_V2": False,
        "INSTANCE_ALIGNMENT": True,
        "LEXICAL_EXTRA": True,
        "LEXICAL_V2": True,
        "NEIGHBOR_RADIUS": 1,
        "PACK_V2": True,
        "SPEAKER_LABEL": True,
        "PLAN_REPAIR": True,
        "PROFILE_PACK": 15,
        "RECENCY_ANCHOR": 8,
        "REFUSAL_REPLAN": True,
        "ROUND2": True,
        "SECOND_PASS": True,
        "SELF_CONSIST": 0,
        "SUPPORT_CHECK": False,
        "TOKEN_CAP": 1700,
        "TOPIC_REFINEMENT": True,
        "WHOLESALE_CHARS": 6000,
    },
}


def _binding_under(environment: dict, binding: str) -> dict:
    """Re-import a profile with exactly `environment` set, and read its knobs.

    A profile resolves its settings at import, and the ablation variables are
    process-wide, so a test that reads an already-imported module measures
    whatever the shell happened to export. Every case here starts from a
    cleared environment and imports afresh.
    """
    import importlib
    import os
    import sys

    saved_environment = {k: v for k, v in os.environ.items() if k.startswith("KITE_")}
    # The modules are restored rather than dropped: another test holding a
    # reference to `benchmarks.…` must keep seeing the object it imported.
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith("benchmarks.")}
    for key in saved_environment:
        del os.environ[key]
    os.environ.update(environment)
    try:
        for name in saved_modules:
            del sys.modules[name]
        module = importlib.import_module(f"benchmarks.{binding}.profile")
        return {name: getattr(module, name) for name in _PUBLISHED[binding]}
    finally:
        for key in list(os.environ):
            if key.startswith("KITE_"):
                del os.environ[key]
        os.environ.update(saved_environment)
        for name in [m for m in list(sys.modules) if m.startswith("benchmarks.")]:
            del sys.modules[name]
        sys.modules.update(saved_modules)


@pytest.mark.parametrize("binding", sorted(_PUBLISHED))
def test_a_binding_publishes_exactly_this_configuration(binding):
    """The knobs a released score was produced under, pinned one by one.

    A profile states only its differences from the shared baseline, so a change
    to the baseline, to a knob's override kind, or to a delta silently moves
    what the benchmark measures. Drift fails here rather than in a re-run.
    """
    assert _binding_under({}, binding) == _PUBLISHED[binding]


@pytest.mark.parametrize("binding", sorted(_PUBLISHED))
def test_an_arm_switches_exactly_the_declared_mechanisms(binding):
    """`KITE_ARM` decides what an arm measures, so its reach is pinned.

    Two arm scores compare only when the same mechanisms were switched. If a
    knob joins or leaves the arm, an old and a new `baseline` measure different
    systems while both call themselves baseline.
    """
    from benchmarks.common import settings

    published = _PUBLISHED[binding]
    # Written out rather than read from `settings.KNOBS`: deriving the expected
    # set from the table under test would make the test agree with any change
    # to it, which is the one thing it exists to catch.
    arm_knobs = {
        "LEXICAL_V2",
        "LEXICAL_EXTRA",
        "PACK_V2",
        "DATE_CHANNEL",
        "NEIGHBOR_RADIUS",
        "SPEAKER_LABEL",
        "ENUM_BASIS",
        "ROUND2",
        "INFER_V2",
        "DUAL_DATE",
    }
    assert {
        "NEIGHBOR_RADIUS" if name == "NEIGHBOR" else name
        for name, knob in settings.KNOBS.items()
        if knob.override == settings.ARM
    } == arm_knobs

    for arm, expected in (("baseline", False), ("candidate", True)):
        actual = _binding_under({"KITE_ARM": arm}, binding)
        for name, value in actual.items():
            if name in arm_knobs:
                want = (1 if expected else 0) if name == "NEIGHBOR_RADIUS" else expected
                assert value == want, f"{arm}: {name}"
            else:
                assert value == published[name], f"{arm} moved a non-arm knob: {name}"


@pytest.mark.parametrize("binding", sorted(_PUBLISHED))
def test_single_call_collapses_only_the_answer_stage(binding):
    """The compound arm switches the extra reader calls off and nothing else."""
    collapsed = {
        "SELF_CONSIST",
        "SUPPORT_CHECK",
        "SECOND_PASS",
        "REFUSAL_REPLAN",
        "INFER_PASS",
        "ROUND2",
        "PLAN_REPAIR",
    }
    actual = _binding_under({"KITE_SINGLE_CALL": "1"}, binding)
    for name, value in actual.items():
        expected = (
            (0 if name == "SELF_CONSIST" else False)
            if name in collapsed
            else _PUBLISHED[binding][name]
        )
        assert value == expected, name


@pytest.mark.parametrize(
    ("variable", "value", "knob", "expected"),
    [
        ("KITE_LEXICAL_V2", "0", "LEXICAL_V2", False),
        ("KITE_NEIGHBOR", "0", "NEIGHBOR_RADIUS", 0),
        ("KITE_TOKEN_CAP", "999", "TOKEN_CAP", 999),
        ("KITE_BUDGET", "7", "DEFAULT_BUDGET", 7),
        ("KITE_SELF_CONSIST", "3", "SELF_CONSIST", 3),
        ("KITE_SUPPORT_CHECK", "1", "SUPPORT_CHECK", True),
        # A FIXED knob belongs to the binding and answers to no variable.
        ("KITE_ENUM_BUDGET", "5", "ENUM_BUDGET", None),
        ("KITE_HYDRATE", "0", "HYDRATE", None),
        ("KITE_TOPIC_REFINEMENT", "1", "TOPIC_REFINEMENT", None),
    ],
)
@pytest.mark.parametrize("binding", sorted(_PUBLISHED))
def test_one_variable_moves_one_knob(binding, variable, value, knob, expected):
    """Each public override reaches its own knob, and only that one.

    `expected=None` means the knob is FIXED: the variable exists in no
    contract, so the binding's own value must survive it.
    """
    actual = _binding_under({variable: value}, binding)
    want = _PUBLISHED[binding][knob] if expected is None else expected
    assert actual[knob] == want
    for name, seen in actual.items():
        if name != knob:
            assert seen == _PUBLISHED[binding][name], f"{variable} also moved {name}"
