"""Offline unit tests for the symbolic retrieval core (no LLM)."""

import xml.etree.ElementTree as ET

import pytest

from memoket_kite.core.algebra import (
    FactRecord,
    Store,
    SubQuery,
    Unit,
    Where,
    execute,
    parse_plan,
)
from memoket_kite.core.vocab import Vocab, VocabError


def check(name, cond, info=""):
    assert cond, f"{name}: {info}"


def make_vocab():
    v = Vocab(admission_k=2, promote_units=2)
    v.define_root("硬件")
    v.define_root("营销")
    v.propose_topic("电机选型", "硬件", unit="c1", bucket="2026-01")
    v.propose_topic("静音电机", "电机选型", unit="c1", bucket="2026-01")
    v.promote("电机选型")
    v.promote("静音电机")
    return v


def test_vocab():
    v = make_vocab()
    # admission cap: c1 already spent 2 proposals
    r = v.propose_topic("保温", "硬件", unit="c1", bucket="2026-01")
    check("admission-cap folds to parent", r == "硬件", r)
    # orphan rejection
    r = v.propose_topic("外星话题", "不存在的父", unit="c2")
    check("orphan rejected", r is None, r)
    # resolve via alias
    v.alias_topic("motor_selection", "电机选型")
    check("alias resolve", v.resolve_topic("motor_selection") == "电机选型")
    # downset closure
    ds = v.downset("硬件", include_candidates=True)
    check("downset", ds == {"硬件", "电机选型", "静音电机"}, ds)
    ds0 = v.downset("硬件", max_depth=0)
    check("downset depth0", ds0 == {"硬件"}, ds0)
    # promotion trigger
    v.propose_topic("投放", "营销", unit="c2", bucket="2026-01")
    v.propose_topic("投放", "营销", unit="c3", bucket="2026-02")  # existing -> touch
    promoted = v.promotion_pass()
    check("promotion trigger", "投放" in promoted, promoted)
    # merge with rewrite map
    v.propose_topic("电机挑选", "硬件", unit="c4", bucket="2026-02")
    rw = v.merge_topics(["电机挑选"], "电机选型")
    check("merge rewrite map", rw == {"电机挑选": "电机选型"}, rw)
    check("merge alias", v.resolve_topic("电机挑选") == "电机选型")
    # move cycle rejected
    try:
        v.move_topic("硬件", ["静音电机"])
        check("cycle rejected", False, "no error")
    except VocabError:
        check("cycle rejected", True)
    # deprecate reparents children
    v.propose_topic("模具", "硬件", unit="c5")
    v.promote("模具")
    v.propose_topic("注塑", "模具", unit="c6")
    v.deprecate_topic("模具")
    check("deprecate reparent", "硬件" in v.topics["注塑"].parents, v.topics["注塑"].parents)
    # entities
    e1 = v.register_entity("蓝湾科", etype="org.supplier")
    v.alias_entity("蓝弯科", e1)
    check("entity alias", v.resolve_entity("蓝弯科") == e1)
    e2 = v.register_entity("星桥", etype="org.supplier")
    v.relate(e1, "alternative-of", e2)
    check("related", e2 in v.related(e1), v.related(e1))
    check("related reverse", e1 in v.related(e2), v.related(e2))
    # invariants ok
    check("invariants", v.check() == [], v.check())
    # xml roundtrip
    v2 = Vocab.from_xml(v.to_xml())
    check("roundtrip topics", set(v2.topics) == set(v.topics))
    check("roundtrip alias", v2.resolve_topic("motor_selection") == "电机选型")
    check("roundtrip rel", ("alternative-of", e2) in v2.entities[e1].rels)
    # consolidation candidates: same parent, affix pair
    v3 = Vocab()
    v3.define_root("营销")
    v3.propose_topic("投放", "营销", unit="x1")
    v3.propose_topic("投放策略", "营销", unit="x2")
    pairs = v3.consolidation_candidates()
    check("consolidation affix pair", ("投放", "投放策略") in pairs, pairs)


def test_vocab_downset_width_is_deterministic_and_includes_root():
    vocab = Vocab()
    vocab.define_root("root")
    for code in ("zulu", "alpha", "bravo", "charlie"):
        vocab.propose_topic(code, "root", unit=code)

    assert vocab.downset("root", max_width=3, include_candidates=True) == {
        "root",
        "alpha",
        "bravo",
    }
    assert vocab.downset("root", max_width=0, include_candidates=True) == {"root"}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("<topic/>", "Topic code must be non-empty"),
        ('<topic code="root"/><topic code="root"/>', "duplicate Topic code"),
        ('<topic code="root" status="active"/>', "invalid Topic status"),
        ('<entity code="person"/><entity code="person"/>', "duplicate Entity code"),
        ('<topic code="root"><alias/></topic>', "Topic alias text must be non-empty"),
        (
            '<topic code="root"><alias>same</alias><alias>same</alias></topic>',
            "duplicate Topic alias",
        ),
        ('<topic code="root"><use/></topic>', "Topic use unit must be non-empty"),
        (
            '<entity code="person"><rel to="other"/></entity><entity code="other"/>',
            "Entity relation type must be non-empty",
        ),
        (
            '<entity code="person"><rel type="knows"/></entity>',
            "Entity relation target of person code must be non-empty",
        ),
    ],
)
def test_vocab_from_xml_always_rejects_structurally_ambiguous_symbols(body, error):
    with pytest.raises(VocabError, match=error):
        Vocab.from_xml(ET.fromstring(f"<vocab>{body}</vocab>"))


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ('<topic code="Bad Code"/>', "Topic code must be normalized"),
        ('<entity code="Bad Entity"/>', "Entity code must be normalized"),
        (
            '<topic code="root"><alias>other</alias></topic><topic code="other"/>',
            "topic alias conflicts with topic code",
        ),
        (
            '<entity code="person"><rel type="knows" to="missing"/></entity>',
            "missing entity relation target",
        ),
        ('<topic code="orphan" status="candidate"/>', "orphan candidate orphan"),
        ('<topic code="child" parents="missing"/>', "missing parent missing of child"),
        (
            '<topic code="first" parents="second"/><topic code="second" parents="first"/>',
            "taxonomy cycle",
        ),
    ],
)
def test_vocab_from_xml_strict_mode_rejects_taxonomy_soft_inconsistencies(body, error):
    with pytest.raises(VocabError, match=error):
        Vocab.from_xml(ET.fromstring(f"<vocab>{body}</vocab>"), strict=True)


def test_vocab_from_xml_default_preserves_legacy_taxonomy_symbols():
    vocab = Vocab.from_xml(
        ET.fromstring(
            """<vocab>
            <topic code="root"/>
            <topic code="technical_help_pH" parents="(ROOT)">
              <alias>root</alias>
            </topic>
            </vocab>"""
        )
    )

    assert set(vocab.topics) == {"root", "technical_help_pH"}
    assert vocab.topics["technical_help_pH"].parents == {"(ROOT)"}
    # A concrete code wins over an alias that collides with it, so a shard
    # carrying both still resolves deterministically; strict mode is where the
    # ambiguity is reported rather than silently settled.
    assert vocab.resolve_topic("root") == "root"


def test_vocab_from_xml_normalizes_nonempty_alias_surfaces():
    vocab = Vocab.from_xml(
        ET.fromstring('<vocab><topic code="root"><alias>Human Label</alias></topic></vocab>')
    )

    assert vocab.resolve_topic("human label") == "root"


def make_store(v: Vocab):
    st = Store()
    st.units["c1"] = Unit("c1", "2026-01-23", "2026-01-23T19:19", "电机评审会", 14, 10)
    st.units["c2"] = Unit("c2", "2026-01-28", "2026-01-28T16:12", "烘焙课", 48, 8)
    st.units["c3"] = Unit("c3", "2026-02-09", "2026-02-09T14:39", "试产评审会", 60, 12)

    def add(fid, unit, date, t, kind, who, conf, topics, ents, src, text):
        st.add_fact(
            FactRecord(
                fid, unit, date, t, kind, who, conf, tuple(topics), tuple(ents), tuple(src), text
            )
        )

    add(
        "f1",
        "c1",
        "2026-01-23",
        "",
        "decision",
        "c",
        "high",
        ["静音电机"],
        ["星桥"],
        ["L101"],
        "决定与蓝湾科+星桥签开发协议，12小时保温目标不变",
    )
    add(
        "f2",
        "c1",
        "2026-01-23",
        "",
        "risk",
        "b",
        "med",
        ["电机选型"],
        ["泛达通"],
        ["L102"],
        "泛达通电机无量产记录，驱动库完整但可能有坑",
    )
    add(
        "f3",
        "c2",
        "2026-01-28",
        "",
        "event",
        "a",
        "med",
        ["烘焙"],
        [],
        ["L201"],
        "老师教授新手法，改进面团发酵控制",
    )
    add(
        "f4",
        "c3",
        "2026-02-09",
        "2026-02-16",
        "plan",
        "d",
        "high",
        ["模具"],
        ["蓝湾科"],
        ["L301"],
        "下周一（2026-02-16）前确认模具方案",
    )
    from memoket_kite.core.algebra import Line

    st.lines["L101"] = Line(
        "L101", "c1", "2026-01-23", "c", "那到底跟蓝湾科跟星桥签不签这个开发协议"
    )
    st.lines_by_unit["c1"] = ["L101"]
    return st


def test_algebra():
    v = make_vocab()
    v.propose_topic("烘焙", "营销", unit="cX")  # deliberately odd parent; still resolvable
    v.promote("烘焙")
    v.propose_topic("模具", "硬件", unit="cY")
    v.promote("模具")
    v.register_entity("蓝湾科", etype="org.supplier")
    v.alias_entity("蓝弯科", "蓝湾科")
    v.register_entity("星桥")
    v.register_entity("泛达通")
    st = make_store(v)

    def run(subs, **kw):
        rows, trace = execute(st, v, subs, **kw)
        return rows, trace

    # closure: query 硬件 catches both 电机选型(child) and 静音电机(grandchild)
    rows, _ = run([SubQuery(where=Where(topics=(("硬件", True),)))])
    check("closure match", {r["id"] for r in rows} >= {"f1", "f2", "f4"}, [r["id"] for r in rows])
    # depth-0 topic does not include children
    rows, _ = run([SubQuery(where=Where(topics=(("硬件", False),)))])
    check(
        "no-closure empty then relax",
        all(r["id"] in ("f1", "f2", "f4") for r in rows) and rows,
        [r["id"] for r in rows],
    )  # relax loop forces closure back on
    # entity alias resolution happens in parse_plan
    plan = parse_plan({"queries": [{"select": "facts", "where": {"entities": ["蓝弯科"]}}]}, v)
    rows, _ = run(plan)
    check("entity alias query", {r["id"] for r in rows} == {"f4"}, [r["id"] for r in rows])
    # time filter month prefix
    rows, _ = run([SubQuery(where=Where(time=("2026-02-01", "2026-02-28")))])
    check("time filter", {r["id"] for r in rows} == {"f4"}, [r["id"] for r in rows])
    # event time beats unit date: f4 said on 02-09 about 02-16
    rows, _ = run([SubQuery(where=Where(time=("2026-02-15", "2026-02-17")))])
    check("event-time filter", {r["id"] for r in rows} == {"f4"}, [r["id"] for r in rows])
    # kind + grep
    rows, _ = run([SubQuery(where=Where(kind=("risk",), grep="出货"))])
    check("kind+grep", [r["id"] for r in rows] == ["f2"], [r["id"] for r in rows])
    # refine: impossible grep relaxes away, kind survives
    rows, tr = run([SubQuery(where=Where(kind=("risk",), grep="不存在的词xyz"))])
    check("relax grep", [r["id"] for r in rows] == ["f2"], [r["id"] for r in rows])
    steps = [s for s in tr.stages if s["stage"] == "refine"][0]["steps"]
    check("relax order grep first", steps and steps[0] == "grep", steps)
    # empty-corpus refusal: no rows when nothing at all matches even relaxed
    rows, tr = run([SubQuery(where=Where(grep="zzzz火箭zzzz"))])
    check("refusal empty", rows == [], rows)
    # aggregates: count units in Jan
    rows, _ = run(
        [
            SubQuery(
                select="units", where=Where(time=("2026-01-01", "2026-01-31")), pipe=(("count",),)
            )
        ]
    )
    check("unit count", rows and rows[0]["type"] == "count" and rows[0]["n"] == 2, rows[:1])
    # compound: hardware convs sorted by dur desc head 1 -> c3 via facts? use units+facts
    rows, _ = run(
        [SubQuery(where=Where(topics=(("硬件", True),)), pipe=(("sort", "dur", True), ("head", 1)))]
    )
    check("pipe dur head", rows and rows[0]["unit"] == "c3", rows[:1])
    # diversity cap
    for i in range(10):
        st.add_fact(
            FactRecord(
                f"fx{i}",
                "c1",
                "2026-01-23",
                "",
                "event",
                "a",
                "med",
                ("电机选型",),
                (),
                (),
                f"filler {i}",
            )
        )
    rows, _ = run([SubQuery(where=Where(topics=(("硬件", True),)))], budget=8, unit_cap=3)
    per_unit = {}
    for r in rows:
        per_unit[r["unit"]] = per_unit.get(r["unit"], 0) + 1
    check("unit cap", max(per_unit.values()) <= 3, per_unit)
    # lines select
    rows, _ = run([SubQuery(select="lines", where=Where(grep="开发协议"))])
    check("lines grep", [r["id"] for r in rows] == ["L101"], rows)
    # store rewrite after merge
    rw = v.merge_topics(["静音电机"], "电机选型")
    st.apply_rewrite(topic_map=rw)
    check("rewrite postings", "f1" in st.by_topic["电机选型"] and "静音电机" not in st.by_topic)
    check("rewrite fact attr", "电机选型" in st.facts["f1"].topics, st.facts["f1"].topics)


def _facet_store():
    vocab = Vocab()
    vocab.define_root("pets")
    store = Store()
    for fact_id, object_code in (("dog", "dog"), ("cat", "cat")):
        store.add_fact(
            FactRecord(
                fact_id,
                "session",
                "2026-01-01",
                "",
                "event",
                "alice",
                "high",
                ("pets",),
                (),
                (),
                f"Alice mentioned a {object_code}.",
                obj=(object_code,),
            )
        )
    return store, vocab


def test_dead_value_pruning_preserves_live_facet_constraints():
    store, vocab = _facet_store()
    rows, trace = execute(
        store,
        vocab,
        [
            SubQuery(
                where=Where(
                    topics=(("pets", True),),
                    kind=("missing_kind",),
                    obj=("dog",),
                )
            )
        ],
    )

    assert [row["id"] for row in rows] == ["dog"]
    refine = next(stage for stage in trace.stages if stage["stage"] == "refine")
    assert refine["steps"] == ["prune(dead-values)"]


def test_relaxing_grep_keeps_a_live_facet_as_the_semantic_anchor():
    store, vocab = _facet_store()
    rows, trace = execute(
        store,
        vocab,
        [SubQuery(where=Where(obj=("dog",), grep="not-in-the-codebook"))],
    )

    assert [row["id"] for row in rows] == ["dog"]
    refine = next(stage for stage in trace.stages if stage["stage"] == "refine")
    assert refine["steps"] == ["grep"]


def test_dead_value_pruning_preserves_grep_all_and_entity_kind():
    vocab = Vocab()
    vocab.define_root("travel")
    vocab.register_entity("paris", etype="place.city")
    store = Store()
    store.add_fact(
        FactRecord(
            "paris",
            "u1",
            "2026-01-01",
            "",
            "event",
            "alice",
            "high",
            ("travel",),
            ("paris",),
            (),
            "Alice booked a train to Paris.",
        )
    )
    store.add_fact(
        FactRecord(
            "laptop",
            "u1",
            "2026-01-01",
            "",
            "event",
            "alice",
            "high",
            ("travel",),
            (),
            (),
            "Alice booked a laptop repair.",
        )
    )

    rows, _ = execute(
        store,
        vocab,
        [
            SubQuery(
                where=Where(
                    topics=(("travel", True),),
                    kind=("missing_kind",),
                    grep_all=("booked", "Paris"),
                    entity_kind="place",
                )
            )
        ],
    )

    assert [row["id"] for row in rows] == ["paris"]


def test_candidate_descendants_are_live_during_topic_pruning():
    vocab = Vocab()
    vocab.define_root("root")
    vocab.propose_topic("candidate_child", "root", unit="seed")
    store = Store()
    store.add_fact(
        FactRecord(
            "f1",
            "u1",
            "2026-01-01",
            "",
            "event",
            "alice",
            "high",
            ("candidate_child",),
            (),
            (),
            "candidate topic fact",
        )
    )

    rows, _ = execute(
        store,
        vocab,
        [SubQuery(where=Where(topics=(("root", True),)))],
    )

    assert [row["id"] for row in rows] == ["f1"]


def test_canonical_entity_query_can_reach_legacy_alias_postings():
    vocab = Vocab()
    vocab.register_entity("canonical")
    vocab.alias_entity("legacy", "canonical")
    store = Store()
    store.add_fact(
        FactRecord(
            "f1",
            "u1",
            "2026-01-01",
            "",
            "event",
            "alice",
            "high",
            (),
            ("legacy",),
            (),
            "legacy-coded entity",
        )
    )

    rows, _ = execute(
        store,
        vocab,
        [SubQuery(where=Where(entities=("canonical",)))],
    )

    assert [row["id"] for row in rows] == ["f1"]


def test_equal_scoring_facts_prefer_intrinsically_specific_evidence():
    vocab = Vocab()
    store = Store()
    facts = (
        ("a", "common words repeated"),
        ("z", "a rare concrete telescope detail"),
        ("m", "common words"),
    )
    for fact_id, text in facts:
        store.add_fact(
            FactRecord(
                fact_id,
                fact_id,
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
        store._index_text(text)

    rows, _ = execute(
        store,
        vocab,
        [SubQuery(where=Where(kind=("event",)), pipe=(("head", 1),))],
        budget=1,
        unit_cap=2,
    )

    assert [row["id"] for row in rows] == ["z"]


def test_equal_score_order_is_invariant_to_fact_id_assignment():
    vocab = Vocab()

    def ranked_texts(assignments):
        store = Store()
        for fact_id, text in assignments:
            store.add_fact(
                FactRecord(
                    fact_id,
                    "session",
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
            store._index_text(text)
        rows, _ = execute(
            store,
            vocab,
            [SubQuery(where=Where(kind=("event",)), pipe=(("head", 2),))],
            budget=2,
            unit_cap=2,
        )
        return [row["text"] for row in rows]

    texts = ("common repeated detail", "rare telescope observation", "common detail")
    first = ranked_texts(zip(("a", "m", "z"), texts, strict=True))
    second = ranked_texts(zip(("z", "a", "m"), texts, strict=True))

    assert set(first) == set(second)


def test_topic_closure_is_computed_once_per_ranking_pass():
    vocab = Vocab()
    vocab.define_root("root")
    vocab.propose_topic("child", "root", unit="seed")
    vocab.promote("child")
    store = Store()
    for index in range(10):
        store.add_fact(
            FactRecord(
                f"f{index}",
                f"u{index}",
                "2026-01-01",
                "",
                "event",
                "alice",
                "high",
                ("child",),
                (),
                (),
                f"fact {index}",
            )
        )

    calls = 0
    original_downset = vocab.downset

    def counted_downset(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_downset(*args, **kwargs)

    vocab.downset = counted_downset
    rows, _ = execute(
        store,
        vocab,
        [SubQuery(where=Where(topics=(("root", True),)))],
        budget=10,
        unit_cap=10,
    )

    assert len(rows) == 10
    assert calls == 3


def test_exact_topic_ranking_does_not_compute_an_unused_closure():
    vocab = Vocab()
    vocab.define_root("root")
    store = Store()
    store.add_fact(
        FactRecord(
            "f1",
            "u1",
            "2026-01-01",
            "",
            "event",
            "alice",
            "high",
            ("root",),
            (),
            (),
            "exact topic",
        )
    )

    calls = 0
    original_downset = vocab.downset

    def counted_downset(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_downset(*args, **kwargs)

    vocab.downset = counted_downset
    rows, _ = execute(
        store,
        vocab,
        [SubQuery(where=Where(topics=(("root", True),)))],
    )

    assert [row["id"] for row in rows] == ["f1"]
    # Dead-value pruning and matching each need one closure. Ranking does not.
    assert calls == 2


def test_entity_rewrite_moves_object_class_bridges():
    store = Store()
    store.add_fact(
        FactRecord(
            "f1",
            "u1",
            "2026-01-01",
            "",
            "event",
            "alice",
            "high",
            (),
            ("old",),
            (),
            "device mention",
            obj=("device",),
        )
    )

    store.apply_rewrite(entity_map={"old": "new"})

    assert "old" not in store.obj_of
    assert store.obj_of["new"] == {"device"}
    assert store.facts["f1"].entities == ("new",)


def test_store_deduplicates_identical_facts_and_rejects_conflicting_ids():
    store = Store()
    first = FactRecord(
        "f1",
        "u1",
        "2026-01-01",
        "",
        "event",
        "alice",
        "high",
        ("first",),
        (),
        (),
        "first",
    )
    replacement = FactRecord(
        "f1",
        "u1",
        "2026-01-01",
        "",
        "event",
        "alice",
        "high",
        ("replacement",),
        (),
        (),
        "replacement",
    )
    store.add_fact(first)
    assert store.add_fact(first) is False

    with pytest.raises(ValueError, match="conflicting Fact id"):
        store.add_fact(replacement)

    assert store.facts["f1"] is first
    assert store.by_topic == {"first": {"f1"}}


def _write_store_fixture(path, suffix, *, vocab="", fact_attrs="", instances=""):
    path.write_text(
        f"""<codebook>
  <vocab>{vocab}</vocab>
  <timeline>
    <session id="s{suffix}" date="2026-01-01">
      <fact id="f{suffix}"{fact_attrs}>fact</fact>
      <line id="l{suffix}">line</line>
    </session>
  </timeline>
  <instances>{instances}</instances>
</codebook>""",
        encoding="utf-8",
    )
    return str(path)


@pytest.mark.parametrize(
    ("session_id", "unit_date", "fact_id", "line_id", "error"),
    [
        ("", "2026-01-01", "f1", "l1", "Unit id must be non-empty"),
        ("s1", "2025-02-29", "f1", "l1", "invalid Unit date"),
        ("s1", "2026-01-01", "", "l1", "Fact id must be non-empty"),
        ("s1", "2026-01-01", "f1", "", "Line id must be non-empty"),
    ],
)
def test_store_rejects_missing_ids_and_invalid_unit_dates(
    tmp_path, session_id, unit_date, fact_id, line_id, error
):
    path = tmp_path / "invalid.xml"
    path.write_text(
        f"""<codebook><vocab/><timeline>
        <session id="{session_id}" date="{unit_date}">
          <fact id="{fact_id}">fact</fact><line id="{line_id}">line</line>
        </session>
        </timeline></codebook>""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=error):
        Store.load([str(path)])


def test_store_treats_identical_shard_symbols_as_idempotent(tmp_path):
    path = _write_store_fixture(
        tmp_path / "same.xml",
        "1",
        vocab="""
          <topic code="root"><alias>shared_topic</alias></topic>
          <entity code="person" type="person" name="Person">
            <alias>shared_person</alias>
          </entity>
        """,
        instances='<instance id="i1" label="Person" kind="person" mentions="f1"/>',
    )

    store, vocab = Store.load([path, path])

    assert set(store.units) == {"s1"}
    assert set(store.facts) == {"f1"}
    assert set(store.lines) == {"l1"}
    assert set(store.instances) == {"i1"}
    assert set(vocab.topics) == {"root"}
    assert set(vocab.entities) == {"person"}


def test_store_treats_an_identical_unit_in_two_shards_as_idempotent(tmp_path):
    first = _write_store_fixture(tmp_path / "first.xml", "1")
    second = _write_store_fixture(tmp_path / "second.xml", "1")

    store, _ = Store.load([first, second])

    assert set(store.units) == {"s1"}
    assert set(store.facts) == {"f1"}
    assert set(store.lines) == {"l1"}
    assert store.lines_by_unit == {"s1": ["l1"]}


def test_store_defers_lexical_postings_through_symbolic_line_execution(tmp_path):
    path = _write_store_fixture(tmp_path / "index.xml", "1")
    eager, vocab = Store.load([path, path])
    deferred, _ = Store.load([path, path], defer_text_index=True)
    queries = [SubQuery(select="lines", where=Where(grep="line"))]
    scope = {"s1"}

    eager_rows, _ = execute(eager, vocab, queries, allowed_line_units=scope)
    deferred_rows, _ = execute(deferred, vocab, queries, allowed_line_units=scope)
    assert eager_rows == deferred_rows
    assert deferred._pending_text and not deferred._by_token
    assert deferred.by_token == eager.by_token
    assert deferred.avg_doc_tokens() == eager.avg_doc_tokens()
    assert deferred.idf("line") == eager.idf("line")
    assert deferred._pending_text is None


def test_store_rejects_same_unit_header_with_different_children(tmp_path):
    first = _write_store_fixture(tmp_path / "first.xml", "1")
    second = tmp_path / "second.xml"
    second.write_text(
        """<codebook><vocab/><timeline>
        <session id="s1" date="2026-01-01">
          <fact id="f2">different fact</fact>
          <line id="l2">different line</line>
        </session>
        </timeline></codebook>""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflicting Unit id: s1"):
        Store.load([first, str(second)])


@pytest.mark.parametrize(
    ("first_vocab", "second_vocab", "first_instances", "second_instances", "error"),
    [
        (
            '<topic code="root" status="canonical"/>',
            '<topic code="root" status="deprecated"/>',
            "",
            "",
            "conflicting Topic id: root",
        ),
        (
            '<entity code="person" type="person" name="Alice"/>',
            '<entity code="person" type="person" name="Bob"/>',
            "",
            "",
            "conflicting Entity id: person",
        ),
        (
            '<topic code="first"><alias>shared</alias></topic>',
            '<topic code="second"><alias>shared</alias></topic>',
            "",
            "",
            "conflicting Topic alias: shared",
        ),
        (
            '<entity code="first"><alias>shared</alias></entity>',
            '<entity code="second"><alias>shared</alias></entity>',
            "",
            "",
            "conflicting Entity alias: shared",
        ),
        (
            "",
            "",
            '<instance id="i1" label="Alice"/>',
            '<instance id="i1" label="Bob"/>',
            "conflicting Instance id: i1",
        ),
    ],
)
def test_store_rejects_conflicting_shard_symbols(
    tmp_path, first_vocab, second_vocab, first_instances, second_instances, error
):
    first = _write_store_fixture(
        tmp_path / "first.xml",
        "1",
        vocab=first_vocab,
        instances=first_instances,
    )
    second = _write_store_fixture(
        tmp_path / "second.xml",
        "2",
        vocab=second_vocab,
        instances=second_instances,
    )

    with pytest.raises(ValueError, match=error):
        Store.load([first, second])


@pytest.mark.parametrize(
    ("fact_attrs", "error"),
    [
        (' topics="missing"', "Fact f1 references unknown Topic: missing"),
        (' entities="missing"', "Fact f1 references unknown Entity: missing"),
    ],
)
def test_store_default_accepts_legacy_postings_but_strict_mode_rejects_them(
    tmp_path, fact_attrs, error
):
    path = _write_store_fixture(
        tmp_path / "dangling.xml",
        "1",
        vocab='<topic code="root"/><entity code="person"/>',
        fact_attrs=fact_attrs,
    )

    store, _ = Store.load([path])
    assert store.facts["f1"]

    with pytest.raises(ValueError, match=error):
        Store.load([path], strict=True)


@pytest.mark.parametrize(
    ("fact_attrs", "instances", "error"),
    [
        (' src="missing"', "", "Fact f1 references unknown source Line: missing"),
        (
            "",
            '<instance id="i1" mentions="missing"/>',
            "Instance i1 references unknown Fact mention: missing",
        ),
    ],
)
def test_store_always_rejects_dangling_provenance_references(
    tmp_path, fact_attrs, instances, error
):
    path = _write_store_fixture(
        tmp_path / "dangling.xml",
        "1",
        vocab='<topic code="root"/><entity code="person"/>',
        fact_attrs=fact_attrs,
        instances=instances,
    )

    with pytest.raises(ValueError, match=error):
        Store.load([path])


def test_store_validates_references_after_all_shards_are_merged(tmp_path):
    first = _write_store_fixture(
        tmp_path / "first.xml",
        "1",
        fact_attrs=' topics="root" entities="person" src="l2"',
        instances='<instance id="i1" mentions="f2"/>',
    )
    second = _write_store_fixture(
        tmp_path / "second.xml",
        "2",
        vocab='<topic code="root"/><entity code="person"/>',
    )

    store, vocab = Store.load([first, second])

    assert set(store.facts) == {"f1", "f2"}
    assert store.instances["i1"]["mentions"] == ["f2"]
    assert set(vocab.topics) == {"root"}
    assert set(vocab.entities) == {"person"}
