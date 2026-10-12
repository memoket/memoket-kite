"""The token index keeps its ``by_token`` / ``_doc_tokens`` reading contract while
storing document indices instead of sets of key tuples."""

from memoket_kite.core.algebra import FactRecord, Line, Store, Unit


def _fact(fid: str, text: str) -> FactRecord:
    return FactRecord(
        id=fid,
        unit="u1",
        unit_date="2026-01-01",
        t="",
        kind="",
        who="",
        conf="",
        topics=(),
        entities=(),
        src=(),
        text=text,
    )


def _store() -> Store:
    st = Store()
    st.units["u1"] = Unit(id="u1", date="2026-01-01", t="", title="t", dur_min=0, n_lines=0)
    for f in (
        _fact("f1", "battery capacity raised to 380mAh"),
        _fact("f2", "battery test report next week"),
    ):
        st.add_fact(f)
        st._index_text(f.text, key=("F", f.id))
    line = Line(id="l1", unit="u1", unit_date="2026-01-01", who="a", text="capacity is 380mAh")
    st.lines[line.id] = line
    st._index_text(line.text, key=("L", line.id))
    return st


def test_postings_decode_to_keys_and_report_length():
    st = _store()
    batt = st.by_token.get("batter", ())
    assert len(batt) == 2 and set(batt) == {("F", "f1"), ("F", "f2")}
    cap = st.by_token["capaci"]
    assert set(cap) == {("F", "f1"), ("L", "l1")} and ("L", "l1") in cap
    assert st.by_token.get("nope", ()) == ()
    assert "batter" in st.by_token and len(st.by_token) == len(st._df)
    assert st._df["batter"] == 2


def test_document_lengths_by_key_and_by_index():
    st = _store()
    assert st._doc_tokens.get(("F", "f1")) == 4  # batter capaci raised mah ("to" is too short)
    assert st._doc_tokens.get(("L", "l1")) == 2
    assert st._doc_tokens.get(("F", "missing"), 0) == 0
    assert st.doc_key(2) == ("L", "l1") and st.doc_tokens_at(2) == 2
    assert st.avg_doc_tokens() == (4 + 5 + 2) / 3


def test_specificity_is_computed_lazily_and_tokens_are_interned():
    st = _store()
    assert st._fact_terms == {}
    assert st.fact_specificity("f1") > 0
    assert "batter" in st._fact_terms["f1"]
    # one string object per token across documents
    keys = list(st._postings)
    assert all(k is __import__("sys").intern(k) for k in keys)
