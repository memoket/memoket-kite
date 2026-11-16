from pathlib import Path

import pytest

import memoket_kite
from memoket_kite.core.algebra import Store, execute_plan
from memoket_kite.research import (
    Codebook,
    CodebookLoadError,
    InvalidQueryError,
    QueryPlan,
    Reasoner,
    ReasonerConfigurationError,
    Sort,
    SymbolicQuery,
    TimeRange,
    TopicMatch,
)

DEMO = Path(__file__).parents[2] / "examples" / "data" / "demo_codebook.xml"


class Profile:
    KINDS = ("plan", "event")
    COMPILE_PROMPT = "compile"
    ANSWER_PROMPT = "answer"
    ATTRIBUTION_ANSWER = "{who}: {quote}"

    @staticmethod
    def keywords(question: str) -> list[str]:
        return question.lower().split()


def test_top_level_exports_are_importable():
    assert memoket_kite.__all__ == [
        "Memory",
        "Fact",
        "Answer",
        "KiteError",
        "ConfigurationError",
        "StorageError",
        "ProviderError",
        "QueryError",
        "__version__",
    ]
    assert memoket_kite.Memory is not None
    assert memoket_kite.Fact is not None
    assert memoket_kite.Answer is not None
    # Provider failures share the package's public error root.
    assert issubclass(memoket_kite.ProviderError, memoket_kite.KiteError)


def test_research_names_are_not_top_level_exports():
    assert not hasattr(memoket_kite, "Codebook")
    assert not hasattr(memoket_kite, "QueryPlan")


def test_load_inspect_and_multiple_shards():
    book = Codebook.load(DEMO)
    summary = book.inspect().summary()

    assert summary.units == 2
    assert summary.facts == 2
    assert summary.speakers == ("alice", "bob")
    assert summary.start_date == "2026-01-10"
    assert book.inspect().sources("s1F1")[0].id == "s1L1"
    assert {topic.code for topic in book.inspect().topics()} == {
        "activities",
        "marathon_training",
        "travel",
        "race_travel",
    }

    sharded = Codebook.load([DEMO, DEMO])
    assert sharded.inspect().summary().shards == 2
    assert sharded.inspect().summary().facts == 2


def test_load_errors_are_public(tmp_path):
    with pytest.raises(CodebookLoadError):
        Codebook.load(tmp_path / "missing.xml")

    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<codebook>", encoding="utf-8")
    with pytest.raises(CodebookLoadError):
        Codebook.load(malformed)

    no_timeline = tmp_path / "no-timeline.xml"
    no_timeline.write_text("<codebook><vocab /></codebook>", encoding="utf-8")
    with pytest.raises(CodebookLoadError):
        Codebook.load(no_timeline)

    invalid_structure = tmp_path / "invalid-structure.xml"
    invalid_structure.write_text(
        """<codebook><vocab/><timeline>
        <session id="s1" date="2025-02-29"><fact id="f1"/><line id="l1"/></session>
        </timeline></codebook>""",
        encoding="utf-8",
    )
    with pytest.raises(CodebookLoadError, match="invalid Unit date"):
        Codebook.load(invalid_structure)

    invalid_vocab = tmp_path / "invalid-vocab.xml"
    invalid_vocab.write_text(
        """<codebook><vocab><topic/></vocab><timeline>
        <session id="s1" date="2026-01-01"><fact id="f1"/><line id="l1"/></session>
        </timeline></codebook>""",
        encoding="utf-8",
    )
    with pytest.raises(CodebookLoadError, match="Topic code must be non-empty"):
        Codebook.load(invalid_vocab)


def test_symbolic_query_matches_internal_executor():
    book = Codebook.load(DEMO)
    query = SymbolicQuery(
        topics=(TopicMatch("activities"),),
        speakers=("alice",),
        kinds=("plan",),
        events=("competition",),
        time=TimeRange("2026-10-18", "2026-10-18"),
        sort=Sort("time"),
        limit=5,
    )
    public = book.query(query)

    store, vocab = Store.load([str(DEMO)])
    rows, trace = execute_plan(
        store,
        vocab,
        query.to_plan().to_dict(),
        speakers=store.speakers,
    )

    assert [item.id for item in public.evidence] == [
        row["id"] for row in rows if row["type"] != "count"
    ]
    assert [event.stage for event in public.trace.events] == [
        stage["stage"] for stage in trace.stages
    ]
    fact = public.evidence[0]
    assert fact.event_time == "2026-10-18"
    assert fact.source_time == "2026-01-10"
    assert fact.sources[0].text.startswith("I started training")
    with pytest.raises(TypeError):
        fact.attributes["duration"] = "P1D"


def test_lines_units_aggregate_and_invalid_regex():
    book = Codebook.load(DEMO)

    lines = book.query(SymbolicQuery(target="lines", text_pattern="started training"))
    assert [item.id for item in lines.evidence] == ["s1L1"]

    units = book.query(SymbolicQuery(target="units", aggregate="count"))
    assert [item.id for item in units.evidence] == ["s1", "s2"]
    assert units.aggregates[0].value == 2
    assert units.aggregates[0].target == "units"

    with pytest.raises(InvalidQueryError):
        SymbolicQuery(text_pattern="[")


def test_staged_plan_and_copy_isolation():
    raw = {
        "stages": [
            {
                "name": "anchor",
                "select": "units",
                "where": {"grep": "training"},
                "pipe": [{"op": "head", "n": 1}],
            },
            {
                "select": "facts",
                "where": {"units": "$anchor.units", "kind": ["plan"]},
            },
        ],
        "intent": "factual",
    }
    plan = QueryPlan.from_dict(raw)
    raw["stages"][0]["where"]["grep"] = "changed"

    result = Codebook.load(DEMO).execute(plan)

    assert plan.to_dict()["stages"][0]["where"]["grep"] == "training"
    assert result.evidence[0].id == "s1F1"
    assert any(event.stage == "stage" for event in result.trace.events)


def test_query_validation():
    with pytest.raises(InvalidQueryError):
        SymbolicQuery(target="unknown")
    with pytest.raises(InvalidQueryError):
        SymbolicQuery(limit=0)
    with pytest.raises(InvalidQueryError):
        SymbolicQuery(target="lines", topics=(TopicMatch("activities"),))
    with pytest.raises(InvalidQueryError):
        SymbolicQuery(target="units", speakers=("alice",))
    with pytest.raises(InvalidQueryError, match="TimeRange"):
        SymbolicQuery(time="2026-01")
    with pytest.raises(InvalidQueryError, match="Sort"):
        SymbolicQuery(sort="time")
    with pytest.raises(InvalidQueryError, match="YYYY"):
        TimeRange("2026-02-31")
    with pytest.raises(InvalidQueryError, match="after end"):
        TimeRange("2026-02", "2026-01-31")
    with pytest.raises(InvalidQueryError, match="YYYY"):
        TimeRange(2026, "2027")
    with pytest.raises(InvalidQueryError):
        QueryPlan.from_dict({"intent": "factual"})
    with pytest.raises(InvalidQueryError):
        Codebook.load(DEMO).execute({"queries": []})


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({"select": "facts", "where": []}, id="empty-list-where"),
        pytest.param({"select": "facts", "where": False}, id="false-where"),
        pytest.param({"select": "facts", "where": {"spekar": ["alice"]}}, id="unknown-where-field"),
        pytest.param({"select": "facts", "where": {"who": "alice"}}, id="string-speaker-list"),
        pytest.param({"select": "facts", "where": {"kind": "plan"}}, id="string-kind-list"),
        pytest.param({"select": "facts", "where": {"time": "2026-01"}}, id="bare-time"),
        pytest.param(
            {"select": "facts", "where": {"time": ["2026-02-29"]}},
            id="impossible-time",
        ),
        pytest.param(
            {"select": "facts", "where": {"time": ["2026-2"]}},
            id="non-iso-time",
        ),
        pytest.param(
            {"select": "facts", "where": {"time": ["2026-02", "2026-01-31"]}},
            id="reversed-time",
        ),
        pytest.param(
            {"select": "facts", "where": {"time": ["$anchor.date"]}},
            id="reference-in-unstaged-plan",
        ),
        pytest.param(
            {"select": "facts", "where": {"conf_min": "maximum"}}, id="unknown-confidence"
        ),
        pytest.param({"select": "units", "where": {"kind": ["plan"]}}, id="ignored-unit-kind"),
        pytest.param(
            {"select": "lines", "where": {"kind": ["plan"]}},
            id="ignored-line-kind",
        ),
        pytest.param(
            {"select": "facts", "where": {"grep_all": ["["]}},
            id="invalid-grep-all",
        ),
        pytest.param({"select": "facts", "ignored": True}, id="ignored-entry-field"),
        pytest.param({"select": "facts", "pipe": {}}, id="empty-mapping-pipe"),
        pytest.param({"select": "facts", "pipe": False}, id="false-pipe"),
        pytest.param({"select": "facts", "pipe": [[]]}, id="non-mapping-step"),
        pytest.param(
            {
                "select": "facts",
                "where": {"topics": [{"code": "activities", "closure": "false"}]},
            },
            id="string-topic-closure",
        ),
        pytest.param(
            {"select": "facts", "where": {"topics": [{"code": "activities", "extra": 1}]}},
            id="ignored-topic-field",
        ),
        pytest.param(
            {"select": "facts", "where": {"topics": [{"code": 3}]}},
            id="integer-topic-code",
        ),
        pytest.param(
            {"select": "facts", "where": {"topics": [{"code": {"activities": True}}]}},
            id="mapping-topic-code",
        ),
        pytest.param({"select": "facts", "pipe": [{}]}, id="missing-op"),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "haed", "n": 1}]},
            id="unknown-op",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "sort", "key": "date", "desc": False}]},
            id="unsupported-sort-key",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "sort", "key": "t"}]},
            id="missing-sort-desc",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "sort", "key": "t", "desc": "false"}]},
            id="string-sort-desc",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "sort", "key": "t", "desc": 0}]},
            id="integer-sort-desc",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "head"}]},
            id="missing-head-n",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "head", "n": True}]},
            id="boolean-head-n",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "head", "n": 0}]},
            id="zero-head-n",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "tail", "n": 1.5}]},
            id="float-tail-n",
        ),
        pytest.param(
            {"select": "facts", "pipe": [{"op": "count", "n": 1}]},
            id="ignored-count-field",
        ),
        pytest.param(
            {"select": "lines", "pipe": [{"op": "head", "n": 1}]},
            id="ignored-line-pipe",
        ),
    ],
)
def test_query_plan_rejects_values_the_executor_would_coerce_or_ignore(entry):
    book = Codebook.load(DEMO)

    with pytest.raises(InvalidQueryError):
        book.execute(QueryPlan.from_dict({"queries": [entry]}))


@pytest.mark.parametrize(
    ("desc", "clip", "expected_id"),
    [
        pytest.param(False, "head", "s2F1", id="ascending-head"),
        pytest.param(True, "head", "s1F1", id="descending-head"),
        pytest.param(True, "tail", "s2F1", id="descending-tail"),
    ],
)
def test_query_plan_sort_direction_and_clipping_reach_execution(desc, clip, expected_id):
    plan = QueryPlan.from_dict(
        {
            "queries": [
                {
                    "select": "facts",
                    "where": {"kind": ["plan"]},
                    "pipe": [
                        {"op": "sort", "key": "t", "desc": desc},
                        {"op": clip, "n": 1},
                    ],
                }
            ]
        }
    )

    result = Codebook.load(DEMO).execute(plan)

    assert [item.id for item in result.evidence] == [expected_id]


@pytest.mark.parametrize(
    "constructor",
    [
        pytest.param(lambda: TopicMatch("activities", descendants="false"), id="topic-match"),
        pytest.param(lambda: Sort(descending="false"), id="sort"),
    ],
)
def test_typed_query_rejects_string_booleans(constructor):
    with pytest.raises(InvalidQueryError):
        constructor()


@pytest.mark.parametrize("code", [None, 3, b"activities"])
def test_topic_match_requires_a_non_empty_string_code(code):
    with pytest.raises(InvalidQueryError, match="non-empty string"):
        TopicMatch(code)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"alice": True}, id="mapping"),
        pytest.param(b"alice", id="bytes"),
        pytest.param(3, id="integer"),
        pytest.param(True, id="boolean"),
        pytest.param(1.5, id="float"),
        pytest.param(None, id="none"),
        pytest.param(["alice", 3], id="non-string-member"),
    ],
)
def test_symbolic_query_sequence_fields_reject_non_string_values(value):
    with pytest.raises(InvalidQueryError, match="entities must be"):
        SymbolicQuery(entities=value)


def test_symbolic_query_sequence_fields_accept_strings_and_string_iterables():
    query = SymbolicQuery(
        entities="alice",
        speakers=(speaker for speaker in ("alice", "bob")),
    )

    assert query.entities == ("alice",)
    assert query.speakers == ("alice", "bob")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(3, id="integer"),
        pytest.param(True, id="boolean"),
        pytest.param(["training"], id="list"),
        pytest.param({"pattern": "training"}, id="mapping"),
    ],
)
def test_symbolic_query_text_pattern_rejects_non_string_values(value):
    with pytest.raises(InvalidQueryError, match="text_pattern must be a string"):
        SymbolicQuery(text_pattern=value)


def test_symbolic_query_none_text_pattern_retains_empty_pattern_compatibility():
    assert SymbolicQuery(text_pattern=None).text_pattern == ""


@pytest.mark.parametrize(
    "time_filter",
    ["$anchor.date", ["$anchor.date", "$anchor.date"]],
)
def test_staged_plan_accepts_date_references(time_filter):
    plan = QueryPlan.from_dict(
        {
            "stages": [
                {"name": "anchor", "select": "units", "where": {}},
                {"select": "units", "where": {"time": time_filter}},
            ]
        }
    )

    assert plan.to_dict()["stages"][1]["where"]["time"] == time_filter


@pytest.mark.parametrize("time_filter", ["$anchor.unit", "$anchor.units"])
def test_staged_plan_rejects_non_date_references_in_time(time_filter):
    with pytest.raises(InvalidQueryError, match="one- or two-string list"):
        QueryPlan.from_dict(
            {
                "stages": [
                    {"name": "anchor", "select": "units", "where": {}},
                    {"select": "units", "where": {"time": time_filter}},
                ]
            }
        )


@pytest.mark.parametrize(
    "where",
    [
        pytest.param({"units": "$anchor.date"}, id="date-as-units"),
        pytest.param({"unit": "$anchor.date"}, id="date-as-unit"),
        pytest.param({"unit": "$anchor.units"}, id="units-as-unit"),
        pytest.param({"kind": "$anchor.units"}, id="reference-as-kind"),
        pytest.param({"kind": ["$anchor.units"]}, id="listed-reference-as-kind"),
    ],
)
def test_staged_plan_rejects_references_in_incompatible_fields(where):
    with pytest.raises(InvalidQueryError, match="unsupported stage reference"):
        QueryPlan.from_dict(
            {
                "stages": [
                    {"name": "anchor", "select": "units", "where": {}},
                    {"select": "facts", "where": where},
                ]
            }
        )


@pytest.mark.parametrize(
    "stages",
    [
        pytest.param(
            [
                {"name": "anchor", "select": "units", "where": {}},
                {"name": "anchor", "select": "units", "where": {}},
            ],
            id="duplicate-name",
        ),
        pytest.param(
            [{"name": "", "select": "units", "where": {}}],
            id="empty-name",
        ),
        pytest.param(
            [{"name": 3, "select": "units", "where": {}}],
            id="non-string-name",
        ),
        pytest.param(
            [
                {"name": "anchor", "select": "units", "where": {}},
                {"select": "units", "where": {"unit": "$typo.unit"}},
            ],
            id="unknown-reference",
        ),
        pytest.param(
            [
                {"name": "first", "select": "units", "where": {"unit": "$later.unit"}},
                {"name": "later", "select": "units", "where": {}},
            ],
            id="forward-reference",
        ),
        pytest.param(
            [
                {"select": "units", "where": {}},
                {"select": "units", "where": {"unit": "$s1.unit"}},
            ],
            id="reference-to-unnamed-stage",
        ),
    ],
)
def test_staged_plan_rejects_ambiguous_names_and_unresolved_references(stages):
    with pytest.raises(InvalidQueryError):
        QueryPlan.from_dict({"stages": stages})


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"queries": [{}], "intnet": "aggregate"}, id="misspelled-intent"),
        pytest.param({"queries": [{}], "intent": "totally_unknown"}, id="unknown-intent"),
        pytest.param({"queries": [{}], "debug": True}, id="ignored-top-level-field"),
        pytest.param({"queries": []}, id="empty-queries"),
        pytest.param({"stages": []}, id="empty-stages"),
        pytest.param({"queries": [{}], "stages": [{}]}, id="ambiguous-shapes"),
        pytest.param({"queries": [{}, {}, {}, {}]}, id="truncated-query"),
        pytest.param({"stages": [{}, {}, {}, {}, {}]}, id="truncated-stage"),
        pytest.param(
            {"queries": [{"pipe": [{"op": "count"}] * 5}]},
            id="truncated-pipe",
        ),
    ],
)
def test_query_plan_rejects_empty_or_silently_truncated_shapes(raw):
    with pytest.raises(InvalidQueryError):
        QueryPlan.from_dict(raw)


def test_reasoner_retrieval_normalizes_internal_rows(monkeypatch):
    def fake_retrieve(store, vocab, profile, question, **kwargs):
        return {
            "question": question,
            "plan": {"queries": [{"select": "facts", "where": {}}]},
            "rows": [
                {"type": "count", "n": 2, "of": "facts", "text": "count = 2"},
                {"type": "fact", "id": "s1F1", "score": 4.2},
            ],
            "used_fallback": True,
            "trace": [{"stage": "pack", "n": 1}],
        }

    monkeypatch.setattr("memoket_kite.pipeline.retrieve.retrieve", fake_retrieve)
    reasoner = Reasoner(
        Codebook.load(DEMO),
        profile=Profile,
        model="test:model",
    )

    result = reasoner.retrieve("race?", evidence_limit=5)

    assert result.question == "race?"
    assert result.evidence[0].id == "s1F1"
    assert result.aggregates[0].value == 2
    assert result.used_fallback is True
    assert result.trace.events[0].stage == "pack"


def test_reasoner_keeps_lexical_recovery_when_every_compiled_plan_is_invalid(monkeypatch):
    # Invalid provider candidates make the compiler choose its explicit
    # no-symbolic-match plan.  That plan still has to cross QueryPlan's public
    # validation boundary after lexical retrieval succeeds.
    monkeypatch.setattr(
        "memoket_kite.pipeline.compile_plan.llm_json",
        lambda *args, **kwargs: {"queries": []},
    )
    reasoner = Reasoner(Codebook.load(DEMO), profile=Profile, model="test:model")

    result = reasoner.retrieve("Which race is Alice training for?", evidence_limit=5)

    assert result.used_fallback is True
    assert result.evidence
    assert result.plan.to_dict()["queries"][0]["where"]["grep"] == "(?!)"


@pytest.mark.parametrize(
    ("pack_rows", "cited", "expected_id", "expected_type"),
    [
        ([{"id": "s1F1", "type": "fact", "src": "s1L1"}], ["s1L1"], "s1F1", "fact"),
        ([{"id": "s1L1", "type": "line", "src": "s1L1"}], ["s1L1"], "s1L1", "line"),
        (
            [
                {"id": "", "type": "count", "src": ""},
                {"id": "s1", "type": "unit", "src": ""},
            ],
            ["s1"],
            "s1",
            "unit",
        ),
    ],
)
def test_reasoner_answer_normalizes_all_return_shapes(
    monkeypatch,
    pack_rows,
    cited,
    expected_id,
    expected_type,
):
    def fake_answer(store, vocab, profile, question, **kwargs):
        return {
            "question": question,
            "plan": {"queries": [{"select": "facts", "where": {}}]},
            "n_evidence_rows": len(pack_rows),
            "pack_src": ["s1L1"],
            "pack_rows": pack_rows,
            "used_fallback": False,
            "answer": "Alice is preparing for the Shanghai Marathon.",
            "cited": cited,
            "trace": [{"stage": "pack"}, {"stage": "answer"}],
            "answer_prompt": "must not be public",
        }

    monkeypatch.setattr("memoket_kite.pipeline.answer.answer", fake_answer)
    result = Reasoner(
        Codebook.load(DEMO),
        profile=Profile,
        model="test:model",
    ).answer("Which race?")

    assert result.answer.startswith("Alice")
    assert result.evidence[0].id == expected_id
    assert result.evidence[0].type == expected_type
    assert result.evidence[0].cited is True
    assert result.citation_ids == tuple(cited)
    assert not hasattr(result, "answer_prompt")


def test_reasoner_requires_profile_and_model():
    class MissingProfile:
        pass

    book = Codebook.load(DEMO)
    with pytest.raises(ReasonerConfigurationError):
        Reasoner(book, profile=MissingProfile(), model="test:model")
    with pytest.raises(ReasonerConfigurationError):
        Reasoner(book, profile=Profile, model="")
    with pytest.raises(ReasonerConfigurationError, match="valid ISO date"):
        Reasoner(
            book,
            profile=Profile,
            model="test:model",
            reference_date="2026-02-31",
        )
    reasoner = Reasoner(book, profile=Profile, model="test:model")
    with pytest.raises(InvalidQueryError):
        reasoner.retrieve("")
    with pytest.raises(InvalidQueryError, match="valid ISO date"):
        reasoner.retrieve("question", reference_date="2026-02-31")
    with pytest.raises(InvalidQueryError, match="ISO date"):
        reasoner.answer("question", reference_date="not-a-date")


@pytest.mark.parametrize("bad", [True, 1.5, "3"])
def test_evidence_limit_rejects_non_integers(bad):
    """A sign check alone is not a type check: `1.5 < 0` and `True < 0` are
    both False, so a float or a bool would reach the row budget, and a str
    would raise a bare TypeError instead of the API's own error."""
    reasoner = Reasoner(Codebook.load(DEMO), profile=Profile, model="test:model")
    with pytest.raises(InvalidQueryError, match="must be an integer"):
        reasoner.retrieve("q", evidence_limit=bad)
    with pytest.raises(InvalidQueryError, match="must be an integer"):
        reasoner.answer("q", evidence_limit=bad)
