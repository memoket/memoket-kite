from pathlib import Path

from memoket_kite.core.algebra import Store, execute_plan

DEMO = Path(__file__).parents[2] / "examples" / "data" / "demo_codebook.xml"


def _load():
    return Store.load([str(DEMO)])


def test_topic_closure_returns_expected_evidence():
    store, vocab = _load()
    plan = {
        "queries": [
            {
                "select": "facts",
                "where": {"topics": [{"code": "activities", "closure": True}]},
            }
        ],
        "intent": "factual",
    }

    rows, trace = execute_plan(store, vocab, plan, speakers=store.speakers)

    assert [row["id"] for row in rows] == ["s1F1"]
    assert [stage["stage"] for stage in trace.stages] == ["expand", "refine", "pack"]


def test_event_time_is_distinct_from_source_time():
    store, vocab = _load()
    plan = {
        "queries": [
            {
                "select": "facts",
                "where": {
                    "topics": [{"code": "activities", "closure": True}],
                    "time": ["2026-10-18", "2026-10-18"],
                },
            }
        ],
        "intent": "factual",
    }

    rows, _ = execute_plan(store, vocab, plan, speakers=store.speakers)

    assert [row["id"] for row in rows] == ["s1F1"]
    assert store.units["s1"].date == "2026-01-10"


def test_line_query_combines_sessions_without_duplicates():
    store, vocab = _load()
    plan = {"queries": [{"select": "lines", "where": {"units": ["s2", "s1", "s1", "missing"]}}]}

    rows, _ = execute_plan(store, vocab, plan)

    assert [row["id"] for row in rows] == ["s1L1", "s2L1"]


def test_line_query_does_not_scan_unrelated_sessions():
    store, vocab = _load()

    class SelectedLinesOnly(dict):
        def __iter__(self):
            raise AssertionError("bound line retrieval must not scan the corpus")

        def __getitem__(self, key):
            assert key == "s2L1", "bound line retrieval must not read unrelated lines"
            return super().__getitem__(key)

    store.lines = SelectedLinesOnly(store.lines)
    plan = {"queries": [{"select": "lines", "where": {"units": ["s2"], "grep": "race"}}]}

    rows, _ = execute_plan(store, vocab, plan)

    assert [row["id"] for row in rows] == ["s2L1"]
