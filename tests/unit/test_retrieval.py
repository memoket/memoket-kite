from copy import deepcopy
from pathlib import Path

import memoket_kite.pipeline.answer as answer_pipeline
from memoket_kite.core.algebra import Store
from memoket_kite.pipeline.answer import answer
from memoket_kite.pipeline.retrieve import RetrievalResult, retrieve

DEMO = Path(__file__).parents[2] / "examples" / "data" / "demo_codebook.xml"


class Profile:
    KINDS = ("plan", "event")
    ANSWER_PROMPT = "{policies}\nTODAY: {today}\nQUESTION: {question}\nEVIDENCE:\n{evidence}"
    ATTRIBUTION_ANSWER = "{who}: {quote}"

    @staticmethod
    def keywords(question: str) -> list[str]:
        return ["marathon", "shanghai"]


class SymbolicProfile(Profile):
    """Profile used when a test needs to isolate plan execution."""

    @staticmethod
    def keywords(question: str) -> list[str]:
        return []


def _fixed_plan():
    return {
        "queries": [
            {
                "select": "facts",
                "where": {
                    "grep": "marathon",
                    "kind": ["plan"],
                    "time": ["2026-10-18", ""],
                },
            }
        ],
        "intent": "factual",
    }


def _retrieval(*, question="What marathon is Alice training for?", intent="factual"):
    row = {
        "type": "fact",
        "id": "s1F1",
        "unit": "s1",
        "date": "2026-10-18",
        "who": "alice",
        "src": "s1L1",
        "score": 5.0,
        "text": "Alice is training for the Shanghai Marathon.",
    }
    return RetrievalResult(
        question=question,
        plan=_fixed_plan(),
        rows=[row],
        lexical_rows=[row],
        aggregates=[],
        trace=[{"stage": "execute"}],
        used_fallback=False,
        intent=intent,
        budget=12,
        unit_cap=4,
    )


def test_retrieve_normalizes_and_executes_the_compiled_plan(monkeypatch):
    store, vocab = Store.load([str(DEMO)])
    monkeypatch.setattr(
        "memoket_kite.pipeline.retrieve.compile_plan",
        lambda *args, **kwargs: deepcopy(_fixed_plan()),
    )
    result = retrieve(
        store,
        vocab,
        SymbolicProfile,
        "What did Alice plan on October 18, 2026?",
        model="test:model",
    )

    where = result["plan"]["queries"][0]["where"]
    assert where["who"] == ["alice"]
    assert where["time"] == ["2026-10-18", "2026-10-18"]
    assert [row["id"] for row in result["rows"]] == ["s1F1"]
    assert result["used_fallback"] is False


def test_answer_uses_retrieved_rows_as_its_evidence_pack(monkeypatch):
    store, vocab = Store.load([str(DEMO)])
    prompts = []
    monkeypatch.setattr(
        "memoket_kite.pipeline.retrieve.compile_plan",
        lambda *args, **kwargs: deepcopy(_fixed_plan()),
    )

    def answer_once(prompt, **kwargs):
        prompts.append(prompt)
        return {
            "answer": "Alice is preparing for the Shanghai Marathon.",
            "evidence": ["s1L1"],
        }

    monkeypatch.setattr(
        "memoket_kite.pipeline.answer.llm_json",
        answer_once,
    )
    result = answer(
        store,
        vocab,
        SymbolicProfile,
        "What did Alice plan on October 18, 2026?",
        model="test:model",
    )

    assert result["answer"].startswith("Alice")
    assert result["pack_rows"] == [{"id": "s1F1", "type": "fact", "src": "s1L1"}]
    assert result["pack_src"] == ["s1L1"]
    assert result["cited"] == ["s1L1"]
    assert "s1F1" in prompts[-1]
    assert "answer_prompt" not in result


def test_empty_reader_answer_uses_the_refusal_contract(monkeypatch):
    store, vocab = Store.load([str(DEMO)])
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *args, **kwargs: _retrieval())
    monkeypatch.setattr(answer_pipeline, "llm_json", lambda *args, **kwargs: {})

    result = answer(store, vocab, Profile, "Which race is Alice training for?")

    assert result["answer"] == answer_pipeline.REFUSAL
    assert result["cited"] == []


def test_empty_plan_is_recompiled_once_when_repair_is_enabled(monkeypatch):
    class RepairProfile(Profile):
        PLAN_REPAIR = True

    plans = iter(
        [
            {
                "queries": [{"select": "facts", "where": {"grep": "not-in-codebook"}}],
                "intent": "factual",
            },
            _fixed_plan(),
        ]
    )
    store, vocab = Store.load([str(DEMO)])
    monkeypatch.setattr(
        "memoket_kite.pipeline.retrieve.compile_plan",
        lambda *args, **kwargs: deepcopy(next(plans)),
    )

    result = retrieve(
        store,
        vocab,
        RepairProfile,
        "What marathon is Alice training for?",
        model="test:model",
    )

    assert result["plan"] == _fixed_plan()
    assert any(event["stage"] == "plan_repair" for event in result["trace"])


def test_lexical_retry_replaces_context_provenance_with_the_winning_prompt(monkeypatch):
    class SecondPassProfile(Profile):
        SECOND_PASS = True
        NEIGHBOR_RADIUS = 1

    prompts = []
    responses = iter(
        [
            {"answer": "No information", "evidence": []},
            {
                "answer": "Alice plans to travel to Shanghai.",
                "evidence": ["s1L0", "s2L1"],
            },
        ]
    )
    store, vocab = Store.load([str(DEMO)])
    from memoket_kite.core.algebra import Line

    store.lines["s1L0"] = Line("s1L0", "s1", "2026-01-10", "bob", "an adjacent detail")
    store.lines_by_unit["s1"].insert(0, "s1L0")
    retrieval = _retrieval()
    retrieval.lexical_rows = [answer_pipeline.fact_row(store.facts["s2F1"], 4.0)]
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *args, **kwargs: retrieval)

    def scripted_reader(prompt, **kwargs):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(
        answer_pipeline,
        "llm_json",
        scripted_reader,
    )

    result = answer(
        store,
        vocab,
        SecondPassProfile,
        "What marathon is Alice training for?",
        model="test:model",
    )

    assert result["answer"] == "Alice plans to travel to Shanghai."
    assert result["pack_rows"] == [{"id": "s2F1", "type": "fact", "src": "s2L1"}]
    assert result["cited"] == ["s2L1"]
    assert "s1L0" in prompts[0]
    assert "s1L0" not in prompts[-1]
    assert "s2F1" in prompts[-1]


def test_unit_aggregate_answers_without_calling_the_answer_model(monkeypatch):
    store, vocab = Store.load([str(DEMO)])
    monkeypatch.setattr(
        "memoket_kite.pipeline.retrieve.compile_plan",
        lambda *args, **kwargs: {
            "queries": [
                {
                    "select": "units",
                    "where": {"grep": "training"},
                    "pipe": [{"op": "count"}],
                }
            ],
            "intent": "aggregate",
        },
    )
    monkeypatch.setattr(
        "memoket_kite.pipeline.answer.llm_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("answer model should not be called")
        ),
    )

    result = answer(
        store,
        vocab,
        Profile,
        "How many training sessions were there?",
        model="test:model",
    )

    assert result["answer"] == "1"
    assert result["trace"][-1]["stage"] == "aggregate_shortcircuit"


def test_attribution_is_answered_from_the_top_source_line(monkeypatch):
    store, vocab = Store.load([str(DEMO)])
    retrieval = _retrieval(question="Who said they were training?", intent="attribution")
    retrieval.rows = [
        {
            "type": "line",
            "id": "s1L1",
            "unit": "s1",
            "date": "2026-01-10",
            "who": "alice",
            "src": "s1L1",
            "score": 4.0,
            "text": "I started training for the Shanghai Marathon.",
        }
    ]
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *args, **kwargs: retrieval)
    monkeypatch.setattr(
        answer_pipeline,
        "llm_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM should not run")),
    )

    result = answer(store, vocab, Profile, retrieval.question, model="test:model")

    assert result["answer"].startswith("ALICE")
    assert result["cited"] == ["s1L1"]
    assert result["trace"][-1]["stage"] == "attribution_shortcircuit"


def test_support_check_replaces_an_unsupported_answer(monkeypatch):
    class CheckedProfile(Profile):
        SUPPORT_CHECK = True

    store, vocab = Store.load([str(DEMO)])
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *args, **kwargs: _retrieval())
    responses = iter(
        [
            {"answer": "Alice is training for a different race.", "evidence": ["s1L1"]},
            {"supported": False},
        ]
    )
    monkeypatch.setattr(answer_pipeline, "llm_json", lambda *args, **kwargs: next(responses))

    result = answer(store, vocab, CheckedProfile, "Which race is Alice training for?")

    assert result["answer"] == answer_pipeline.REFUSAL
    assert result["cited"] == []


def test_inference_recovery_uses_the_profile_prompt(monkeypatch):
    class InferenceProfile(Profile):
        INFER_PASS = True
        INFER_PROMPT = "TODAY: {today}\nQUESTION: {question}\nEVIDENCE: {evidence}"

    store, vocab = Store.load([str(DEMO)])
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *args, **kwargs: _retrieval())
    responses = iter(
        [
            {"answer": "No information in the provided evidence.", "evidence": []},
            {"answer": "The Shanghai Marathon", "evidence": ["s1L1"]},
        ]
    )
    monkeypatch.setattr(answer_pipeline, "llm_json", lambda *args, **kwargs: next(responses))

    result = answer(store, vocab, InferenceProfile, "Which race is Alice training for?")

    assert result["answer"] == "The Shanghai Marathon"
    assert result["cited"] == ["s1L1"]


def test_self_consistency_selects_the_judged_attempt(monkeypatch):
    responses = iter(
        [
            {"answer": "second", "evidence": ["s1L1"]},
            {"answer": "third", "evidence": ["s1L1"]},
            {"pick": 2},
        ]
    )
    monkeypatch.setattr(answer_pipeline, "llm_json", lambda *args, **kwargs: next(responses))

    selected = answer_pipeline._select_consistent_answer(
        {"answer": "first", "evidence": []},
        "prompt",
        "question",
        "test:model",
        3,
    )

    assert selected["answer"] == "second"


def test_instance_registry_is_added_to_enumeration_evidence():
    class InstanceProfile(Profile):
        INSTANCE_ALIGNMENT = True

    store, _ = Store.load([str(DEMO)])
    store.instances = {
        "I1": {
            "id": "I1",
            "label": "Shanghai Marathon",
            "kind": "competition",
            "mentions": ["s1F1"],
        }
    }
    lines = []

    instances = answer_pipeline._add_instance_context(
        lines,
        store,
        InstanceProfile,
        "How many marathons was Alice preparing for?",
        "enumeration",
        True,
    )

    assert [item["id"] for item in instances] == ["I1"]
    assert any("Shanghai Marathon" in line for line in lines)


def test_replan_retry_replaces_context_provenance_with_the_winning_prompt(monkeypatch):
    class RecompileProfile(Profile):
        REFUSAL_REPLAN = True
        NEIGHBOR_RADIUS = 1

    store, vocab = Store.load([str(DEMO)])
    from memoket_kite.core.algebra import Line

    store.lines["s1L0"] = Line("s1L0", "s1", "2026-01-10", "bob", "an adjacent detail")
    store.lines_by_unit["s1"].insert(0, "s1L0")
    monkeypatch.setattr(answer_pipeline, "_run_retrieval", lambda *args, **kwargs: _retrieval())
    cache_dirs = []
    prompts = []

    def compile_again(*args, **kwargs):
        cache_dirs.append(kwargs.get("cache_dir"))
        return deepcopy(_fixed_plan())

    monkeypatch.setattr(answer_pipeline, "compile_plan", compile_again)
    replan_rows = [answer_pipeline.fact_row(store.facts["s2F1"], 4.0)]
    monkeypatch.setattr(
        answer_pipeline,
        "execute_plan",
        lambda *args, **kwargs: (replan_rows, None),
    )
    responses = iter(
        [
            {"answer": "No information", "evidence": []},
            {
                "answer": "Alice plans to travel to Shanghai.",
                "evidence": ["s1L0", "s2L1"],
            },
        ]
    )

    def scripted_reader(prompt, **kwargs):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(answer_pipeline, "llm_json", scripted_reader)

    result = answer(
        store,
        vocab,
        RecompileProfile,
        "Which race is Alice training for?",
        model="test:model",
        plan_cache="cache/plans",
    )

    assert result["answer"] == "Alice plans to travel to Shanghai."
    assert result["pack_rows"] == [{"id": "s2F1", "type": "fact", "src": "s2L1"}]
    assert result["cited"] == ["s2L1"]
    assert "s1L0" in prompts[0]
    assert "s1L0" not in prompts[-1]
    assert "s2F1" in prompts[-1]
    assert cache_dirs == ["cache/plans"]


def test_a_single_endpoint_time_window_is_tolerated():
    """The executor accepts `["2023-05"]` as legal compiler output.

    The normalizer runs before execution and must accept everything the algebra
    layer does: indexing `time_filter[1]` unconditionally raises IndexError on
    a one-element window, killing retrieval before a single row is fetched. A
    single endpoint is widened into a closed window over the same instant.
    """
    from memoket_kite.pipeline.retrieve import QuestionAnchors, _normalize_time_filters

    queries = [{"select": "facts", "where": {"time": ["2023-05-13"]}}]
    _normalize_time_filters(queries, "What happened?", QuestionAnchors(day="2023-05-13"))
    assert queries[0]["where"]["time"] == ["2023-05-13", "2023-05-13"]

    queries = [{"select": "facts", "where": {"time": ["2023-05"]}}]
    _normalize_time_filters(queries, "What happened in May?", QuestionAnchors(month="2023-05"))
    assert queries[0]["where"]["time"] == ["2023-05", "2023-05"]


def test_a_day_anchor_is_a_real_calendar_date():
    """The day anchor becomes a plan time filter, so it is always a valid date.

    Full-width digits are read by value; a date that does not exist on the
    calendar gives no day anchor.
    """
    from memoket_kite.pipeline.retrieve import _extract_question_anchors

    def day(question):
        return _extract_question_anchors(question, []).day

    assert day("What did Alice plan on October 18, 2026?") == "2026-10-18"
    assert day("２０２６年１月１０日にアリスは何を計画しましたか？") == "2026-01-10"
    assert day("What did Alice plan on September 31, 2026?") == ""
    assert day("What did Alice plan on 2026-02-30?") == ""
    assert day("What happened with order 4521-33-90?") == ""


def test_cjk_directional_questions_are_not_pinned_to_the_anchor_day():
    """A question about the time before, after, or since a date keeps an open window."""
    from memoket_kite.pipeline.retrieve import QuestionAnchors, _normalize_time_filters

    for question in (
        "2026年1月10日之后Alice计划了什么？",
        "2026年1月10日之前Alice计划了什么？",
        "2026年1月10日以后Alice计划了什么？",
        "自2026年1月10日以来Alice做了什么？",
        "２０２６年１月１０日以降にアリスは何をしましたか？",
    ):
        queries = [{"select": "facts", "where": {}}]
        _normalize_time_filters(queries, question, QuestionAnchors(day="2026-01-10"))
        assert "time" not in queries[0]["where"], question

    queries = [{"select": "facts", "where": {}}]
    _normalize_time_filters(
        queries, "2026年1月10日Alice计划了什么？", QuestionAnchors(day="2026-01-10")
    )
    assert queries[0]["where"]["time"] == ["2026-01-10", "2026-01-10"]
