from copy import deepcopy

import pytest
from jsonschema import ValidationError

from benchmarks.multihop import adapter, passage_index
from benchmarks.multihop.musique import index, qa
from memoket_kite.core.algebra import SubQuery, Where, execute


def test_topic_wire_distinguishes_roots_from_children():
    source = adapter.document("Catalog", "A catalog of maps.")
    metadata = {"entities": [], "topics": [],
                "facets": {"object": [], "event": []}}
    for root in index.ROOTS:
        for code, parent in ((root, None), (root + "_maps", root), (root[:-1], root)):
            metadata["topics"] = [{"code": code, "parent": parent}]
            assert index.project_metadata(metadata, source) == (
                {"entities": [], "aliases": [], "topics": metadata["topics"],
                 "facets": {"object": [], "place": [], "event": []}},
                [],
            )
        for code, parent in ((root, root), (root, "people"), (root + "_maps", None)):
            metadata["topics"] = [{"code": code, "parent": parent}]
            with pytest.raises(ValidationError):
                index.project_metadata(metadata, source)


def test_conflicting_alias_does_not_discard_independent_passage_metadata(tmp_path):
    first = adapter.document("Springfield", "Springfield is in Illinois.")
    second = adapter.document(
        "Another Place", "Another Place is also called Springfield and Old Town. Rivertown is nearby."
    )
    third = adapter.document("Historic Site", "Historic Site, formerly called Old Town, is in Ohio.")
    first_metadata = {
        "entities": [{"name": "Springfield", "type": "place"}],
        "aliases": [],
        "topics": [{"code": "geography", "parent": None}],
        "facets": {"object": [], "place": ["Springfield"], "event": []},
    }
    second_metadata = deepcopy(first_metadata)
    second_metadata["entities"] += [
        {"name": "Another Place", "type": "place"}, {"name": "Rivertown", "type": "place"},
        {"name": "Old Town", "type": "place"},
    ]
    second_metadata["aliases"] = [
        {"canonical": "Another Place", "alias": "Springfield"},
        {"canonical": "Another Place", "alias": "Old Town"},
    ]
    second_metadata["facets"]["place"].append("Rivertown")
    third_metadata = {
        **first_metadata,
        "entities": [{"name": name, "type": "place"} for name in ("Historic Site", "Old Town")],
        "aliases": [{"canonical": "Historic Site", "alias": "Old Town"}],
        "facets": {"object": [], "place": [], "event": []},
    }
    results = {
        doc["id"]: {"status": "ok", "index": passage_index.validate(meta, doc)}
        for doc, meta in [(first, first_metadata), (second, second_metadata), (third, third_metadata)]
    }
    before = deepcopy(results)
    report = passage_index.assemble(
        [first, second, third], results, tmp_path, scoped_probes=True,
        admit_metadata=index.admit_metadata,
    )
    store, vocab = passage_index.load_index(tmp_path / "index.xml")
    rows, _ = execute(store, vocab, [SubQuery(where=Where(entities=["another_place"]))])
    assert passage_index.render_sources(rows, store)[0]["text"] == second["title"] + "\n" + second["text"]
    fact = store.facts[second["id"] + "F1"]
    assert set(fact.entities) == {"another_place", "rivertown"}
    assert fact.topics == ("geography",)
    assert fact.place == ("rivertown",)
    assert vocab.resolve_entity("Springfield") == "springfield"
    assert vocab.resolve_entity("Old Town") == "historic_site"
    assert list(store.units) == [first["id"], second["id"], third["id"]]
    assert report["metadata_omissions"] == [{"id": second["id"], "aliases": second_metadata["aliases"]}]
    assert report["raw_only_documents"] == 0
    assert results == before


def test_wire_place_and_alias_declarations_project_to_grounded_native_metadata():
    source = adapter.document(
        "Old Harbor", "Old Harbor is also called North Port. Ada Reed wrote a guide."
    )
    wire = {
        "entities": [
            {"name": "Ada Reed", "type": "person", "aliases": []},
            {"name": "Old Harbor", "type": "place", "aliases": ["North Port"], "place_facet": True},
        ],
        "topics": [{"code": "geography", "parent": None}],
        "facets": {"object": ["guide"], "event": []},
    }
    original = deepcopy(wire)
    native, rejected = index.project_metadata(wire, source)
    assert rejected == []
    assert native["aliases"] == [{"canonical": "Old Harbor", "alias": "North Port"}]
    assert native["facets"]["place"] == ["Old Harbor"]
    assert {entry["name"]: entry["type"] for entry in native["entities"]} == {
        "Ada Reed": "person", "Old Harbor": "place", "North Port": "place",
    }
    assert wire == original

    wire["entities"].append({"name": "North Port", "type": "work", "aliases": []})
    native, rejected = index.project_metadata(wire, source)
    assert native["aliases"] == []
    assert {"name": "North Port", "type": "work"} in native["entities"]
    assert rejected == [{"field": "alias", "name": "North Port", "reason": "ambiguous_alias"}]
    wire["entities"].pop()
    wire["entities"][1]["aliases"] = ["Invented Port"]
    native, rejected = index.project_metadata(wire, source)
    assert native["facets"]["place"] == ["Old Harbor"]
    assert native["aliases"] == []
    assert rejected == [{"field": "alias", "name": "Invented Port", "reason": "absent_from_source"}]


def test_invalid_entities_and_place_limit_do_not_discard_grounded_postings(tmp_path):
    names = ["Old Harbor", "North Port", "Rivertown", "Hill Road", "West Bank"]
    source = adapter.document("Travel notes", ", ".join(names))
    wire = {
        "entities": [
            {"name": name, "type": "place", "aliases": [], "place_facet": True}
            for name in [*names, "Invented Port"]
        ],
        "topics": [{"code": "geography", "parent": None}],
        "facets": {"object": [], "event": []},
    }
    native, rejected = index.project_metadata(wire, source)
    assert rejected == [
        {"field": "entity", "name": "Invented Port", "reason": "absent_from_source"},
        {"field": "place_facet", "names": names, "reason": "over_limit"},
    ]
    sid = source["id"]
    passage_index.assemble(
        [source], {sid: {"status": "ok", "index": native, "raw_index": wire}}, tmp_path,
        scoped_probes=True, admit_metadata=index.admit_metadata,
    )
    store, vocab = passage_index.load_index(tmp_path / "index.xml")
    assert vocab.resolve_entity("Invented Port") is None
    assert store.facts[sid + "F1"].place == ()
    rows, _ = execute(store, vocab, [SubQuery(where=Where(entities=["west_bank"]))])
    assert passage_index.render_sources(rows, store)[0]["text"] == source["title"] + "\n" + source["text"]


def test_musique_compile_uses_projected_units_and_native_validation():
    schema = qa.response_format()["json_schema"]["schema"]
    where = dict.fromkeys(schema["$defs"]["stages_units_where"]["properties"])
    first = {"name": "s1", "select": "units", "where": {**where, "grep": "title"}, "pipe": []}
    second = {"name": "s2", "select": "units", "where": {**where, "units": "$s1"}, "pipe": []}
    wire = {"plan": {"intent": "factual", "stages": [first, second]}}
    with pytest.raises(ValidationError):
        qa.decode(wire)
    second["where"]["units"] = "$s1.units"
    assert qa.decode(wire)["stages"][1]["where"]["units"] == "$s1.units"
    first["where"]["grep"] = "(?i)title|(?i)heading"
    with pytest.raises(RuntimeError, match="invalid plan grep pattern"):
        qa.decode(wire)


def test_terminal_title_reservation_keeps_plan_passages_and_budget(monkeypatch):
    from types import SimpleNamespace

    from benchmarks.multihop import evidence_delivery

    monkeypatch.setattr(evidence_delivery.ledger, "require_exact_tokenizer", lambda reason: None)
    monkeypatch.setattr(evidence_delivery.ledger, "text_tokens", len)
    bridge = {"type": "fact", "id": "bridge-fact", "unit": "bridge"}
    noise = {"type": "line", "id": "noise-line", "unit": "noise"}
    terminal = {"type": "unit", "id": "terminal"}
    scope = {"type": "unit", "id": "scope"}
    original = [bridge, noise]
    stages = [
        {"name": "lookup", "select": "units", "where": {"grep": "Earlier place"}},
        {"name": "bridge", "select": "facts", "where": {"units": "$lookup.units"}},
        {"name": "last", "select": "units", "where": {"grep": "Known subject"}},
    ]
    result = SimpleNamespace(plan={"stages": stages}, rows=original)
    store = SimpleNamespace(units={
        "scope": SimpleNamespace(title="Earlier place"),
        "terminal": SimpleNamespace(title="Known subject (author)"),
    })
    documents = {"bridge": "Bridge", "noise": "Long distractor", "terminal": "Result", "scope": "Scope"}

    def render(pack):
        units = [row.get("unit", row["id"]) for row in pack.rows]
        return "".join(documents[unit] for unit in units), {}, [{"unit": unit} for unit in units]

    prompt, _, sources, swaps = evidence_delivery.reserve_terminal_titles(
        result, store, {"lookup": [scope], "last": [terminal]}, [bridge, terminal],
        [bridge, noise, terminal], render,
    )
    assert {source["unit"] for source in sources} == {"bridge", "terminal"}
    assert len(prompt) <= len(render(result)[0])
    assert swaps == [{"added": "terminal", "removed": "noise"}]
    assert result.rows is original


def test_answer_pack_recovers_duplicate_whole_passage_slots(monkeypatch):
    from types import SimpleNamespace

    from benchmarks.multihop import passage_qa, profile
    from memoket_kite.pipeline.retrieve import RetrievalResult

    def line(uid):
        return {"type": "line", "unit": uid, "id": uid + "L1"}

    store = SimpleNamespace(
        units={uid: SimpleNamespace(title=uid) for uid in ("a", "b", "c", "d")},
        lines_by_unit={uid: [uid + "L1"] for uid in ("a", "b", "c", "d")},
        lines={uid + "L1": SimpleNamespace(text=uid + " original") for uid in ("a", "b", "c", "d")},
    )
    rows = [{"type": "fact", "unit": "a", "id": "aF1"}, line("a"), {"type": "unit", "id": "b"}]
    result = RetrievalResult("Which?", {}, rows, [], [], [], False, "factual", 3, 5,
                             candidate_rows=[line("unknown"), line("a"), line("c"), line("d")])
    schema = {"json_schema": {"schema": {"properties": {
        "evidence": {}, "observations": {"items": {"properties": {"evidence": {}}}},
    }}}}
    monkeypatch.setattr(profile, "TOKEN_CAP", 0)
    prompt, fmt, sources = passage_qa.render_distinct_passages(result, store, "Which?", schema)
    assert [source["unit"] for source in sources] == ["a", "b", "c"]
    assert prompt.count("a original") == 1
    assert fmt["json_schema"]["schema"]["properties"]["evidence"]["items"]["enum"] == ["aL1", "bL1", "cL1"]
    assert result.rows is rows
    assert "candidate_rows" not in result.as_dict()

    original = passage_qa.render(result, store, "Which?", schema)
    monkeypatch.setattr(profile, "TOKEN_CAP", 100)
    assert passage_qa.render_distinct_passages(result, store, "Which?", schema) == original


def test_open_document_question_requires_a_value_but_polar_question_allows_binary():
    from types import SimpleNamespace
    from jsonschema import Draft202012Validator

    schema = {"json_schema": {"schema": {"properties": {
        "answer": {"type": "string"}, "evidence": {},
        "observations": {"items": {"properties": {"evidence": {}}}},
    }}}}
    result = SimpleNamespace(plan={}, rows=[], aggregates=[])
    for question, allows_binary in [
        ("  Where did the writer live?", False),
        ("Which author was born earlier?", False),
        ("Did the writer live there?", True),
        ("Was the author's birthplace the same?", True),
    ]:
        _, response_format, _ = qa.render_answer(result, None, question, schema)
        answer = response_format["json_schema"]["schema"]["properties"]["answer"]
        validator = Draft202012Validator(answer)
        for value in ("Yes", "NO", "yEs", "no"):
            assert validator.is_valid(value) == allows_binary
        for value in ("A", "LA", "NY", "York", "No information"):
            assert validator.is_valid(value)
        assert not validator.is_valid("")
    assert schema["json_schema"]["schema"]["properties"]["answer"] == {"type": "string"}


def test_compile_feedback_shares_full_sources_once_for_all_hit_row_types():
    from types import SimpleNamespace

    store = SimpleNamespace(
        units={uid: SimpleNamespace(title=uid) for uid in ("a", "b")},
        lines_by_unit={"a": ["aL1"], "b": ["bL1"]},
        lines={
            "aL1": SimpleNamespace(text="a\nComplete first passage."),
            "bL1": SimpleNamespace(text="b\nComplete second passage."),
        },
    )
    rows = [
        {"type": "fact", "id": "aF1", "unit": "a", "date": "2000-01-01"},
        {"type": "line", "id": "aL1", "unit": "a", "date": "2000-01-01"},
        {"type": "unit", "id": "b", "date": "2000-01-01"},
    ]
    assert qa.render_compile_feedback(rows, store) == [
        "[aL1]\na\nComplete first passage.", "[bL1]\nb\nComplete second passage.",
    ]


def test_passage_stages_keep_rank_and_do_not_bind_storage_dates(tmp_path):
    from types import SimpleNamespace
    from memoket_kite.core.algebra import execute_plan

    path = tmp_path / "passages.xml"
    path.write_text(
        """<codebook><vocab/><timeline>
        <session id="a" title="Other" date="2000-01-01"><fact id="aF1">Bridge</fact></session>
        <session id="b" title="Another" date="2000-01-01"><fact id="bF1">Bridge</fact></session>
        <session id="z" title="Bridge" date="2000-01-01"><fact id="zF1">Bridge</fact></session>
        </timeline></codebook>"""
    )
    store, vocab = passage_index.load_index(path)
    profile = SimpleNamespace()
    qa.configure_profile(profile)
    plan = {"stages": [
        {"name": "title", "select": "units", "where": {"grep": "Bridge"}},
        {"name": "by_date", "select": "facts", "where": {"time": "$title.date"}},
    ]}
    rows, trace = execute_plan(
        store, vocab, plan, budget=2,
        rank_stage_rows=profile.RANK_STAGE_ROWS, bind_stage_dates=profile.BIND_STAGE_DATES,
    )
    assert [row["id"] for row in rows] == ["z", "a"]
    assert {"stage": "stage", "name": "by_date", "skipped": "unresolved_ref"} in trace.stages


def test_passage_unit_stage_can_reserve_a_hit_before_earlier_stage_fills_budget(tmp_path):
    from types import SimpleNamespace

    from memoket_kite.core.algebra import execute_plan

    path = tmp_path / "passages.xml"
    path.write_text(
        """<codebook><vocab/><timeline>
        <session id="a" title="Other" date="2000-01-01"><fact id="aF1">Anchor</fact></session>
        <session id="b" title="Other" date="2000-01-01"><fact id="bF1">Anchor</fact></session>
        <session id="c" title="Other" date="2000-01-01"><fact id="cF1">Anchor</fact></session>
        <session id="d" title="Bridge" date="2000-01-01"><fact id="dF1">Terminal passage</fact></session>
        </timeline></codebook>"""
    )
    store, vocab = passage_index.load_index(path)
    plan = {"stages": [
        {"name": "wide", "select": "facts", "where": {"grep": "Anchor"}},
        {"name": "bridge", "select": "units", "where": {"grep": "Bridge"}},
    ]}
    ordinary, _ = execute_plan(store, vocab, plan, budget=3, pack_v2=True)
    profile = SimpleNamespace()
    qa.configure_profile(profile)
    passages, _ = execute_plan(
        store, vocab, plan, budget=3, pack_v2=True, reserve_stage_units=profile.RESERVE_STAGE_UNITS,
    )
    assert [row["id"] for row in ordinary] == ["aF1", "bF1", "cF1"]
    assert [row["id"] for row in passages] == ["aF1", "bF1", "d"]
    assert passages[-1]["_plan_reserved"]
