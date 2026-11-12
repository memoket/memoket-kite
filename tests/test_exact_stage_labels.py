"""Local symbol parsing, using fictional documents and no model calls."""
import copy
import tempfile
import unittest
from pathlib import Path

from memoket_kite.core.algebra import (
    Store, execute_plan, _subst_refs, _has_stage_reference,
)
from memoket_kite.pipeline.compile_plan import _validated_compiled_plan


class ExactStageLabels(unittest.TestCase):
    def plan(self, name="source stage"):
        return {"stages": [
            {"name": name, "select": "units", "where": {"grep": "Cedar"}},
            {"name": "final stage", "select": "facts",
             "where": {"units": "$" + name + ".units", "grep": "born"}},
        ]}

    def test_roundtrip_preserves_exact_labels_and_queries(self):
        raw = self.plan()
        before = copy.deepcopy(raw)
        self.assertEqual(_validated_compiled_plan(raw), before)
        self.assertEqual(raw, before)

    def test_real_execution_matches_ordinary_identifier_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            bank = Path(folder) / "bank.xml"
            bank.write_text('<codebook><timeline>'
                '<session id="a" title="Cedar" date="2000-01-01">'
                '<line id="aL">Cedar was born in Pine.</line>'
                '<fact id="aF" src="aL" who="cedar" entities="cedar" kind="identity">Cedar was born in Pine.</fact></session>'
                '<session id="b" title="Willow" date="2000-01-01">'
                '<line id="bL">Willow was born in Elm.</line>'
                '<fact id="bF" src="bL" who="willow" entities="willow" kind="identity">Willow was born in Elm.</fact></session>'
                '</timeline></codebook>')
            store, vocab = Store.load([str(bank)])
            spaced = _validated_compiled_plan(self.plan())
            ordinary = self.plan("s1")
            ordinary["stages"][1]["name"] = "s2"
            ordinary = _validated_compiled_plan(ordinary)
            old, _ = execute_plan(store, vocab, ordinary, budget=40)
            new, trace = execute_plan(store, vocab, spaced, budget=40)
            self.assertEqual(new, old)
            self.assertIn("aF", [r["id"] for r in new])
            self.assertNotIn("bF", [r["id"] for r in new])
            self.assertFalse(any(t.get("skipped") for t in trace.stages))

    def test_scalar_array_and_date_references_bind_exactly(self):
        bindings = {"source stage": {"unit": "a", "units": ["a", "b"], "date": "2000-01-01"}}
        body = {"where": {"unit": "$source stage.unit", "time": ["$source stage.date", ""]}}
        self.assertEqual(_subst_refs(body, bindings, spaced_names=True),
                         ({"where": {"unit": "a", "time": ["2000-01-01", ""]}}, True))
        body = {"where": {"units": ["$source stage.units"]}}
        self.assertEqual(_subst_refs(body, bindings, spaced_names=True), ({"where": {"units": ["a", "b"]}}, True))
        plan = self.plan()
        plan["stages"][1]["where"]["time"] = ["$source stage.date", ""]
        self.assertEqual(_validated_compiled_plan(plan), plan)

    def test_literal_grep_is_never_a_spaced_reference(self):
        body = {"where": {"grep": "$source stage.units"}}
        bindings = {"source stage": {"units": ["a"]}}
        self.assertEqual(_subst_refs(body, bindings, spaced_names=True), (body, True))
        self.assertFalse(_has_stage_reference(body, spaced_names=True))
        self.assertTrue(_has_stage_reference({"where": {"units": "$source stage.units"}}, spaced_names=True))

    def test_unknown_spaced_name_does_not_guess_single_predecessor(self):
        body = {"where": {"units": "$another stage.units"}}
        self.assertEqual(_subst_refs(body, {"source stage": {"units": ["a"]}}, spaced_names=True), (None, False))
        stages = self.plan()["stages"]
        stages[1]["where"]["units"] = "$another stage.units"
        with self.assertRaises(RuntimeError):
            _validated_compiled_plan({"stages": stages})

    def test_invalid_symbols_ambiguous_edges_and_missing_projection_stay_invalid(self):
        for name in [" leading", "trailing ", "two\twords", "two\nwords", "a.b", "$a", ""]:
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                _validated_compiled_plan(self.plan(name))
        for ref in ["$source stage", "$source_stage.units", "$final stage.units", "$source stage.entity"]:
            raw = self.plan()
            raw["stages"][1]["where"]["units"] = ref
            with self.subTest(ref=ref), self.assertRaises(RuntimeError):
                _validated_compiled_plan(raw)
        duplicate = self.plan()
        duplicate["stages"][1]["name"] = "source stage"
        with self.assertRaises(RuntimeError):
            _validated_compiled_plan(duplicate)

    def test_old_literal_and_standard_reference_semantics_remain(self):
        bindings = {"s1": {"units": ["a"]}}
        for body in [{"where": {"grep": "$"}}, {"where": {"grep": "$2,500"}},
                     {"where": {"units": "$s1.units"}}, {"where": {"grep": "anchor$"}}]:
            self.assertEqual(_subst_refs(body, bindings), _subst_refs(body, bindings, spaced_names=True))
            self.assertEqual(_has_stage_reference(body), _has_stage_reference(body, spaced_names=True))

    def test_unicode_labels_are_local_symbols_not_entity_names(self):
        for name in ["来源", "узел", "source 来源", "Alpha_β", "cafe\u0301", "नाम"]:
            raw = self.plan(name)
            self.assertEqual(_validated_compiled_plan(raw), raw)
            body = {"where": {"units": "$" + name + ".units"}}
            self.assertEqual(_subst_refs(body, {name: {"units": ["a"]}}, spaced_names=True),
                             ({"where": {"units": ["a"]}}, True))
