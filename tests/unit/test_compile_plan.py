"""Focused tests for compilation-plan cache invalidation."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from memoket_kite.core.algebra import FactRecord, Store, Unit
from memoket_kite.core.vocab import Vocab
from memoket_kite.pipeline import compile_plan as compilation


class Profile:
    KINDS = ("event", "plan")
    COMPILE_PROMPT = (
        "TREE: {tree}\nENTITIES: {entities}\nKINDS: {kinds}\n"
        "UNITS: {units}\nTODAY: {today}\nQUESTION: {question}"
    )


def _vocab(entity: str) -> Vocab:
    vocab = Vocab()
    vocab.register_entity(entity)
    return vocab


def _store(unit_id: str) -> Store:
    store = Store()
    store.units[unit_id] = Unit(unit_id, "2026-01-01", "", "", 0, 0)
    return store


def _fake_compiler(calls: list[tuple[str, str, float]]):
    def compile_response(prompt: str, *, model: str, temperature: float) -> dict:
        calls.append((prompt, model, temperature))
        return {
            "queries": [{"select": "facts", "where": {}}],
            "intent": "factual",
        }

    return compile_response


def test_cache_hits_only_when_the_complete_prompt_context_matches(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))

    kwargs = {
        "profile": Profile,
        "model": "test:model",
        "cache_dir": str(tmp_path),
    }
    compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)
    compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)
    compilation.compile_plan("Who attended?", _vocab("bob"), _store("s1"), **kwargs)
    compilation.compile_plan("Who attended?", _vocab("bob"), _store("s2"), **kwargs)

    assert len(calls) == 3
    assert "ENTITIES: alice" in calls[0][0]
    assert "ENTITIES: bob" in calls[1][0]
    assert "UNITS: s2=2026-01-01" in calls[2][0]


def test_compiler_retries_candidates_with_invalid_plan_schema(monkeypatch):
    responses = iter(
        (
            {"queries": [{"select": "facts", "where": "all"}], "intent": "factual"},
            {"queries": [{"select": "facts", "where": {}}], "intent": "factual"},
        )
    )
    monkeypatch.setattr(compilation, "llm_json", lambda *args, **kwargs: next(responses))

    plan = compilation.compile_plan(
        "Who attended?",
        _vocab("alice"),
        _store("s1"),
        Profile,
        model="test:model",
        scorer=lambda candidate: 1.0,
        n_candidates=2,
    )

    assert plan == {"queries": [{"select": "facts", "where": {}}], "intent": "factual"}


def test_compile_prompt_orders_same_day_units_by_timestamp_then_id():
    store = Store()
    store.units["a-late"] = Unit("a-late", "2026-01-01", "2026-01-01T18:00:00", "", 0, 0)
    store.units["z-early"] = Unit("z-early", "2026-01-01", "2026-01-01T09:00:00", "", 0, 0)

    prompt = compilation.render_compile_prompt("When?", _vocab("alice"), store, Profile)

    assert prompt.index("z-early=2026-01-01") < prompt.index("a-late=2026-01-01")


def test_cache_identity_includes_provider_endpoint(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))
    kwargs = {
        "profile": Profile,
        "model": "same-model-name",
        "cache_dir": str(tmp_path),
    }

    monkeypatch.setenv("OPENAI_BASE_URL", "https://one.example/v1")
    compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)
    compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://two.example/v1")
    compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)

    assert len(calls) == 2


def test_cache_fingerprint_includes_model_scorer_mode_and_candidate_count(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))
    vocab, store = _vocab("alice"), _store("s1")

    def scorer(plan: dict) -> float:
        return float(len(plan["queries"]))

    base = {
        "question": "Who attended?",
        "vocab": vocab,
        "store": store,
        "profile": Profile,
        "cache_dir": str(tmp_path),
    }
    compilation.compile_plan(**base, model="model:a", n_candidates=2)
    compilation.compile_plan(**base, model="model:a", n_candidates=2)
    compilation.compile_plan(**base, model="model:b", n_candidates=2)
    scored = {**base, "scorer": scorer, "scorer_cache_key": "test-scorer-v1"}
    compilation.compile_plan(**scored, model="model:a", n_candidates=2)
    compilation.compile_plan(**scored, model="model:a", n_candidates=2)
    compilation.compile_plan(**scored, model="model:a", n_candidates=3)

    assert len(calls) == 7
    assert [temperature for _, _, temperature in calls[-3:]] == [0.0, 0.8, 0.8]


def test_scored_cache_fingerprint_includes_symbolic_store_content(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))
    vocab = _vocab("alice")

    def store_with_fact(text: str) -> Store:
        store = _store("s1")
        store.add_fact(
            FactRecord(
                "s1F1",
                "s1",
                "2026-01-01",
                "",
                "event",
                "alice",
                "high",
                (),
                (),
                (),
                text,
            )
        )
        return store

    def scorer(plan: dict) -> float:
        return float(len(plan["queries"]))

    kwargs = {
        "profile": Profile,
        "model": "test:model",
        "scorer": scorer,
        "n_candidates": 2,
        "cache_dir": str(tmp_path),
        "scorer_cache_key": "test-scorer-v1",
    }
    compilation.compile_plan("Who attended?", vocab, store_with_fact("first"), **kwargs)
    compilation.compile_plan("Who attended?", vocab, store_with_fact("first"), **kwargs)
    compilation.compile_plan("Who attended?", vocab, store_with_fact("changed"), **kwargs)
    store_with_changed_idf = store_with_fact("changed")
    store_with_changed_idf._index_text("external lexical term")
    compilation.compile_plan("Who attended?", vocab, store_with_changed_idf, **kwargs)

    assert len(calls) == 6


def test_scored_cache_fingerprint_includes_entity_object_bridges(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))
    vocab = _vocab("new")

    def store_with_bridge(entity_code: str) -> Store:
        store = _store("s1")
        store.add_fact(
            FactRecord(
                "s1F1",
                "s1",
                "2026-01-01",
                "",
                "event",
                "alice",
                "high",
                (),
                ("new",),
                (),
                "same fact",
                obj=("device",),
            )
        )
        store.obj_of = {entity_code: {"device"}}
        return store

    kwargs = {
        "profile": Profile,
        "model": "test:model",
        "scorer": lambda plan: float(len(plan["queries"])),
        "n_candidates": 2,
        "cache_dir": str(tmp_path),
        "scorer_cache_key": "test-scorer-v1",
    }
    compilation.compile_plan("Who attended?", vocab, store_with_bridge("old"), **kwargs)
    compilation.compile_plan("Who attended?", vocab, store_with_bridge("new"), **kwargs)

    assert len(calls) == 4


def test_scored_cache_is_disabled_without_an_explicit_scorer_key(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))

    compilation.compile_plan(
        "Who attended?",
        _vocab("alice"),
        _store("s1"),
        Profile,
        model="test:model",
        scorer=lambda plan: 1.0,
        n_candidates=2,
        cache_dir=str(tmp_path),
    )
    compilation.compile_plan(
        "Who attended?",
        _vocab("alice"),
        _store("s1"),
        Profile,
        model="test:model",
        scorer=lambda plan: 1.0,
        n_candidates=2,
        cache_dir=str(tmp_path),
    )

    assert len(calls) == 4
    assert list(tmp_path.iterdir()) == []


def test_corrupt_cache_entry_is_recompiled_and_replaced(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compilation, "llm_json", _fake_compiler(calls))
    kwargs = {
        "profile": Profile,
        "model": "test:model",
        "cache_dir": str(tmp_path),
    }
    compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)
    cache_path = next(tmp_path.glob("*.json"))
    cache_path.write_text('{"queries":', encoding="utf-8")

    plan = compilation.compile_plan("Who attended?", _vocab("alice"), _store("s1"), **kwargs)

    assert len(calls) == 2
    assert plan["intent"] == "factual"
    assert json.loads(cache_path.read_text(encoding="utf-8")) == plan


def test_cache_publish_keeps_the_previous_complete_entry_visible(tmp_path, monkeypatch):
    cache_path = tmp_path / "plan.json"
    old_plan = {
        "queries": [{"select": "facts", "where": {"grep": "old"}}],
        "intent": "factual",
    }
    new_plan = {
        "queries": [{"select": "facts", "where": {"grep": "new"}}],
        "intent": "factual",
    }
    compilation._write_cached_plan(str(cache_path), old_plan)

    write_started = Event()
    finish_write = Event()
    real_dump = compilation.json.dump

    def paused_dump(plan, stream, **kwargs):
        stream.write('{"partial":')
        stream.flush()
        write_started.set()
        assert finish_write.wait(timeout=2)
        stream.seek(0)
        stream.truncate()
        real_dump(plan, stream, **kwargs)

    monkeypatch.setattr(compilation.json, "dump", paused_dump)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(compilation._write_cached_plan, str(cache_path), new_plan)
        assert write_started.wait(timeout=2)
        try:
            assert compilation._read_cached_plan(str(cache_path)) == old_plan
        finally:
            finish_write.set()
        future.result(timeout=2)

    assert compilation._read_cached_plan(str(cache_path)) == new_plan
