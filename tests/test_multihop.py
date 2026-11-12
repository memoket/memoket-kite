"""Independent fictional fixtures; no real benchmark QA, no paid API calls."""
import io
import fcntl
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.multihop import adapter, build, evaluate, profile, protocol
from benchmarks.multihop.score import evidence_units
from memoket_kite.core.algebra import Store, execute_plan, fact_row, line_row
from memoket_kite.core.vocab import Vocab
from memoket_kite.errors import ProviderError
from memoket_kite.pipeline import compile_plan, retrieve
from memoket_kite.pipeline import extract as native_extract
from memoket_kite.pipeline.answer import (
    AnswerOptions,
    EvidencePack,
    _build_evidence_pack,
    _generate_answer,
    _render_compact_sources,
    _render_evidence_row,
)
from memoket_kite.providers import llm
from memoket_kite.research.errors import InvalidQueryError
from memoket_kite.research.query import QueryPlan


class MultihopTests(unittest.TestCase):
    def test_feedback_compiler_stop_is_opt_in_and_not_a_native_query(self):
        self.assertEqual(compile_plan._validated_compiled_plan({"stop": True}, allow_stop=True), {"stop": True})
        for value in ({"stop": True}, {"stop": False}, {"stop": True, "queries": []}):
            with self.assertRaises(RuntimeError):
                compile_plan._validated_compiled_plan(value)
        with tempfile.TemporaryDirectory() as directory, patch.object(compile_plan, "llm_json", return_value={"stop": True}) as model:
            binding = SimpleNamespace(COMPILE_PROMPT="Question: {question}", KINDS=[])
            vocab = Vocab()
            result = compile_plan.compile_plan("A fictional question", vocab, SimpleNamespace(), binding,
                n_candidates=1, cache_dir=directory, evidence_context="Sourced fictional evidence.")
            self.assertEqual(result, {"stop": True})
            self.assertEqual(model.call_count, 1)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_feedback_round_cap_no_progress_stop_and_no_repeated_execution(self):
        def result(name, ident):
            return retrieve.RetrievalResult("fictional", {"stages": [
                {"name": "a", "select": "units", "where": {"grep": name}},
                {"name": "b", "select": "facts", "where": {"units": "$a.units"}}]},
                [{"type": "fact", "id": ident, "unit": ident, "text": name}], [], [], [], False, "factual", 4, 2)
        binding = SimpleNamespace(COMPILE_MAX_ROUNDS=3, COMPILE_CANDIDATES=1)
        a, b, c = result("Alpha", "a"), result("Beta", "b"), result("Gamma", "c")
        with patch.object(retrieve, "_run_retrieval_once", side_effect=[a, b, c]) as execute, patch.object(
                retrieve, "compile_plan", side_effect=[b.plan, c.plan]) as compiler, patch.object(
                retrieve, "_compile_feedback_context", return_value="fictional evidence"):
            actual = retrieve._run_retrieval(None, None, binding, "fictional", model="fictional")
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(compiler.call_count, 2)  # first compilation belongs to _run_retrieval_once
        self.assertEqual(actual.trace[-1]["compile_rounds"], 3)
        self.assertEqual(actual.trace[-1]["reason"], "round_limit")
        a, b = result("Alpha", "a"), result("Beta", "a")
        with patch.object(retrieve, "_run_retrieval_once", side_effect=[a, b]) as execute, patch.object(
                retrieve, "compile_plan", return_value=b.plan) as compiler, patch.object(
                retrieve, "_compile_feedback_context", return_value="fictional evidence"):
            actual = retrieve._run_retrieval(None, None, binding, "fictional", model="fictional")
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(compiler.call_count, 1)
        self.assertEqual(actual.rows, b.rows)
        self.assertEqual(actual.trace[-1]["reason"], "no_new_evidence")
        a = result("Alpha", "a")
        with patch.object(retrieve, "_run_retrieval_once", return_value=a) as execute, patch.object(
                retrieve, "compile_plan", return_value=deepcopy(a.plan)) as compiler, patch.object(
                retrieve, "_compile_feedback_context", return_value="fictional evidence"):
            actual = retrieve._run_retrieval(None, None, binding, "fictional", model="fictional")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(compiler.call_count, 1)
        self.assertEqual(actual.trace[-1]["reason"], "repeated_plan")

    def test_feedback_stop_and_error_retain_prior_pack_without_another_request(self):
        binding = SimpleNamespace(COMPILE_MAX_ROUNDS=3, COMPILE_CANDIDATES=1)
        for outcome, reason in [({"stop": True}, "compiler_stop"),
                                (ProviderError("fictional failure"), "compiler_error_previous_evidence_retained")]:
            original = retrieve.RetrievalResult("fictional", {"queries": [{"select": "facts"}]},
                [{"type": "fact", "id": "a", "unit": "u", "text": "fictional"}], [], [],
                [{"stage": "refine", "steps": ["grep"]}], False, "factual", 4, 2)
            options = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with patch.object(retrieve, "_run_retrieval_once", return_value=original) as execute, patch.object(
                    retrieve, "compile_plan", **options) as compiler, patch.object(
                    retrieve, "_compile_feedback_context", return_value="fictional evidence"):
                actual = retrieve._run_retrieval(None, None, binding, "fictional", model="fictional")
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(compiler.call_count, 1)
            self.assertEqual(actual.trace[-1]["reason"], reason)
            self.assertEqual(actual.rows, original.rows)
        for kwargs in [dict(COMPILE_MAX_ROUNDS=4, COMPILE_CANDIDATES=1),
                       dict(COMPILE_MAX_ROUNDS=3, COMPILE_CANDIDATES=3),
                       dict(COMPILE_MAX_ROUNDS=3, COMPILE_CANDIDATES=1, PLAN_REPAIR=True)]:
            with patch.object(retrieve, "_run_retrieval_once") as execute, self.assertRaises(ValueError):
                retrieve._run_retrieval(None, None, SimpleNamespace(**kwargs), "fictional", model="fictional")
            execute.assert_not_called()

    def test_feedback_retains_earlier_candidates_after_revised_pool_when_enabled(self):
        first_hit = {"type": "fact", "id": "bridge", "unit": "bridge"}
        terminal = {"type": "fact", "id": "terminal", "unit": "terminal"}
        shared = {"type": "line", "id": "shared", "unit": "shared"}
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                first = retrieve.RetrievalResult(
                    "Where?", {"queries": [{"select": "facts"}]}, [first_hit], [], [], [],
                    False, "factual", 2, 2, [first_hit, shared],
                )
                revised = retrieve.RetrievalResult(
                    "Where?", {"queries": [{"select": "units"}]}, [terminal], [], [], [],
                    False, "factual", 2, 2, [terminal, shared],
                )
                binding = SimpleNamespace(COMPILE_MAX_ROUNDS=2, COMPILE_CANDIDATES=1,
                                          COMPILE_REVIEW_SUBJECT_SCOPE=True,
                                          COMPILE_RETAIN_PREVIOUS_CANDIDATES=enabled)
                with patch.object(retrieve, "_run_retrieval_once", side_effect=[first, revised]), patch.object(
                        retrieve, "compile_plan", return_value=revised.plan), patch.object(
                        retrieve, "_compile_feedback_context", return_value="Bridge evidence."):
                    actual = retrieve._run_retrieval(None, None, binding, "Where?", model="fictional")
                self.assertEqual(actual.rows, [terminal])
                self.assertEqual(actual.candidate_rows, [terminal, shared, first_hit] if enabled else [terminal, shared])

    def test_subject_scope_review_opens_one_feedback_round_after_a_nonempty_plan(self):
        def result(entity, row_type="fact"):
            return retrieve.RetrievalResult(
                "Where was Cedar's designer born?",
                {"queries": [{"select": "facts", "where": {"entities": [entity]}}]},
                [{"type": row_type, "id": entity}], [], [], [], False, "factual", 4, 2,
            )

        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                first, revised = result("cedar"), result("iris", "unit")
                binding = SimpleNamespace(COMPILE_MAX_ROUNDS=2, COMPILE_CANDIDATES=1,
                                          COMPILE_REVIEW_SUBJECT_SCOPE=enabled)
                with patch.object(retrieve, "_run_retrieval_once", side_effect=[first, revised]), patch.object(
                        retrieve, "compile_plan", return_value=revised.plan) as compiler, patch.object(
                        retrieve, "_compile_feedback_context", return_value="Cedar's designer is Iris."):
                    actual = retrieve._run_retrieval(None, None, binding, first.question, model="fictional")
                self.assertEqual(compiler.call_count, int(enabled))
                self.assertEqual(actual.plan, revised.plan if enabled else first.plan)
                if enabled:
                    self.assertEqual(next(e["trigger"] for e in actual.trace if e.get("round") == 2),
                                     "review_subject_scope")

    def test_native_feedback_grounds_second_hop_and_calls_reader_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocabulary = ET.SubElement(root, "vocab")
            ET.SubElement(vocabulary, "topic", code="knowledge", status="canonical")
            for code in ("cedar", "iris"):
                ET.SubElement(vocabulary, "entity", code=code, type="person", name=code)
            timeline = ET.SubElement(root, "timeline")
            for ident, title, text, entities in [("u1", "Cedar", "Cedar's designer is Iris.", "cedar iris"),
                                               ("u2", "Iris", "Iris was born in Bay.", "iris")]:
                unit = ET.SubElement(timeline, "session", id=ident, date="2000-01-01", title=title)
                ET.SubElement(unit, "line", id=ident+"L1", who="source").text = text
                ET.SubElement(unit, "fact", id=ident+"F1", topics="knowledge", entities=entities,
                              kind="relationship", src=ident+"L1").text = text
            path = Path(directory)/"book.xml"; ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            first = {"stages": [{"name": "s1", "select": "units", "where": {"grep": "Cedar"}},
                                {"name": "s2", "select": "facts", "where": {"units": "$s1.units", "grep": "designer"}}]}
            second = {"queries": [
                {"select": "facts", "where": {"entities": ["cedar"], "grep": "designer"}},
                {"select": "facts", "where": {"entities": ["iris"], "grep": "born"}}]}
            binding = SimpleNamespace(**{k: getattr(profile, k) for k in dir(profile) if not k.startswith("__")})
            binding.COMPILE_MAX_ROUNDS = 3; binding.DEFAULT_BUDGET = 2
            from memoket_kite.pipeline.answer import answer as native_answer
            with patch.object(retrieve, "compile_plan", side_effect=[first, second]) as compiler, patch.object(
                    retrieve, "_retrieve_lexically", return_value=[]), patch(
                    "memoket_kite.pipeline.answer.llm_json", return_value={"evidence": ["u1F1", "u2F1"], "answer": "Bay"}) as reader:
                actual = native_answer(store, vocab, binding, "Where was Cedar's designer born?",
                                       model="gpt-6-luna", answer_model="gpt-4o-mini", budget=2)
            self.assertEqual(compiler.call_count, 2)
            feedback = compiler.call_args.kwargs["evidence_context"]
            self.assertIn("Cedar's designer is Iris.", feedback)
            self.assertNotIn("Iris was born in Bay.", feedback)
            self.assertEqual(reader.call_count, 1)
            self.assertEqual(actual["answer"], "Bay")
            self.assertEqual({r["id"] for r in actual["pack_rows"]}, {"u1F1", "u2F1"})
            self.assertEqual(actual["trace"][-1]["compile_rounds"], 2)

    def test_native_topic_resolution_keeps_facts_without_inventing_missing_nodes(self):
        vocab = Vocab()
        vocab.define_root("science")
        binding = SimpleNamespace(EXTRACT_PROMPT="{dialog}", KINDS=["identity"],
                                  EXTRACT_SINGLE_SOURCE=True, EXTRACT_STRICT_TOPICS=False)
        unit = {"id": "u", "date": "2000-01-01", "utterances": [
            ("uL1", "source", "", "Iris studies plants. Iris teaches botany.")]}
        raw = {"facts": [
            {"content": "Iris studies plants.", "kind": "identity", "topics": ["unregistered_field"],
             "entities": ["Iris"]},
            {"content": "Iris teaches botany.", "kind": "identity", "topics": ["science"],
             "entities": ["Iris"]}], "proposals": [], "entity_types": {"Iris": "person"}}
        with patch.object(native_extract, "llm_json", return_value=raw):
            facts = native_extract.extract_facts(vocab, binding, unit, log=lambda _: None)
        self.assertEqual([f["content"] for f in facts], [f["content"] for f in raw["facts"]])
        self.assertEqual([f["topics"] for f in facts], [[], ["science"]])
        self.assertTrue(all(f["src"] == ["uL1"] and f["entities"] == ["iris"] for f in facts))
        self.assertIsNone(vocab.resolve_topic("unregistered_field"))
        self.assertEqual(raw["facts"][0]["topics"], ["unregistered_field"])

    def test_native_topic_missing_count_is_explicit_in_builder_manifest(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            root = Path(directory)
            data = root / "data"
            data.mkdir()
            protocol.append(data / "corpus.jsonl", adapter.document("Cedar", "Cedar is a town."))
            protocol.dump(data / "manifest.json", {"scope": "fictional", "sha256": {
                "corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
            raw = {"facts": [{"content": "Cedar is a town.", "kind": "identity",
                              "topics": ["unregistered_field"], "entities": ["Cedar"]}],
                   "proposals": [], "entity_types": {"Cedar": "place"}}
            with patch.object(protocol, "paths", return_value=(data, root / "bank.xml", root / "result", root / "cache")), patch.object(
                    native_extract, "llm_json", return_value=raw), patch.object(profile, "EXTRACT_STRICT_TOPICS", False):
                result = build.build("musique", "fictional")
            manifest = protocol.read((root / "bank.xml").with_suffix(".manifest.json"))
            self.assertTrue(result["extraction_complete"])
            self.assertEqual(manifest["facts"], 1)
            self.assertEqual(manifest["empty_topic_facts"], 1)
            self.assertEqual(manifest["topic_policy"], "original_native_resolution")
            self.assertIn("Cedar is a town.", (root / "bank.xml").read_text())

    def test_prefix_copy_refuses_a_running_origin_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with (cache / "campaign.lock").open("a+") as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(protocol, "paths", return_value=(cache, cache, cache, cache)):
                    with self.assertRaises(BlockingIOError):
                        build.seed_prefix("musique", "new", "old")

    def test_format_recovery_only_accepts_one_received_unparseable_response(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            previous = {"failed_extractions": [{"id": "u", "error_type": "ProviderError"}]}
            request = {"event": "request", "call_id": "r", "item": "u", "request": {
                "model": "gpt-6-luna", "reasoning_effort": "none", "temperature": 0,
                "messages": [{"role": "user", "content": "Fictional source. Return JSON."}]}}
            invalid = '{"facts":[{"content":"The term "bright" appears."}]}'
            def write(content, finish="stop", extra=()):
                response = {"event": "response", "call_id": "r", "item": "u", "response": {
                    "choices": [{"finish_reason": finish, "message": {"content": content}}]}}
                (cache / "extract.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [request, response, *extra]))
            write(invalid)
            self.assertEqual(build._format_recovery(previous, cache)["response_format"], {"type": "json_object"})
            for content, finish in [('{}', 'stop'), ('{"facts":"bad schema"}', 'stop'),
                                    ('{}', 'length'), (invalid, 'content_filter'), ('No information', 'stop'),
                                    ('{"facts":[]} trailing prose', 'stop')]:
                write(content, finish)
                self.assertIsNone(build._format_recovery(previous, cache))
            write(invalid, extra=[{**request, "call_id": "second"}])
            self.assertIsNone(build._format_recovery(previous, cache))
            write(invalid)
            protocol.dump(cache / "format-retries/u.json", {})
            self.assertIsNone(build._format_recovery(previous, cache))

    def test_truncated_json_format_recovery_keeps_original_prompt_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            prompt = 'Fictional source: Rowan married Elm. Return JSON.'
            request = {"model": "gpt-6-luna", "reasoning_effort": "none", "temperature": 0,
                       "max_completion_tokens": 16000,
                       "messages": [{"role": "user", "content": prompt}]}
            response = {"choices": [{"finish_reason": "length", "message": {
                "content": '{"entity_types":{"Rowan":"person","Rowan":"person",'}}]}
            protocol.append(cache / "extract.jsonl", {"event": "request", "call_id": "r", "item": "u", "request": request})
            protocol.append(cache / "extract.jsonl", {"event": "response", "call_id": "r", "item": "u", "response": response})
            previous = {"failed_extractions": [{"id": "u", "error_type": "ProviderError"}]}
            marker = build._format_recovery(previous, cache)
            self.assertEqual(marker["failed_finish_reason"], "length")
            self.assertEqual(marker["max_additional_format_attempts"], 1)
            import hashlib
            self.assertEqual(marker["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
            self.assertNotIn("max_completion_tokens", marker)
            self.assertTrue(marker["failed_response_preserved_not_model_input"])
            protocol.dump(cache / "format-retries/u.json", marker)
            self.assertIsNone(build._format_recovery(previous, cache))

    def test_broken_envelope_with_cached_inner_fact_gets_only_format_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            previous = {"failed_extractions": [{"id": "u", "error_type": "RuntimeError"}]}
            request = {"model": "gpt-6-luna", "reasoning_effort": "none", "temperature": 0,
                       "messages": [{"role": "user", "content": "Fictional source. Return JSON."}]}
            # A stray brace breaks the envelope, leaving a parseable inner fact.
            invalid = '{"entity_types":{},"proposals":[],"facts":[{"content":"Iris writes."}} ,{"content":"Elm reads."}]}'
            protocol.append(cache / "extract.jsonl", {"event": "request", "call_id": "r", "item": "u", "request": request})
            protocol.append(cache / "extract.jsonl", {"event": "response", "call_id": "r", "item": "u", "response": {
                "choices": [{"finish_reason": "stop", "message": {"content": invalid}}]}})
            raw = cache / "raw/u.fingerprint.json"
            protocol.dump(raw, {"content": "Elm reads."})
            marker = build._format_recovery(previous, cache)
            self.assertEqual(marker["max_additional_format_attempts"], 1)
            protocol.dump(raw, {"facts": "invalid schema"})
            self.assertIsNone(build._format_recovery(previous, cache))
            protocol.dump(raw, {"facts": [], "proposals": [], "entity_types": {}})
            self.assertIsNone(build._format_recovery(previous, cache))

    def test_response_format_has_distinct_cache_and_checks_original_prompt(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "fictional"}):
            unit = next(adapter.iter_units([adapter.document("Cedar", "Cedar is a city.")]))
            def vocabulary():
                vocab = Vocab()
                for root in profile.SEED_ROOTS:
                    vocab.define_root(root)
                return vocab
            response = {"facts": [], "proposals": [], "entity_types": {}}
            with patch.object(native_extract, "llm_json", return_value=response) as model:
                native_extract.extract_facts(vocabulary(), profile, unit, model="gpt-6-luna", cache_dir=directory)
                prompt = model.call_args.args[0]
                with self.assertRaisesRegex(RuntimeError, "cache-only"):
                    native_extract.extract_facts(vocabulary(), profile, unit, model="gpt-6-luna", cache_dir=directory,
                                                 cache_only=True, response_format={"type": "json_object"})
                with self.assertRaisesRegex(RuntimeError, "exact original prompt"):
                    native_extract.extract_facts(vocabulary(), profile, unit, model="gpt-6-luna", cache_dir=directory,
                                                 response_format={"type": "json_object"}, expected_prompt_sha256="wrong")
                self.assertEqual(model.call_count, 1)
                native_extract.extract_facts(vocabulary(), profile, unit, model="gpt-6-luna", cache_dir=directory,
                                             response_format={"type": "json_object"},
                                             expected_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
                self.assertEqual(model.call_count, 2)
                native_extract.extract_facts(vocabulary(), profile, unit, model="gpt-6-luna", cache_dir=directory,
                                             cache_only=True, response_format={"type": "json_object"})
                self.assertEqual(model.call_count, 2)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)

    def test_incomplete_prefix_and_bounded_format_resume_preserve_successes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "OPENAI_API_KEY": "fictional", "KITE_LLM_RETRIES": "0"}):
            root = Path(directory)
            documents = [adapter.document("Cedar", "Cedar is a city."),
                         adapter.document("Bay", 'Bay uses the term "bright".')]
            def paths(tag, dataset, run=None):
                return (root / tag / "data", root / tag / "book.xml", root / tag / "result", root / tag / "cache")
            for tag in ["old", "new"]:
                data, _, _, _ = paths(tag, "musique")
                data.mkdir(parents=True)
                for doc in documents:
                    protocol.append(data / "corpus.jsonl", doc)
                protocol.dump(data / "manifest.json", {"scope": "fictional", "sha256": {
                    "corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
            seen = []
            def transport(prompt, timeout, temperature, model, **options):
                item = os.environ["KITE_LLM_ITEM"]
                seen.append((item, prompt, options))
                call_id = str(len(seen))
                request = {"model": model, "reasoning_effort": options["reasoning_effort"],
                           "temperature": temperature, "messages": [{"role": "user", "content": prompt}]}
                if "response_format" in options:
                    request["response_format"] = options["response_format"]
                llm._record_call("request", call_id, request=request)
                content = json.dumps({"facts": [{"content": 'A source says "bright".', "kind": "identity",
                                      "topics": ["people"], "entities": []}], "proposals": [], "entity_types": {}})
                if len(seen) == 2:
                    content = '{"facts":[{"content":"Bay uses "bright"."}]}'
                llm._record_call("response", call_id, response={"model": model, "usage": {"total_tokens": 10},
                    "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
                return content
            with patch.object(protocol, "paths", side_effect=paths), patch.object(llm, "_http_llm", side_effect=transport):
                self.assertFalse(build.build("musique", "old")["extraction_complete"])
                # Copy only successful prefix as reusable, preserve failed receipt.
                first = build.build("musique", "new", reuse_from="old", recover_format=True)
                self.assertTrue(first["extraction_complete"])
                self.assertEqual(len(seen), 3)  # Eligible terminal failure resumes on first seed.
                resumed = build.build("musique", "new", resume=True, reuse_from="old", recover_format=True)
                self.assertTrue(resumed["extraction_complete"])
                build.build("musique", "new", resume=True, reuse_from="old", recover_format=True)
            self.assertEqual(len(seen), 3)
            self.assertEqual(seen[1][:2], seen[2][:2])
            self.assertEqual(seen[2][2]["response_format"], {"type": "json_object"})
            memory = root / "new/book.xml"
            self.assertEqual([f.text for f in ET.parse(memory).findall("timeline/session/fact")],
                             ['A source says "bright".', 'A source says "bright".'])
            manifest = protocol.read(memory.with_suffix(".manifest.json"))
            self.assertEqual(len(manifest["format_recoveries"]), 1)
            self.assertEqual(manifest["new_HTTP_calls"], 1)
            self.assertFalse(manifest["reuse"]["origin_extraction_complete"])
            self.assertEqual(len(manifest["reuse"]["prefix_ids"]), 1)

    def test_unit_lookup_keeps_title_relevance_when_budget_trims_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            ET.SubElement(root, "vocab")
            timeline = ET.SubElement(root, "timeline")
            for uid, title, text in [("a", "Other", "Cedar is mentioned here."),
                                     ("z", "Cedar", "The main record describes a town.")]:
                unit = ET.SubElement(timeline, "session", id=uid, date="2000-01-01", title=title)
                ET.SubElement(unit, "line", id=uid + "L1", who="source").text = text
                ET.SubElement(unit, "fact", id=uid + "F1", src=uid + "L1").text = text
            path = Path(directory) / "bank.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            rows, _ = execute_plan(store, vocab, {"queries": [
                {"select": "units", "where": {"grep": "Cedar"}, "pipe": []}
            ]}, budget=1, pack_v2=True)
            self.assertEqual([r["id"] for r in rows], ["z"])

    def test_scalar_unit_binding_uses_rank_and_honors_explicit_date_sort(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            ET.SubElement(root, "vocab")
            timeline = ET.SubElement(root, "timeline")
            for uid, title, day, text in [
                ("a", "Other", "1999-01-01", "Cedar is mentioned here."),
                ("z", "Cedar", "2005-01-01", "The main record describes a town."),
            ]:
                unit = ET.SubElement(timeline, "session", id=uid, date=day, title=title)
                ET.SubElement(unit, "line", id=uid + "L1", who="source").text = text
                ET.SubElement(unit, "fact", id=uid + "F1", src=uid + "L1").text = text
            path = Path(directory) / "bank.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            for pipe, expected in [([], "zF1"),
                                   ([{"op": "sort", "key": "t", "desc": False}], "aF1")]:
                with self.subTest(pipe=pipe):
                    plan = {"stages": [
                        {"name": "anchor", "select": "units", "where": {"grep": "Cedar"}, "pipe": pipe},
                        {"name": "property", "select": "facts", "where": {"units": ["$anchor.unit"]}, "pipe": []},
                    ]}
                    rows, _ = execute_plan(store, vocab, plan, budget=1, pack_v2=True)
                    self.assertEqual([r["id"] for r in rows], [expected])

    def test_explicit_transport_resume_reuses_successes_and_never_retries_twice(self):
        for recovery_succeeds in [True, False]:
            with self.subTest(recovery_succeeds=recovery_succeeds), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
                base = Path(directory)
                data = base / "data"
                data.mkdir()
                docs = [adapter.document("Cedar", "Cedar is a town."),
                        adapter.document("Bay", "Bay is a port.")]
                for doc in docs:
                    protocol.append(data / "corpus.jsonl", doc)
                protocol.dump(data / "manifest.json", {"scope": "fictional",
                              "sha256": {"corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
                memory, cache = base / "book.xml", base / "cache"
                calls = []

                def model(prompt, model, **kwargs):
                    self.assertEqual(kwargs.get("reasoning_effort"), "none")
                    item = os.environ["KITE_LLM_ITEM"]
                    calls.append(item)
                    call_id = f"request{len(calls)}"
                    llm._record_call("request", call_id, request={"model": model})
                    if len(calls) == 2 or (len(calls) == 3 and not recovery_succeeds):
                        llm._record_call("error", call_id, error_type="URLError")
                        raise ProviderError("Connection reset by peer")
                    llm._record_call("response", call_id, response={"model": model, "usage": {"total_tokens": 10}})
                    return {"facts": [{"content": "A source claim.", "kind": "identity",
                                       "topics": ["people"], "entities": []}],
                            "proposals": [], "entity_types": {}}

                with patch.object(protocol, "paths", return_value=(data, memory, base / "result", cache)), patch.object(
                        native_extract, "llm_json", side_effect=model):
                    first = build.build("musique", "fictional")
                    self.assertFalse(first["extraction_complete"])
                    resumed = build.build("musique", "fictional", resume=True)
                    self.assertEqual(resumed["extraction_complete"], recovery_succeeds)
                    build.build("musique", "fictional", resume=True)
                self.assertEqual(calls, [docs[0]["id"], docs[1]["id"], docs[1]["id"]])
                usage = protocol.usage(cache / "extract.jsonl")
                self.assertEqual(usage["HTTP_calls"], 3)
                self.assertEqual(usage["unknown_usage"], 1 if recovery_succeeds else 2)
                self.assertTrue(protocol.read(cache / "transport-retry.json")["lost_response_usage_unknown"])

    def test_seeded_http520_resume_preserves_prompt_successes_and_retry_bound(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
                base = Path(directory)
                def paths(tag, dataset):
                    return base / tag / "data", base / tag / "book.xml", base / tag / "result", base / tag / "cache"
                docs = [adapter.document("Cedar", "Cedar is a town."), adapter.document("Bay", "Bay is a port.")]
                for tag in ("old", "new"):
                    data = paths(tag, "musique")[0]
                    data.mkdir(parents=True)
                    for doc in docs:
                        protocol.append(data / "corpus.jsonl", doc)
                    protocol.dump(data / "manifest.json", {"scope": "fictional", "sha256": {
                        "corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
                calls = []
                def model(prompt, model, **kwargs):
                    item = os.environ["KITE_LLM_ITEM"]
                    calls.append((item, prompt))
                    call_id = str(len(calls))
                    llm._record_call("request", call_id, request={"model": model, "messages": [
                        {"role": "user", "content": prompt}]})
                    if len(calls) == 2 or (len(calls) == 3 and not succeeds):
                        llm._record_call("error", call_id, error_type="HTTPError")
                        raise ProviderError("llm failed: HTTP Error 520: unavailable")
                    llm._record_call("response", call_id, response={"model": model, "usage": {"total_tokens": 10}})
                    return {"facts": [], "proposals": [], "entity_types": {}}
                with patch.object(protocol, "paths", side_effect=paths), patch.object(native_extract, "llm_json", side_effect=model):
                    self.assertFalse(build.build("musique", "old")["extraction_complete"])
                    self.assertEqual(build.build("musique", "new", reuse_from="old")["extraction_complete"], succeeds)
                    build.build("musique", "new", reuse_from="old", resume=True)
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[1], calls[2])
                cache = paths("new", "musique")[3]
                marker = protocol.read(cache / "transport-retries" / (docs[1]["id"] + ".json"))
                self.assertEqual(marker["terminal_http_status"], 520)
                self.assertEqual(protocol.usage(cache / "extract.jsonl")["unknown_usage"], 1 if succeeds else 2)

    def test_transport_recovery_rejects_responses_inflight_and_nontransport_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            previous = {"failed_extractions": [{"id": "u", "error_type": "ProviderError"}]}
            request = {"event": "request", "call_id": "r", "item": "u"}
            variants = [[], [{"event": "error", "call_id": "r", "item": "u", "error_type": "HTTPError"}],
                        [{"event": "response", "call_id": "r", "item": "u"}]]
            for extra in variants:
                path = cache / "extract.jsonl"
                path.write_text("".join(json.dumps(r) + "\n" for r in [request, *extra]))
                self.assertIsNone(build._transport_recovery(previous, cache))

    def test_transport_recovery_is_bounded_per_source_not_per_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            previous = {"failed_extractions": [{"id": "second", "error_type": "ProviderError"}]}
            protocol.dump(cache / "transport-retry.json", {"source_id": "first"})
            protocol.dump(cache / "transport-retries/first.json", {"source_id": "first"})
            protocol.append(cache / "extract.jsonl", {"event": "request", "call_id": "lost",
                                                     "item": "second"})
            protocol.append(cache / "extract.jsonl", {"event": "error", "call_id": "lost",
                                                     "item": "second", "error_type": "TimeoutError"})
            recovery = build._transport_recovery(previous, cache)
            self.assertEqual(recovery["source_id"], "second")
            protocol.dump(cache / "transport-retries/second.json", recovery)
            self.assertIsNone(build._transport_recovery(previous, cache))

    def test_seeded_terminated_worker_replays_successes_and_requests_only_lost_source(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            base = Path(directory)
            def paths(tag, dataset):
                return base / tag / "data", base / tag / "book.xml", base / tag / "result", base / tag / "cache"
            docs = [adapter.document("Cedar", "Cedar is a town."), adapter.document("Bay", "Bay is a port.")]
            for tag in ("old", "new"):
                data = paths(tag, "musique")[0]
                data.mkdir(parents=True)
                for doc in docs:
                    protocol.append(data / "corpus.jsonl", doc)
                protocol.dump(data / "manifest.json", {"scope": "fictional", "sha256": {
                    "corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
            calls = []
            def model(prompt, model, **kwargs):
                calls.append((os.environ["KITE_LLM_ITEM"], prompt))
                call_id = str(len(calls))
                llm._record_call("request", call_id, request={"model": model,
                    "messages": [{"role": "user", "content": prompt}]})
                if len(calls) == 2:
                    raise RuntimeError("exact extraction cache missing or invalid; cache-only mode forbids API calls")
                llm._record_call("response", call_id, response={"model": model, "usage": {"total_tokens": 10}})
                return {"facts": [], "proposals": [], "entity_types": {}}
            with patch.object(protocol, "paths", side_effect=paths), patch.object(native_extract, "llm_json", side_effect=model):
                self.assertFalse(build.build("musique", "old")["extraction_complete"])
                _, memory, _, cache = paths("old", "musique")
                manifest = protocol.read(memory.with_suffix(".manifest.json"))
                protocol.dump(cache / "interrupted-workers" / (docs[1]["id"] + ".json"), {
                    "origin_pid": 12345, "process_absent": True, "new_API_calls": 0,
                    "exact_native_cache_replay": True, "source_id": docs[1]["id"], "lost_call_id": "2",
                    "receipt_sha256": protocol.sha(cache / "extract.jsonl"),
                    "manifest_memory_sha256": manifest["memory_sha256"], "extraction_source_sha256": manifest["source"],
                    "code": manifest["code"], "accepted_prefix": 1})
                with patch.object(build.os, "kill", side_effect=ProcessLookupError):
                    self.assertTrue(build.build("musique", "new", reuse_from="old")["extraction_complete"])
                    build.build("musique", "new", reuse_from="old", resume=True)
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[1], calls[2])
            new_cache = paths("new", "musique")[3]
            self.assertEqual(protocol.usage(new_cache / "extract.jsonl")["unknown_usage"], 1)
            self.assertTrue((new_cache / "interrupted-workers" / (docs[1]["id"] + ".json")).exists())

    def test_interrupted_worker_requires_exact_checkpoint_and_current_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            request = {"event": "request", "call_id": "lost", "item": "u", "request": {
                "model": "gpt-6-luna", "messages": [{"role": "user", "content": "Fictional source."}]}}
            protocol.append(cache / "extract.jsonl", request)
            previous = {"failed_extractions": [{"id": "u", "error_type": "RuntimeError", "error":
                "exact extraction cache missing or invalid; cache-only mode forbids API calls"}],
                "memory_sha256": "memory", "source": "corpus", "documents": 2, "code": {"kernel": "same"}}
            self.assertIsNone(build._transport_recovery(previous, cache))
            proof = {"origin_pid": 12345, "process_absent": True, "new_API_calls": 0,
                     "exact_native_cache_replay": True, "source_id": "u", "lost_call_id": "lost",
                     "receipt_sha256": protocol.sha(cache / "extract.jsonl"),
                     "manifest_memory_sha256": "memory", "extraction_source_sha256": "corpus",
                     "code": previous["code"], "accepted_prefix": 1}
            path = cache / "interrupted-workers/u.json"
            protocol.dump(path, proof)
            with patch.object(build.os, "kill", return_value=None):
                self.assertIsNone(build._transport_recovery(previous, cache))
            with patch.object(build.os, "kill", side_effect=ProcessLookupError):
                marker = build._transport_recovery(previous, cache)
                self.assertEqual(marker["recovery_reason"], "verified_terminated_worker")
                for key, value in [("origin_pid", 0), ("lost_call_id", "wrong"),
                                   ("accepted_prefix", 2), ("manifest_memory_sha256", "changed"),
                                   ("receipt_sha256", "changed"), ("new_API_calls", 1)]:
                    with self.subTest(key=key):
                        protocol.dump(path, dict(proof, **{key: value}))
                        self.assertIsNone(build._transport_recovery(previous, cache))
                protocol.dump(path, proof)
                protocol.dump(cache / "transport-retries/u.json", marker)
                self.assertIsNone(build._transport_recovery(previous, cache))

    def test_terminal_http_5xx_recovery_requires_matched_receipt_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            request = {"event": "request", "call_id": "lost", "item": "u",
                       "request": {"model": "gpt-6-luna", "messages": [
                           {"role": "user", "content": "Fictional source only."}]}}
            error = {"event": "error", "call_id": "lost", "item": "u", "error_type": "HTTPError"}
            for status in [400, 401, 404, 429, 500, 502, 520, 599]:
                previous = {"failed_extractions": [{"id": "u", "error_type": "ProviderError",
                            "error": f"llm failed: HTTP Error {status}: unavailable"}]}
                (cache / "extract.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [request, error]))
                recovery = build._transport_recovery(previous, cache)
                self.assertEqual(recovery is not None, status >= 500)
                if recovery:
                    self.assertEqual(recovery["terminal_http_status"], status)
                    self.assertTrue(recovery["prompt_sha256"])
            previous["failed_extractions"][0]["error"] = "unknown HTTP status"
            self.assertIsNone(build._transport_recovery(previous, cache))
            previous["failed_extractions"][0]["error"] = "llm failed: HTTP Error 520: unavailable"
            (cache / "extract.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
                request, {**error, "call_id": "unrelated"}]))
            self.assertIsNone(build._transport_recovery(previous, cache))

    def test_native_checkpoint_resume_preserves_prompt_promotion_and_XML_without_full_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            base = Path(directory)
            docs = [adapter.document(name, name + " was designed by Iris Labs.")
                    for name in ("Cedar", "Bay", "Rowan")]
            def paths(tag, dataset):
                return base / tag / "data", base / tag / "book.xml", base / tag / "result", base / tag / "cache"
            for tag in ("reference", "resume"):
                data = paths(tag, "musique")[0]
                data.mkdir(parents=True)
                for doc in docs:
                    protocol.append(data / "corpus.jsonl", doc)
                protocol.dump(data / "manifest.json", {"scope": "fictional", "sha256": {
                    "corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
            calls = []
            def response(prompt, model, **kwargs):
                item = os.environ["KITE_LLM_ITEM"]
                calls.append((item, prompt))
                doc = next(d for d in docs if d["id"] == item)
                return {"facts": [{"content": doc["text"], "kind": "relationship",
                    "topics": ["arts_design"], "entities": [doc["title"], "Iris Labs"]}],
                    "proposals": [{"code": "arts_design", "parent": "arts"}],
                    "entity_types": {doc["title"]: "work", "Iris Labs": "org"}}
            with patch.object(protocol, "paths", side_effect=paths), patch.object(native_extract, "llm_json", side_effect=response):
                build.build("musique", "reference")
                reference_prompt = calls[-1][1]
                events = []
                build.build("musique", "resume", progress=events.append,
                            stop_requested=lambda: len(events) >= 2)
                self.assertEqual(len(calls), 5)
                with patch.object(build, "extract_facts", wraps=build.extract_facts) as extract_call:
                    result = build.build("musique", "resume", resume=True)
                self.assertTrue(result["extraction_complete"])
                self.assertEqual([c.args[2]["id"] for c in extract_call.call_args_list],
                                 [docs[0]["id"], docs[2]["id"]])
            self.assertEqual(len(calls), 6)
            self.assertEqual(calls[-1][1], reference_prompt)
            self.assertEqual(paths("resume", "musique")[1].read_bytes(), paths("reference", "musique")[1].read_bytes())
            manifest = protocol.read(paths("resume", "musique")[1].with_suffix(".manifest.json"))
            self.assertEqual(manifest["native_checkpoint_resume"]["sources"], 2)

    def test_progress_callback_reports_cached_resume_without_extra_model_calls(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            base = Path(directory)
            data = base / "data"
            data.mkdir()
            docs = [adapter.document("Cedar", "Cedar is a town."),
                    adapter.document("Bay", "Bay is a port.")]
            for doc in docs:
                protocol.append(data / "corpus.jsonl", doc)
            protocol.dump(data / "manifest.json", {"scope": "fictional",
                          "sha256": {"corpus.jsonl": protocol.sha(data / "corpus.jsonl")}})
            events = []
            memory = base / "book.xml"
            def response(prompt, model, **kwargs):
                call_id = os.environ["KITE_LLM_ITEM"]
                llm._record_call("request", call_id, request={"model": model})
                llm._record_call("response", call_id, response={"model": model, "usage": {}})
                return {"facts": [], "proposals": [], "entity_types": {}}
            with patch.object(protocol, "paths", return_value=(data, memory, base / "result", base / "cache")), patch.object(
                    native_extract, "llm_json", side_effect=response) as model:
                build.build("musique", "fictional", stop_requested=lambda: len(events) >= 1,
                            progress=events.append)
                self.assertEqual(model.call_count, 1)
                resumed_events = []
                build.build("musique", "fictional", resume=True, progress=resumed_events.append)
                self.assertEqual(model.call_count, 2)
            self.assertEqual([x["processed_sources"] for x in resumed_events], [1, 2])
            self.assertEqual([x["cache_replay"] for x in resumed_events], [True, False])
            self.assertTrue(protocol.read(memory.with_suffix(".manifest.json"))["extraction_complete"])

    def test_strict_topic_contract_rejects_invalid_codes_before_vocabulary_mutation(self):
        for topics, proposals in [
            (["society/nobility"], []),
            (["nobility"], [{"code": "nobility", "parent": "society/nobility"}]),
            (["missing"], []),
            ([], []),
        ]:
            with self.subTest(topics=topics, proposals=proposals), tempfile.TemporaryDirectory() as directory:
                vocab = Vocab()
                vocab.define_root("society")
                before = list(vocab.oplog)
                binding = SimpleNamespace(EXTRACT_PROMPT="{dialog}", KINDS=["identity"],
                                          EXTRACT_SINGLE_SOURCE=True, EXTRACT_STRICT_TOPICS=True)
                unit = {"id": "u1", "date": "2000-01-01",
                        "utterances": [("u1L1", "source", "", "Cedar is a town.")]}
                raw = {"facts": [{"content": "Cedar is a town.", "kind": "identity",
                        "topics": topics, "entities": ["Cedar"]}],
                       "proposals": proposals, "entity_types": {}}
                with patch.object(native_extract, "llm_json", return_value=raw) as model:
                    for cached in [False, True]:
                        with self.assertRaisesRegex(RuntimeError, "topic"):
                            native_extract.extract_facts(vocab, binding, unit, cache_dir=directory,
                                                         cache_only=cached, log=lambda _: None)
                    self.assertEqual(model.call_count, 1)
                self.assertEqual(vocab.oplog, before)
                self.assertEqual(vocab.entities, {})
                saved, = Path(directory).glob("*.json")
                self.assertEqual(protocol.read(saved), raw)

    def test_strict_topic_contract_preserves_native_admission_parent_folding(self):
        vocab = Vocab(admission_k=1)
        vocab.define_root("people")
        binding = SimpleNamespace(EXTRACT_PROMPT="{dialog}", KINDS=["identity"],
                                  EXTRACT_SINGLE_SOURCE=True, EXTRACT_STRICT_TOPICS=True)
        unit = {"id": "u1", "date": "2000-01-01",
                "utterances": [("u1L1", "source", "", "Iris is a sculptor and a pilot.")]}
        raw = {"facts": [{"content": "Iris is a sculptor and a pilot.", "kind": "identity",
                "topics": ["sculpture", "aviation"], "entities": ["Iris"]}],
               "proposals": [{"code": "sculpture", "parent": "people"},
                             {"code": "aviation", "parent": "people"}], "entity_types": {}}
        with patch.object(native_extract, "llm_json", return_value=raw):
            facts = native_extract.extract_facts(vocab, binding, unit, log=lambda _: None)
        self.assertEqual(set(facts[0]["topics"]), {"sculpture", "people"})
        self.assertEqual(raw["facts"][0]["topics"], ["sculpture", "aviation"])

    def test_auxiliary_unknown_topic_keeps_all_facts_exactly_as_native_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            unit = {"id": "u1", "date": "2000-01-01",
                    "utterances": [("u1L1", "source", "", "Iris studies plants. Iris teaches botany.")]}
            raw = {"facts": [
                {"content": "Iris studies plants.", "kind": "identity",
                 "topics": ["science", "unregistered_subfield"], "entities": ["Iris"]},
                {"content": "Iris teaches botany.", "kind": "identity",
                 "topics": ["science"], "entities": ["Iris"]}],
                "proposals": [], "entity_types": {}}
            binding = SimpleNamespace(EXTRACT_PROMPT="{dialog}", KINDS=["identity"],
                                      EXTRACT_SINGLE_SOURCE=True, EXTRACT_STRICT_TOPICS=True)
            vocab = Vocab()
            vocab.define_root("science")
            messages = []
            with patch.object(native_extract, "llm_json", return_value=raw) as model:
                strict = native_extract.extract_facts(vocab, binding, unit, cache_dir=directory,
                                                     log=messages.append)
                fresh = Vocab()
                fresh.define_root("science")
                binding.EXTRACT_STRICT_TOPICS = False
                native = native_extract.extract_facts(fresh, binding, unit, cache_dir=directory,
                                                     cache_only=True, log=lambda _: None)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(strict, native)
            self.assertEqual(len(strict), len(raw["facts"]))
            self.assertTrue(all(fact["topics"] == ["science"] for fact in strict))
            self.assertEqual([f["content"] for f in strict], [f["content"] for f in raw["facts"]])
            self.assertTrue(any("unregistered_subfield" in message for message in messages))
            saved, = Path(directory).glob("*.json")
            self.assertEqual(protocol.read(saved), raw)

    def test_each_fact_still_needs_its_own_valid_topic_before_any_mutation(self):
        vocab = Vocab()
        vocab.define_root("science")
        before = list(vocab.oplog)
        raw = {"facts": [{"topics": ["science"]}, {"topics": ["unknown"]}], "proposals": []}
        with self.assertRaisesRegex(RuntimeError, "fact 2"):
            native_extract._validate_topic_references(vocab, raw)
        self.assertEqual(vocab.oplog, before)

    def test_strict_topic_contract_accepts_native_ordered_parent_proposals(self):
        vocab = Vocab()
        vocab.define_root("science")
        raw = {"proposals": [{"code": "biology", "parent": "science"},
                             {"code": "botany", "parent": "biology"}],
               "facts": [{"topics": ["botany"]}]}
        native_extract._validate_topic_references(vocab, raw)
        self.assertEqual(set(vocab.topics), {"science"})

    def test_executor_preserves_all_list_filters_accepted_by_public_plan_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root=ET.Element('codebook')
            vocabulary=ET.SubElement(root,'vocab')
            timeline=ET.SubElement(root,'timeline')
            for n in range(5):
                ET.SubElement(vocabulary,'topic',code=f'topic_{n}',status='canonical')
                unit=ET.SubElement(timeline,'session',id=f'u{n}',date='2000-01-01')
                ET.SubElement(unit,'line',id=f'u{n}L1',who='source').text=f'Record {n}.'
                text='Alpha Beta Gamma Delta' if n==0 else ('Alpha Beta Gamma Epsilon' if n==1 else f'Record {n}')
                ET.SubElement(unit,'fact',id=f'u{n}F1',topics=f'topic_{n}',entities=f'person_{n}',
                              who=f'person_{n}',kind=f'kind_{n}',obj=f'class_{n}',place=f'place_{n}',
                              event=f'event_{n}',src=f'u{n}L1').text=text
            path=Path(directory)/'book.xml'
            ET.ElementTree(root).write(path)
            store,vocab=Store.load([str(path)])
            cases=[(field,[f'{prefix}_{n}' for n in range(5)]) for field,prefix in [
                ('entities','person'),('who','person'),('topics','topic'),('kind','kind'),
                ('obj','class'),('place','place'),('event','event')]]
            cases.append(('grep_all',['Alpha','Beta','Gamma','Delta']))
            for field,values in cases:
                with self.subTest(field=field):
                    plan=QueryPlan.from_dict({'queries':[{'select':'facts','where':{field:values},'pipe':[]}]}).to_dict()
                    rows,_=execute_plan(store,vocab,plan,budget=20)
                    expected={'u0F1'} if field=='grep_all' else {f'u{n}F1' for n in range(5)}
                    self.assertEqual({r['id'] for r in rows if r['type']=='fact'},expected)

    def test_invalid_source_pointer_fails_before_vocabulary_changes_and_is_not_repaired(self):
        vocab = Vocab()
        vocab.define_root("people")
        unit = {"id":"u1234567890", "date":"2000-01-01", "speakers":["source"],
                "utterances":[("u1234567890L1","source","","Iris founded Cedar.")]}
        raw = {"facts":[{"content":"Iris founded Cedar.", "kind":"relationship",
                "topics":["people"], "entities":["Iris","Cedar"], "src":["u123456790L1"]}],
               "proposals":[], "entity_types":{}}
        binding = SimpleNamespace(EXTRACT_PROMPT="{dialog}", KINDS=["relationship"],
                                  EXTRACT_STRICT_SOURCES=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(native_extract,"llm_json",return_value=raw) as model:
            with self.assertRaisesRegex(RuntimeError,"source.*pointer"):
                native_extract.extract_facts(vocab,binding,unit,cache_dir=directory,log=lambda _:None)
            self.assertEqual(vocab.entities,{})
            with self.assertRaisesRegex(RuntimeError,"source.*pointer"):
                native_extract.extract_facts(vocab,binding,unit,cache_dir=directory,cache_only=True,log=lambda _:None)
            self.assertEqual(model.call_count,1)
            saved,=Path(directory).glob("*.json")
            self.assertEqual(protocol.read(saved)['facts'][0]['src'],["u123456790L1"])

    def test_builder_stops_at_first_extraction_failure_before_later_sources(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            base=Path(directory)
            data=base/'data'
            data.mkdir()
            docs=[adapter.document('Cedar','Iris founded Cedar.'),adapter.document('Bay','Bay is a port.')]
            for doc in docs:
                protocol.append(data/'corpus.jsonl',doc)
            protocol.dump(data/'manifest.json',{'sha256':{'corpus.jsonl':protocol.sha(data/'corpus.jsonl')},'scope':'fictional'})
            memory=base/'book.xml'
            with patch.object(protocol,'paths',return_value=(data,memory,base/'result',base/'cache')), patch.object(
                    build,'extract_facts',side_effect=RuntimeError('invalid source pointer')) as extract:
                build.build('musique','fictional')
            self.assertEqual(extract.call_count,1)
            manifest=protocol.read(memory.with_suffix('.manifest.json'))
            self.assertFalse(manifest['extraction_complete'])
            self.assertEqual(manifest['unprocessed_sources'],1)

    def test_malformed_fact_array_is_preserved_and_never_becomes_an_empty_success(self):
        vocab=Vocab()
        unit={'id':'u1','date':'2000-01-01','utterances':[('u1L1','source','','Iris founded Cedar.')]}
        binding=SimpleNamespace(EXTRACT_PROMPT='{dialog}',KINDS=[],EXTRACT_STRICT_SOURCES=True)
        raw={'facts':'Iris founded Cedar.','proposals':[],'entity_types':{}}
        with tempfile.TemporaryDirectory() as directory, patch.object(native_extract,'llm_json',return_value=raw) as model:
            with self.assertRaisesRegex(RuntimeError,'malformed extraction'):
                native_extract.extract_facts(vocab,binding,unit,cache_dir=directory,log=lambda _:None)
            saved,=Path(directory).glob('*.json')
            self.assertEqual(protocol.read(saved),raw)
            with self.assertRaisesRegex(RuntimeError,'cache-only'):
                native_extract.extract_facts(vocab,binding,unit,cache_dir=directory,cache_only=True,log=lambda _:None)
            self.assertEqual(model.call_count,1)

    def test_single_source_provenance_is_bound_from_input_and_rejects_multiple_lines(self):
        vocab=Vocab()
        vocab.define_root('people')
        text='Iris founded Cedar.'
        raw={'facts':[{'content':text,'kind':'relationship','topics':['people'],
                      'entities':['Iris','Cedar']}],'proposals':[],'entity_types':{}}
        unit={'id':'u1234567890','date':'2000-01-01','speakers':['source'],
              'utterances':[('u1234567890L1','source','',text)]}
        binding=SimpleNamespace(EXTRACT_PROMPT='{dialog}',KINDS=['relationship'],EXTRACT_SINGLE_SOURCE=True)
        with patch.object(native_extract,'llm_json',return_value=raw) as model:
            facts=native_extract.extract_facts(vocab,binding,unit,log=lambda _:None)
            self.assertEqual(facts[0]['content'],text)
            self.assertEqual(facts[0]['src'],['u1234567890L1'])
            self.assertNotIn('src',raw['facts'][0])  # Never mutate the raw model output.
            unit['utterances'].append(('u1234567890L2','source','','Cedar is a hall.'))
            with self.assertRaisesRegex(ValueError,'exactly one input'):
                native_extract.extract_facts(vocab,binding,unit,log=lambda _:None)
            self.assertEqual(model.call_count,1)

    def test_evaluation_rejects_incomplete_bank_before_model_or_question_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            memory=base/'book.xml'
            memory.write_text('<codebook/>')
            protocol.dump(memory.with_suffix('.manifest.json'),{'memory_sha256':protocol.sha(memory),
                          'extraction_complete':False,'failed_extractions':[{'id':'u1'}]})
            with patch.object(protocol,'paths',return_value=(base/'data',memory,base/'result',base/'cache')), patch.object(
                    evaluate,'answer') as model, self.assertRaisesRegex(RuntimeError,'incomplete'):
                evaluate.evaluate('musique','fictional')
            model.assert_not_called()
            self.assertFalse((base/'result').exists())

    def test_build_cli_returns_failure_for_an_incomplete_source_bank(self):
        with patch('sys.argv',['build','--dataset','musique','--tag','fictional']), patch.object(
                protocol,'configure'), patch.object(build,'build',return_value={
                    'status':'incomplete','extraction_complete':False}), self.assertRaises(SystemExit) as error:
            build.main()
        self.assertEqual(error.exception.code,1)

    def test_boundary_stop_drains_current_source_and_resumes_only_missing_sources(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            base=Path(directory)
            data=base/'data'
            data.mkdir()
            for doc in [adapter.document('Cedar','Cedar is a hall.'),adapter.document('Bay','Bay is a port.')]:
                protocol.append(data/'corpus.jsonl',doc)
            protocol.dump(data/'manifest.json',{'sha256':{'corpus.jsonl':protocol.sha(data/'corpus.jsonl')},'scope':'fictional'})
            stopped=[False]
            def response(*args,**kwargs):
                stopped[0]=True
                return {'facts':[],'proposals':[],'entity_types':{}}
            memory=base/'book.xml'
            with patch.object(protocol,'paths',return_value=(data,memory,base/'result',base/'cache')), patch.object(
                    native_extract,'llm_json',side_effect=response) as model:
                build.build('musique','fictional',stop_requested=lambda:stopped[0])
                first=protocol.read(memory.with_suffix('.manifest.json'))
                self.assertTrue(first['control_stop'])
                self.assertFalse(first['extraction_complete'])
                self.assertEqual(first['unprocessed_sources'],1)
                self.assertEqual(model.call_count,1)
                build.build('musique','fictional',resume=True,stop_requested=lambda:False)
                self.assertEqual(model.call_count,2)
                self.assertTrue(protocol.read(memory.with_suffix('.manifest.json'))['extraction_complete'])

    def test_context_entity_preview_exposes_late_mentions_without_expanding_the_catalog(self):
        vocab = Vocab()
        for n in range(130):
            vocab.register_entity(f"Aster Group {n:03d}")
        late = vocab.register_entity("Zephyr Observatory")
        vocab.alias_entity("Western Observatory", late)
        # Keep two homonyms separate even when both have an explicit alias.
        artist = vocab.register_entity("Iris (artist)")
        engineer = vocab.register_entity("Iris (engineer)")
        vocab.entities[artist].aliases.add("iris")
        vocab.entities[engineer].aliases.add("iris")
        bay = vocab.register_entity("Bay")
        source = "Iris worked at the Western Observatory in Bayfield."
        original = {c: (e.name, set(e.aliases)) for c, e in vocab.entities.items()}
        legacy = compile_plan._entity_preview_codes(vocab, source, 80)
        selected = compile_plan._entity_preview_codes(vocab, source, 80, by_mention=True)
        self.assertEqual(legacy, sorted(vocab.entities)[:80])
        self.assertNotIn(late, legacy)
        self.assertEqual(len(selected), 80)
        self.assertTrue({late, artist, engineer}.issubset(selected))
        self.assertNotIn(bay, selected[:3])  # Bay must not match Bayfield.
        self.assertEqual({c: (e.name, set(e.aliases)) for c, e in vocab.entities.items()}, original)
        self.assertNotEqual(artist, engineer)

    def test_context_entity_preview_preserves_small_catalogs_and_native_prompt_paths(self):
        vocab = Vocab()
        for code in ["Cedar", "Iris"]:
            vocab.register_entity(code)
        self.assertEqual(compile_plan._entity_preview_codes(vocab, "Iris", 80, by_mention=True),
                         sorted(vocab.entities))
        for n in range(130):
            vocab.register_entity(f"Aster Group {n:03d}")
        late = vocab.register_entity("Zephyr Observatory")
        unit = {"id": "u1", "title": "Zephyr Observatory", "date": "2000-01-01",
                "speakers": ["source"], "utterances": [("u1L1", "source", "", "Zephyr Observatory opened.")]}
        binding = SimpleNamespace(EXTRACT_PROMPT="{entities}", EXTRACT_FACETS=False,
                                  ENTITY_PREVIEW_BY_MENTION=True, KINDS=[])
        with patch.object(native_extract, "llm_json", return_value={}) as model:
            native_extract.extract_facts(vocab, binding, unit, log=lambda _: None)
        self.assertIn(late, model.call_args.args[0].split(", "))
        self.assertEqual(len(model.call_args.args[0].split(", ")), 80)
        binding.COMPILE_PROMPT = "{entities}"
        rendered = compile_plan._compile_prompt("Where is Zephyr Observatory?", vocab,
                                               SimpleNamespace(), binding)
        self.assertIn(late, rendered.split(", "))
        self.assertEqual(len(rendered.split(", ")), 120)

    def test_later_dependent_stage_survives_the_evidence_budget_with_bridge_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocabulary = ET.SubElement(root, "vocab")
            ET.SubElement(vocabulary, "topic", code="people", status="canonical")
            timeline = ET.SubElement(root, "timeline")
            for n, title in [(1, "Cedar"), (2, "Iris")]:
                unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01", title=title)
                texts = ([f"Cedar lists Iris as its lead in record {i}." for i in range(10)]
                         if n == 1 else ["Iris has an office in Bay."])
                ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = " ".join(texts)
                for i, text in enumerate(texts, 1):
                    ET.SubElement(unit, "fact", id=f"u{n}F{i}", topics="people",
                                  kind="relationship", src=f"u{n}L1").text = text
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            plan = {"stages": [
                {"name": "anchor", "select": "units", "where": {"grep": "Cedar"}, "pipe": []},
                {"name": "bridge", "select": "facts", "where": {"units": "$anchor.units"}, "pipe": []},
                {"name": "person", "select": "units", "where": {"grep": "Iris"}, "pipe": []},
                {"name": "property", "select": "facts", "where": {
                    "units": "$person.units", "grep": "office"}, "pipe": []},
            ]}
            rows, trace = execute_plan(store, vocab, QueryPlan.from_dict(plan).to_dict(),
                                       budget=5, unit_cap=10, pack_v2=True)
            self.assertTrue(any(t.get("name") == "property" and t.get("hits") == 1
                                for t in trace.stages))
            self.assertIn("u2F1", {r["id"] for r in rows})
            self.assertTrue(any(r.get("unit") == "u1" for r in rows))
            self.assertLessEqual(len(rows), 5)

    def test_complementary_unbound_property_stage_is_not_starved_by_bound_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            ET.SubElement(root, "vocab")
            timeline = ET.SubElement(root, "timeline")
            for n, texts in [(1, [f"Cedar employs Iris in record {i}." for i in range(12)]),
                             (2, ["Iris opened an office in Bay in April 2011."])]:
                unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01")
                ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = " ".join(texts)
                for i, text in enumerate(texts, 1):
                    ET.SubElement(unit, "fact", id=f"u{n}F{i}", src=f"u{n}L1").text = text
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            plan = {"stages": [
                {"name": "anchor", "select": "units", "where": {"grep": "Cedar"}, "pipe": []},
                {"name": "bridge", "select": "facts", "where": {"units": "$anchor.units"}, "pipe": []},
                {"name": "property", "select": "facts", "where": {"grep": "office"}, "pipe": []},
            ]}
            rows, trace = execute_plan(store, vocab, plan, budget=5, unit_cap=10, pack_v2=True)
            self.assertTrue(any(t.get("name") == "property" and t.get("hits") == 1
                                for t in trace.stages))
            self.assertIn("u2F1", {r["id"] for r in rows})
            self.assertTrue(any(r.get("unit") == "u1" for r in rows))
            self.assertLessEqual(len(rows), 5)

    def test_final_v2_selector_preserves_reserved_precise_property_before_broad_bound_tail(self):
        bound = [{"type": "fact", "id": f"b{i}", "unit": "bridge", "bound": True,
                  "score": 1.0} for i in range(10)]
        property_row = {"type": "fact", "id": "property", "unit": "other", "score": 4.9}
        plan_rows = bound[:2] + [property_row] + bound[2:]
        rows = retrieve._select_evidence_rows(plan_rows, plan_rows, budget=5, unit_cap=5,
                                             pack_v2=True)
        self.assertIn("property", {r["id"] for r in rows})
        self.assertEqual(len(rows), 5)

    def test_broad_bound_seed_does_not_bypass_diversity_and_hide_fused_anchor(self):
        bound = [{"type": "fact", "id": f"b{i}", "unit": "broad", "bound": True,
                  "score": 0.4} for i in range(20)]
        bound[0]["_plan_reserved"] = True
        anchor = {"type": "fact", "id": "anchor", "unit": "anchor-doc", "bound": True,
                  "score": 0.4}
        context = [{"type": "fact", "id": f"c{i}", "unit": f"context{i}", "score": 1.0}
                   for i in range(5)]
        plan_rows = bound + [anchor]
        fused = [anchor] + context + bound
        rows = retrieve._select_evidence_rows(plan_rows, fused, budget=6, unit_cap=2, pack_v2=True)
        self.assertIn("anchor", {r["id"] for r in rows})
        self.assertIn("b0", {r["id"] for r in rows})
        self.assertLessEqual(sum(r["unit"] == "broad" for r in rows), 2)
        self.assertEqual(len(rows), 6)
        legacy = retrieve._select_evidence_rows(plan_rows, fused, budget=6, unit_cap=2, pack_v2=False)
        self.assertEqual([r["id"] for r in legacy], [f"b{i}" for i in range(6)])

    def test_document_scope_does_not_preempt_fusion_with_many_distinct_sources(self):
        bound = [{"type": "fact", "id": f"b{i}", "unit": f"broad{i}", "bound": True,
                  "score": 0.4} for i in range(20)]
        bound[0]["_plan_reserved"] = True
        ranked = {"type": "fact", "id": "ranked", "unit": "relevant-doc", "score": 1.0}
        rows = retrieve._select_evidence_rows(bound, [ranked] + bound, budget=6, unit_cap=2, pack_v2=True)
        self.assertIn("b0", {r["id"] for r in rows})
        self.assertIn("ranked", {r["id"] for r in rows})
        self.assertEqual(len(rows), 6)

    def test_low_score_query_allocations_survive_the_complete_retrieval_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            vocab = Vocab()
            vocab.define_root("knowledge")
            root = ET.Element("codebook")
            timeline = ET.SubElement(root, "timeline")
            docs = [
                ("a1", "Nova Alloy", [("Nova Alloy was discovered by Elin Tor.",
                                        ["Nova Alloy", "Elin Tor"])]),
                ("a2", "Elin Tor", [("Elin Tor works for Solace Institute.",
                                     ["Elin Tor", "Solace Institute"])]),
                ("a6", "Solace Institute", [("Solace Institute was founded by Nika Rao.",
                                             ["Solace Institute", "Nika Rao"])])]
            for i in range(20):
                docs.append((f"z{i}", f"Other Institute {i}", [
                    (f"Other Institute {i} was founded by Other Person {i}.",
                     [f"Other Institute {i}", f"Other Person {i}"]),
                    (f"Other Person {i} works for Other Institute {i}, headquartered elsewhere.",
                     [f"Other Person {i}", f"Other Institute {i}"])]))
            for uid, title, facts in docs:
                unit = ET.SubElement(timeline, "session", id=uid, title=title, date="2000-01-01")
                ET.SubElement(unit, "line", id=uid + "L1", who="source").text = " ".join(f[0] for f in facts)
                for i, (text, names) in enumerate(facts, 1):
                    codes = [vocab.register_entity(name) for name in names]
                    ET.SubElement(unit, "fact", id=f"{uid}F{i}", src=uid + "L1", topics="knowledge",
                                  entities=" ".join(codes), kind="relationship", conf="high").text = text
            root.insert(0, vocab.to_xml())
            path = Path(directory) / "bank.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            employment = {"select": "facts", "where": {
                "grep": "employ|employer|employee|works for|working for|staff of"}, "pipe": []}
            founding = {"select": "facts", "where": {
                "grep": "founder|founded|established|set up"}, "pipe": []}
            plans = [
                {"intent": "factual", "queries": [
                    {"select": "facts", "where": {"entities": ["nova_alloy"],
                     "grep": "discover|discovered|discovery"}, "pipe": []}, employment, founding]},
                {"intent": "factual", "stages": [
                    {"name": "anchor", "select": "units", "where": {"grep": "Nova Alloy"}, "pipe": []},
                    {"name": "discoverer", "select": "facts", "where": {
                        "units": "$anchor.units", "grep": "discover|discovered|discovery"}, "pipe": []},
                    {"name": "employer", **employment}, {"name": "founder", **founding}]}]
            question = "Who founded the organization employing the person who discovered Nova Alloy?"
            for plan in plans:
                with self.subTest(shape="stages" if "stages" in plan else "queries"):
                    inner, _ = execute_plan(store, vocab, plan, budget=40, unit_cap=5, pack_v2=True)
                    bridge = next(row for row in inner if row["id"] == "a2F1")
                    self.assertLess(bridge["score"], retrieve._PRECISE_SCORE)
                    with patch.object(retrieve, "compile_plan", return_value=plan):
                        result = retrieve.retrieve(store, vocab, profile, question, budget=40)
                    self.assertTrue({"a1F1", "a2F1", "a6F1"}.issubset({r["id"] for r in result["rows"]}))
                    self.assertLessEqual(len(result["rows"]), 40)
                    legacy, _ = execute_plan(store, vocab, plan, budget=40, unit_cap=5, pack_v2=False)
                    self.assertFalse(any(row.get("_plan_reserved") for row in legacy))

    def test_token_admission_keeps_reserved_property_without_exceeding_its_caps(self):
        property_row = {"type": "fact", "id": "p", "unit": "property", "score": 1.0,
                        "_plan_reserved": True}
        distractors = [{"type": "fact", "id": f"d{i}", "unit": f"other{i}", "score": 5.0}
                       for i in range(3)]
        with patch.object(retrieve, "_row_tokens", return_value=1):
            rows = retrieve._select_evidence_rows_tokencap(
                [property_row] + distractors, distractors + [property_row], None,
                cap=2, unit_cap=1, row_cap=2, speaker=False)
        self.assertIn("p", {row["id"] for row in rows})
        self.assertEqual(len(rows), 2)

    def test_relaxing_a_grep_conjunction_retains_its_terms_as_ranking_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            ET.SubElement(root, "vocab")
            timeline = ET.SubElement(root, "timeline")
            unit = ET.SubElement(timeline, "session", id="u", date="2000-01-01")
            texts = ["Cedar opened a shop.", "Cedar hosted the festival.",
                     "Iris was compared to Fern for her work."]
            ET.SubElement(unit, "line", id="uL1", who="source").text = " ".join(texts)
            for i, text in enumerate(texts):
                ET.SubElement(unit, "fact", id=f"uF{i}", src="uL1").text = text
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            plan = {"queries": [{"select": "facts", "where": {"units": ["u"],
                    "grep_all": ["festival", "compar", "Iris"]}, "pipe": []}]}
            rows, trace = execute_plan(store, vocab, plan, budget=1, pack_v2=True)
            self.assertTrue(any("grep" in t.get("steps", []) for t in trace.stages))
            self.assertEqual([r["id"] for r in rows], ["uF2"])

    def test_single_pass_keeps_a_relation_endpoint_after_the_fifth_entity(self):
        vocabulary = Vocab()
        vocabulary.define_root("people")
        names = ["Orchid Council", "Ada", "Ben", "Cam", "Dee", "Eli", "Fay"]
        text = "Orchid Council has Ada, Ben, Cam, Dee, Eli and Fay as members."
        unit = {"id": "u1", "date": "2000-01-01", "title": "Orchid Council",
                "speakers": ["source"], "utterances": [("u1L1", "source", "", text)]}
        raw = {"facts": [{"content": text, "kind": "relationship", "who": "Orchid Council",
                          "t": None, "conf": "high", "topics": ["people"],
                          "entities": names, "src": ["u1L1"]}],
               "proposals": [], "entity_types": {}}
        with patch.object(native_extract, "llm_json", return_value=raw) as model:
            facts = native_extract.extract_facts(vocabulary, profile, unit, log=lambda _: None)
        self.assertEqual(model.call_count, 1)
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            root.append(vocabulary.to_xml())
            timeline = ET.SubElement(root, "timeline")
            native_extract.append_session_to_xml(timeline, profile, unit, facts)
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            plan = {"queries": [{"select": "facts", "where": {"entities": ["Fay"]}, "pipe": []}]}
            rows, _ = execute_plan(store, vocab, QueryPlan.from_dict(plan).to_dict(), budget=10)
            self.assertEqual({r["id"] for r in rows if r["type"] == "fact"}, {"u1F1"})
            self.assertEqual(set(facts[0]["entities"]),
                             {"orchid_council", "ada", "ben", "cam", "dee", "eli", "fay"})

    def test_native_entity_class_expansion_preserves_distinct_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocabulary = ET.SubElement(root, "vocab")
            ET.SubElement(vocabulary, "topic", code="organizations", status="canonical")
            timeline = ET.SubElement(root, "timeline")
            documents = [
                ("cedar_labs iris", "Cedar Labs is owned by Iris."),
                ("juniper_labs rowan", "Juniper Labs is owned by Rowan."),
                ("cedar_research iris", "Cedar Research has an office in Bay."),
            ]
            for n, (entities, text) in enumerate(documents, 1):
                unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01")
                ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = text
                ET.SubElement(unit, "fact", id=f"u{n}F1", topics="organizations",
                              entities=entities, obj="organization", kind="relationship",
                              src=f"u{n}L1").text = text
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            vocab.register_entity("Cedar Labs")
            vocab.entities["cedar_labs"].aliases.add("cedar_research")
            for where, expected in [
                ({"entities": ["Cedar Labs"]}, {"u1F1", "u2F1", "u3F1"}),
                ({"obj": ["organization"]}, {"u1F1", "u2F1", "u3F1"}),
            ]:
                with self.subTest(where=where):
                    plan = {"queries": [{"select": "facts", "where": where, "pipe": []}]}
                    rows, trace = execute_plan(store, vocab, QueryPlan.from_dict(plan).to_dict(), budget=20)
                    self.assertEqual({r["id"] for r in rows if r["type"] == "fact"}, expected)
                    if "entities" in where:
                        self.assertTrue(any(t.get("atom") == "obj_bridge" for t in trace.stages))
                    self.assertEqual(store.facts["u2F1"].entities, ("juniper_labs", "rowan"))
                    self.assertNotEqual(vocab.resolve_entity("Cedar Labs"),
                                        vocab.resolve_entity("Juniper Labs"))

    def test_extraction_root_coverage_is_opt_in_and_preserves_complete_previews(self):
        vocab = Vocab()
        for code in ["arts", "sports"]:
            vocab.define_root(code)
        legacy, complete = SimpleNamespace(), SimpleNamespace(EXTRACT_COMPLETE_ROOTS=True)
        original = vocab.render_tree(max_lines=150)
        self.assertEqual(native_extract._render_extraction_tree(vocab, complete), original)
        for n in range(160):
            vocab.propose_topic(f"arts_branch_{n:03d}", "arts", f"u{n}", "")
        original = vocab.render_tree(max_lines=150)
        self.assertNotIn("sports", original.splitlines())
        self.assertEqual(native_extract._render_extraction_tree(vocab, legacy), original)
        self.assertEqual(native_extract._render_extraction_tree(vocab, complete), original + "\nsports")

    def test_compiler_lists_all_roots_even_when_the_tree_preview_omits_one(self):
        vocabulary = SimpleNamespace(
            entities={}, topics={code: SimpleNamespace(parents=set(), status="canonical")
                                 for code in ["arts", "sports"]},
            render_tree=lambda max_lines: "arts\n  works")
        binding = SimpleNamespace(COMPILE_PROMPT="ROOTS: {roots}\nTREE: {tree}\n{question}", KINDS=[])
        rendered = compile_plan._compile_prompt("Fictional question?", vocabulary, SimpleNamespace(), binding)
        self.assertIn("ROOTS: arts, sports", rendered)
        self.assertIn("TREE: arts\n  works", rendered)

    def test_structured_answer_transport_is_explicit_and_uses_one_request(self):
        response = {"model": "gpt-4o-mini", "choices": [{"finish_reason": "stop",
                    "message": {"content": '{"evidence":["u1F1"],"answer":"Bay"}'}}]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fictional-key", "KITE_LLM_RETRIES": "0"}), patch.object(
            llm.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as http:
            result = llm.llm_json("Return JSON for a fictional source.", model="gpt-4o-mini",
                                  response_format=profile.ANSWER_RESPONSE_FORMAT)
            payload = json.loads(http.call_args.args[0].data)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(payload["response_format"], profile.ANSWER_RESPONSE_FORMAT)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(result["answer"], "Bay")

    def test_answer_format_is_opt_in_without_changing_legacy_calls(self):
        for enabled in [False, True]:
            binding = SimpleNamespace(ANSWER_PROMPT="Return JSON. {question}\n{evidence}")
            if enabled:
                binding.ANSWER_RESPONSE_FORMAT = profile.ANSWER_RESPONSE_FORMAT
            with patch("memoket_kite.pipeline.answer.llm_json", return_value={
                "evidence": ["u1F1"], "answer": "Bay"}) as model:
                result = _generate_answer(EvidencePack(
                    rows=[{"type": "fact", "id": "u1F1", "src": "u1L1"}],
                    lines=["u1F1: Iris lives in Bay."], instances=[]),
                    binding, "Where does Iris live?", "", "", "gpt-4o-mini",
                    SimpleNamespace(self_consistency=0))
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result["answer"], "Bay")
            if enabled:
                schema = model.call_args.kwargs["response_format"]["json_schema"]["schema"]
                self.assertEqual(schema["properties"]["evidence"]["items"]["enum"], ["u1F1", "u1L1"])
                self.assertNotIn("enum", profile.ANSWER_RESPONSE_FORMAT["json_schema"]["schema"]["properties"]["evidence"]["items"])
            else:
                self.assertNotIn("response_format", model.call_args.kwargs)

    def test_structured_citations_only_allow_rendered_provenance_ids(self):
        pack = EvidencePack(rows=[{"type": "fact", "id": "u1F1", "src": "u1L1"}],
                            lines=["(u1F1, src=u1L1) Iris lives in Bay."], instances=[],
                            shown_context_ids=frozenset({"u2L1"}))
        with patch("memoket_kite.pipeline.answer.llm_json", return_value={
            "evidence": ["u1F1"], "answer": "Bay"}) as model:
            _generate_answer(pack, profile, "Where does Iris live?", "", "", "gpt-4o-mini",
                             SimpleNamespace(self_consistency=0))
        self.assertEqual(model.call_count, 1)
        schema = model.call_args.kwargs["response_format"]["json_schema"]["schema"]
        allowed = schema["properties"]["evidence"]["items"]["enum"]
        self.assertEqual(set(allowed), {"u1F1", "u1L1", "u2L1"})
        self.assertNotIn("(u1F1, src=u1L1) Iris lives in Bay.", allowed)
        self.assertNotIn("invisibleF1", allowed)

    def test_structured_citations_are_empty_when_no_provenance_was_shown(self):
        with patch("memoket_kite.pipeline.answer.llm_json", return_value={
            "evidence": [], "answer": "No information"}) as model:
            _generate_answer(EvidencePack(rows=[], lines=["No sourced facts."], instances=[]),
                             profile, "Where does Iris live?", "", "", "gpt-4o-mini",
                             SimpleNamespace(self_consistency=0))
        evidence = model.call_args.kwargs["response_format"]["json_schema"]["schema"]["properties"]["evidence"]
        self.assertEqual(evidence["maxItems"], 0)

    def test_native_document_scope_reaches_alias_properties_and_cross_document_mentions(self):
        cases = [
            ("alias", [
                ("Cedar", [("Iris is professionally known as Cedar.", "identity", ""),
                           ("Iris was born in 1924.", "event", "birth")]),
                ("Rowan", [("Rowan was born in 1930.", "event", "birth")]),
                ("Other", [("Sage was born in 1900.", "event", "birth")])],
             "Cedar|Rowan", {"event": ["birth"]}, {"u1F2", "u2F1"}),
            ("cross_document", [
                ("Cedar", [("Cedar's sponsor is Iris.", "relationship", "")]),
                ("Iris", [("Iris sponsors Cedar.", "relationship", ""),
                          ("Iris has an office in Bay.", "relationship", "")]),
                ("Other", [("Sage has an office in Dale.", "relationship", "")])],
             "Cedar", {"grep": "sponsor|office"}, {"u1F1", "u2F1", "u2F2"}),
        ]
        for label, documents, anchor, predicate, expected in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = ET.Element("codebook")
                vocabulary = ET.SubElement(root, "vocab")
                ET.SubElement(vocabulary, "topic", code="people", status="canonical")
                timeline = ET.SubElement(root, "timeline")
                for n, (title, facts) in enumerate(documents, 1):
                    unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01", title=title)
                    ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = " ".join(f[0] for f in facts)
                    for j, (text, kind, event) in enumerate(facts, 1):
                        ET.SubElement(unit, "fact", id=f"u{n}F{j}", topics="people",
                                      src=f"u{n}L1", kind=kind, event=event).text = text
                path = Path(directory) / "book.xml"
                ET.ElementTree(root).write(path)
                store, vocab = Store.load([str(path)])
                plan = {"stages": [
                    {"name": "sources", "select": "units", "where": {"grep": anchor}, "pipe": []},
                    {"name": "claims", "select": "facts", "where": {
                        "units": "$sources.units", **predicate}, "pipe": []}]}
                rows, trace = execute_plan(store, vocab, QueryPlan.from_dict(plan).to_dict(), budget=20)
                self.assertEqual({r["id"] for r in rows if r["type"] == "fact"}, expected)
                self.assertTrue(any(t.get("bound") and t.get("hits") for t in trace.stages))

    def test_document_filters_and_stage_references_keep_the_entire_requested_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocabulary = ET.SubElement(root, "vocab")
            ET.SubElement(vocabulary, "topic", code="arts", status="canonical")
            timeline = ET.SubElement(root, "timeline")
            for n in range(1, 10):
                unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01",
                                     title=f"Common collection item {n}")
                text = "Zinnia is a work." if n == 9 else "Cedar is a work."
                ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = text
                ET.SubElement(unit, "fact", id=f"u{n}F1", topics="arts", src=f"u{n}L1",
                              kind="identity").text = text
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            explicit = {"queries": [{"select": "facts", "where": {
                "units": [f"u{n}" for n in range(1, 10)], "grep": "Zinnia"}, "pipe": []}]}
            dependent = {"stages": [
                {"name": "s1", "select": "units", "where": {"grep": "Common"}, "pipe": []},
                {"name": "s2", "select": "facts", "where": {
                    "units": "$s1.units", "grep": "Zinnia"}, "pipe": []}]}
            for plan in [explicit, dependent]:
                with self.subTest(shape="stages" if "stages" in plan else "queries"):
                    validated = QueryPlan.from_dict(plan).to_dict()
                    rows, _ = execute_plan(store, vocab, validated, budget=20)
                    self.assertEqual({r["id"] for r in rows if r["type"] == "fact"}, {"u9F1"})

    def test_prefix_reuse_replays_only_exact_prompts_and_extracts_only_new_sources(self):
        for change in [None, "order", "prompt", "cache", "effort"]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
                base = Path(directory)

                def paths(tag, dataset):
                    return base / tag / "data", base / tag / "book.xml", base / tag / "result", base / tag / "cache"

                docs = [adapter.document("Cedar", "Cedar was made by Iris."),
                        adapter.document("Rowan", "Rowan was made by Iris.")]
                for tag, sources in [("old", docs[:1]), ("new", docs[::-1] if change == "order" else docs)]:
                    data, _, _, _ = paths(tag, "musique")
                    data.mkdir(parents=True)
                    for doc in sources:
                        protocol.append(data / "corpus.jsonl", doc)
                    protocol.dump(data / "manifest.json", {"sha256": {
                        "corpus.jsonl": protocol.sha(data / "corpus.jsonl")}, "scope": "fictional prefix test"})

                raw = {"facts": [{"content": "Cedar was made by Iris.", "kind": "relationship",
                                  "who": "Cedar", "t": None, "conf": "high", "topics": ["arts"],
                                  "entities": ["Cedar", "Iris"], "src": [docs[0]["id"] + "L1"]}],
                       "proposals": [], "entity_types": {}}
                with patch.object(protocol, "paths", side_effect=paths), patch.object(
                    native_extract, "llm_json", return_value=raw) as model:
                    build.build("musique", "old")
                    self.assertEqual(model.call_count, 1)
                    model.reset_mock()
                    if change == "cache":
                        for path in (paths("old", "musique")[3] / "raw").glob("*.json"):
                            path.write_text("{corrupt")
                    with patch.object(profile, "EXTRACT_REASONING_EFFORT",
                                      "medium" if change == "effort" else profile.EXTRACT_REASONING_EFFORT), patch.object(
                            profile, "EXTRACT_PROMPT", profile.EXTRACT_PROMPT + (
                                "\nChanged extraction instructions." if change == "prompt" else "")):
                        if change:
                            with self.assertRaises(RuntimeError):
                                build.build("musique", "new", reuse_from="old")
                            self.assertEqual(model.call_count, 0)
                        else:
                            build.build("musique", "new", reuse_from="old")
                            self.assertEqual(model.call_count, 1)
                            old_tree = ET.parse(paths("old", "musique")[1])
                            new_tree = ET.parse(paths("new", "musique")[1])
                            old_session, new_session = old_tree.find("timeline/session"), new_tree.find("timeline/session")
                            old_session.tail = new_session.tail = None
                            self.assertEqual(ET.tostring(old_session), ET.tostring(new_session))
                            model.reset_mock()
                            build.build("musique", "new", resume=True, reuse_from="old")
                            self.assertEqual(model.call_count, 0)

    def test_event_date_padding_preserves_calendar_value_and_precision(self):
        for raw, expected in [("73-2-9", "0073-02-09"), ("73-2", "0073-02"),
                              ("2020-2-29", "2020-02-29"), ("1984-03-12", "1984-03-12")]:
            with self.subTest(raw=raw):
                self.assertEqual(native_extract._normalize_fact_time(raw), expected)
        for unsupported in ["73", "before 73", "73–75", "2001-2-29", "0-1-1", "12000-1-1"]:
            with self.subTest(unsupported=unsupported):
                self.assertEqual(native_extract._normalize_fact_time(unsupported), "")

    def test_shared_prompt_reference_forms_execute_the_existing_document_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocabulary = ET.SubElement(root, "vocab")
            ET.SubElement(vocabulary, "topic", code="arts", status="canonical")
            timeline = ET.SubElement(root, "timeline")
            for n, title in [(1, "Cedar"), (2, "Rowan")]:
                unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01", title=title)
                ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = title + " is a work."
                ET.SubElement(unit, "fact", id=f"u{n}F1", topics="arts", src=f"u{n}L1",
                              kind="identity").text = title + " is a work."
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])
            for reference in ["$s1.units", ["$s1.unit"]]:
                plan = {"intent": "factual", "stages": [
                    {"name": "s1", "select": "units", "where": {"grep": "Cedar"}, "pipe": []},
                    {"name": "s2", "select": "facts", "where": {"units": reference}, "pipe": []}]}
                validated = QueryPlan.from_dict(plan).to_dict()
                rows, trace = execute_plan(store, vocab, validated, budget=10)
                self.assertEqual({r["id"] for r in rows if r["type"] == "fact"}, {"u1F1"})
                self.assertTrue(any(t.get("bound") and t.get("hits") for t in trace.stages))
            plan["stages"][1]["where"] = {"unit": "$s1.units"}
            with self.assertRaises(InvalidQueryError):
                QueryPlan.from_dict(plan)

    def test_unknown_symbol_is_not_an_unconstrained_corpus_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocabulary = ET.SubElement(root, "vocab")
            ET.SubElement(vocabulary, "topic", code="arts", status="canonical")
            timeline = ET.SubElement(root, "timeline")
            for n, text in [(1, "Cedar was made by Iris."), (2, "Rowan lived in Bay.")]:
                unit = ET.SubElement(timeline, "session", id=f"u{n}", date="2000-01-01", title="Fictional")
                ET.SubElement(unit, "line", id=f"u{n}L1", who="source").text = text
                ET.SubElement(unit, "fact", id=f"u{n}F1", topics="arts",
                              src=f"u{n}L1", kind="relationship").text = text
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocab = Store.load([str(path)])

            def query(where):
                return execute_plan(store, vocab, {"queries": [
                    {"select": "facts", "where": where, "pipe": []}]}, budget=10)[0]

            # Explicit full-corpus queries and live alternatives retain their
            # original semantics; only the accidental loss of an anchor fails.
            self.assertEqual(len(query({})), 2)
            self.assertEqual(len(query({"topics": [{"code": "arts", "closure": True},
                                                   {"code": "absent_topic", "closure": True}]})), 2)
            for where in [{"entities": ["absent_entity"]},
                          {"topics": [{"code": "absent_topic", "closure": True}]}]:
                with self.subTest(where=where):
                    self.assertEqual(query(where), [])

    def test_named_place_keeps_identity_while_object_classes_keep_native_singularization(self):
        facets = native_extract._normalize_fact_facets(
            {"object": ["cities"], "place": ["Cedar Hills"], "event": ["births"]}, {"birth"})
        self.assertEqual(facets["obj"], ["city"])
        self.assertEqual(facets["event"], ["birth"])
        self.assertEqual(facets["place"], ["cedar_hills"])

    def test_attempted_source_with_stale_or_corrupt_cache_is_never_resent(self):
        for raw_cache_value in ["{invalid", '{"facts":[],"proposals":[],"entity_types":{}}']:
            with self.subTest(raw_cache_value=raw_cache_value), tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
                base = Path(directory)
                data, cache, memory = base / "data", base / "cache", base / "book.xml"
                data.mkdir()
                (cache / "raw").mkdir(parents=True)
                doc = adapter.document("Cedar", "Cedar was made by Iris.")
                p = protocol
                p.append(data / "corpus.jsonl", doc)
                p.dump(data / "manifest.json", {"sha256": {"corpus.jsonl": p.sha(data / "corpus.jsonl")},
                       "scope": "fictional cache integrity fixture"})
                p.dump(cache / "build.json", {"source": p.sha(data / "corpus.jsonl"), "code": p.source_hashes(),
                       "model": "gpt-6-luna", "reasoning_effort": profile.EXTRACT_REASONING_EFFORT, "extraction_rounds": 1})
                p.append(cache / "extract.jsonl", {"event": "request", "item": doc["id"],
                         "call_id": "prior-unknown", "request": {"model": "gpt-6-luna"}})
                (cache / "raw" / (doc["id"] + ".wrong-fingerprint.json")).write_text(raw_cache_value)
                with patch.object(p, "paths", return_value=(data, memory, base / "result", cache)), patch.object(
                    native_extract, "llm_json", return_value={"facts": [], "proposals": [], "entity_types": {}}) as model:
                    build.build("musique", "fictional", resume=True)
                self.assertEqual(model.call_count, 0)
                self.assertFalse(p.read(memory.with_suffix(".manifest.json"))["extraction_complete"])
                self.assertEqual(ET.parse(memory).find("timeline/session/line").text,
                                 "Cedar\nCedar was made by Iris.")

    def test_cache_only_replays_valid_response_and_fails_closed_on_a_miss(self):
        def vocabulary():
            result = Vocab(admission_k=4, promote_units=3)
            for root in profile.SEED_ROOTS:
                result.define_root(root)
            return result

        doc = adapter.document("Cedar", "Cedar was made by Iris.")
        unit = next(adapter.iter_units([doc]))
        raw = {"facts": [{"content": "Cedar was made by Iris.", "kind": "relationship",
                         "who": "Cedar", "t": None, "conf": "high", "topics": ["arts"],
                         "entities": ["Cedar", "Iris"], "src": [doc["id"] + "L1"]}],
               "proposals": [], "entity_types": {}}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(native_extract, "llm_json", return_value=raw) as model:
                original = native_extract.extract_facts(vocabulary(), profile, unit,
                    model="gpt-6-luna", cache_dir=directory, log=lambda *args: None)
            self.assertEqual(model.call_count, 1)
            with patch.object(native_extract, "llm_json", side_effect=AssertionError("no duplicate call")) as model:
                replayed = native_extract.extract_facts(vocabulary(), profile, unit,
                    model="gpt-6-luna", cache_dir=directory, cache_only=True, log=lambda *args: None)
                self.assertEqual(replayed, original)
                self.assertEqual(len(replayed), 1)
                changed = {**unit, "utterances": [(doc["id"] + "L1", "source", "", "Different source.")]}
                with self.assertRaisesRegex(RuntimeError, "cache-only"):
                    native_extract.extract_facts(vocabulary(), profile, changed,
                        model="gpt-6-luna", cache_dir=directory, cache_only=True, log=lambda *args: None)
                self.assertEqual(model.call_count, 0)

    def rendering_fixture(self):
        lines = {f"uL{i}": SimpleNamespace(id=f"uL{i}", unit="u", unit_date="2000-01-01",
                 who="source", text=f"Fictional passage {i}. " + "x" * 800) for i in range(1, 7)}
        facts = {f"uF{i}": SimpleNamespace(id=f"uF{i}", unit="u", unit_date="2000-01-01",
                 t="1971-05-06" if i == 1 else "", when="1971-05-06" if i == 1 else "2000-01-01",
                 kind="relationship", who="Iris", conf="high", src=("uL1",),
                 text=f"Fictional claim {i}.") for i in range(1, 4)}
        return SimpleNamespace(lines=lines, facts=facts)

    def test_shared_source_rendering_preserves_facts_ids_and_real_event_dates(self):
        store = self.rendering_fixture()
        rows = [fact_row(f, 1) for f in store.facts.values()]
        rows[0]["bound"] = True
        rendered = "\n".join(_render_compact_sources(rows, store, AnswerOptions.from_profile(profile)))
        for row in rows:
            self.assertIn(row["id"], rendered)
            self.assertIn(row["text"], rendered)
        self.assertEqual(rendered.count(store.lines["uL1"].text), 1)
        self.assertEqual(rendered.count("2000-01-01"), 1)
        self.assertIn("event=1971-05-06", rendered)
        self.assertIn("[anchored] (uF1, src=uL1)", rendered)
        self.assertIn("not proof of a relationship", rendered)

    def test_compact_hydration_retains_late_assertions_without_new_rows(self):
        store = self.rendering_fixture()
        source = "Fictional biography. " + "Earlier career detail. " * 30
        source += "Iris earned a degree at Cedar Academy. Later Iris moved to Bay."
        store.lines["uL1"].text = source
        rows = [fact_row(store.facts["uF1"], 1), fact_row(store.facts["uF2"], 1)]
        original = deepcopy(rows)
        rendered = "\n".join(_render_compact_sources(
            rows, store, AnswerOptions.from_profile(profile)))
        self.assertEqual(rows, original)
        self.assertEqual(rendered.count(source), 1)
        self.assertIn("Iris earned a degree at Cedar Academy.", rendered)
        self.assertEqual(rendered.count("(uL1,"), 1)
        self.assertNotIn("uL2", rendered)

    def test_selected_full_source_is_shown_once_without_losing_metadata(self):
        store = self.rendering_fixture()
        rows = [fact_row(f, 1) for f in store.facts.values()] + [line_row(store.lines["uL1"], 1)]
        rendered = "\n".join(_render_compact_sources(rows, store, AnswerOptions.from_profile(profile)))
        self.assertEqual(rendered.count(store.lines["uL1"].text), 1)
        self.assertEqual(rendered.count("(uL1,"), 1)
        self.assertIn("recorded=2000-01-01", rendered)

    def test_source_limit_matches_native_renderer_including_missing_references(self):
        store = self.rendering_fixture()
        row = fact_row(store.facts["uF1"], 1)
        row["src"] = "missing uL1 uL2 uL3 uL4 uL5 uL6"
        for hydrate, limit, chars in [(True, 4, 400), (False, 2, 120)]:
            from dataclasses import replace
            options = replace(AnswerOptions.from_profile(profile), hydrate_sources=hydrate, speaker_label=True)
            rendered = "\n".join(_render_compact_sources([row], store, options))
            self.assertIn(row["src"], rendered)  # Keep all provenance, even missing IDs.
            for i in range(1, limit + 1):
                self.assertIn(("source: " + store.lines[f"uL{i}"].text)[:chars], rendered)
            self.assertNotIn(f"Fictional passage {limit + 1}.", rendered)

    def test_ordinary_profile_keeps_identical_native_evidence_format(self):
        from dataclasses import replace
        store = self.rendering_fixture()
        rows = [fact_row(f, 1) for f in store.facts.values()]
        options = replace(AnswerOptions.from_profile(profile), compact_sources=False,
                          enum_basis=False, neighbor_radius=0)
        pack = _build_evidence_pack(SimpleNamespace(rows=rows, intent="factual"), store,
                                    profile, "Fictional question?", options)
        expected = [_render_evidence_row(r, store, options.hydrate_sources,
                    dual_date=options.dual_date, speaker=options.speaker_label) for r in rows]
        self.assertEqual(pack.lines, expected)
        self.assertEqual(pack.allowed_citation_ids(), frozenset([*store.facts, "uL1"]))
        self.assertFalse(AnswerOptions.from_profile(SimpleNamespace()).compact_sources)
        self.assertEqual(_render_compact_sources([], store, options), [])

    def test_compiler_avoids_unused_directory_and_preserves_requested_dates(self):
        class UnusedDirectory:
            def values(self):
                raise AssertionError("unused directory must not be scanned")
        vocabulary = SimpleNamespace(entities={}, render_tree=lambda max_lines: "arts")
        binding = SimpleNamespace(COMPILE_PROMPT="{tree} {entities} {question}", KINDS=[])
        self.assertEqual(compile_plan._compile_prompt("Cedar?", vocabulary,
                         SimpleNamespace(units=UnusedDirectory()), binding), "arts (none) Cedar?")
        binding.COMPILE_PROMPT = "{units}|{today}|{question}"
        store = SimpleNamespace(units={"b": SimpleNamespace(id="b", date="2021-02-03", t=""),
                                       "a": SimpleNamespace(id="a", date="2019-04-05", t="")})
        self.assertEqual(compile_plan._compile_prompt("Cedar?", vocabulary, store, binding),
                         "a=2019-04-05; b=2021-02-03|2021-02-03|Cedar?")

    def test_resume_checks_extraction_not_unrelated_evaluation_code(self):
        old = {"source": "s", "model": "gpt-6-luna", "reasoning_effort": "medium", "extraction_rounds": 1,
               "code": {"benchmarks/multihop/profile.py": "old", "benchmarks/multihop/evaluate.py": "eval-old"}}
        changed = {**old, "code": {**old["code"], "benchmarks/multihop/evaluate.py": "eval-new"}}
        self.assertTrue(build.same_extraction(old, changed))
        changed["code"]["benchmarks/multihop/profile.py"] = "new-profile"
        self.assertFalse(build.same_extraction(old, changed))

    def test_native_packed_ids_resolve_to_source_documents(self):
        store = SimpleNamespace(facts={"f1": SimpleNamespace(unit="u1")},
                                lines={"l2": SimpleNamespace(unit="u2")})
        result = {"pack_rows": [{"id": "f1", "type": "fact", "src": "l1"}], "pack_src": ["l2"]}
        self.assertEqual(evidence_units(result, store), {"u1", "u2"})

    def test_candidate_pool_ignores_support_labels(self):
        row = {"paragraphs": [
            {"title": "Cedar", "paragraph_text": "Cedar is a work.", "is_supporting": True},
            {"title": "Iris", "paragraph_text": "Iris is a person.", "is_supporting": False}]}
        first = adapter.candidate_documents(row)
        for passage in row["paragraphs"]:
            passage["is_supporting"] = not passage["is_supporting"]
        self.assertEqual(first, adapter.candidate_documents(row))
        self.assertEqual(len(first), 2)

    def test_context_format_adapter_preserves_all_sentences(self):
        docs = adapter.candidate_documents({"context": [["Cedar", ["First. ", "Second."]]]})
        self.assertEqual(docs[0]["text"], "First. Second.")
        unit = next(adapter.iter_units(docs))
        self.assertNotIn("question", unit)
        self.assertNotIn("answer", unit)
        self.assertEqual(len(unit["utterances"]), 1)
        self.assertNotIn("utterance_groups", unit)

    def test_sentence_boundaries_follow_exact_official_source_without_dataset_rules(self):
        official = adapter.document("Cedar", "Cedar was made by Iris. Iris lived in Bay.")
        corpus = {official["id"]: official}
        for sentences in [["Cedar was made by Iris.", "Iris lived in Bay."],
                          ["Cedar was made by Iris. ", "Iris lived in Bay."]]:
            row = {"context": [["Cedar", sentences]], "answer": "must not be read",
                   "supporting_facts": [["unrelated", 99]]}
            self.assertEqual(adapter.candidate_documents(row, corpus), [official])
        # A genuinely different source version is retained, not replaced by
        # another passage merely sharing its title.
        different = {"context": [["Cedar", ["Cedar was made by Rowan."]]]}
        self.assertEqual(adapter.candidate_documents(different, corpus),
                         [adapter.document("Cedar", "Cedar was made by Rowan.")])

    def test_em_f1_and_aliases_are_scoring_only(self):
        self.assertEqual(protocol.answer_scores("The Cedar", ["Cedar"]), {"em": 1.0, "f1": 1.0})
        self.assertEqual(protocol.answer_scores("Cedar Bay", ["Cedar"])["em"], 0)
        self.assertAlmostEqual(protocol.answer_scores("Cedar Bay", ["Cedar"])["f1"], 2/3)
        self.assertEqual(protocol.answer_scores("yes", ["no"])["f1"], 0)

    def test_native_retrieval_compiles_one_plan_and_does_not_score_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = ET.Element("codebook")
            vocab = ET.SubElement(root, "vocab")
            ET.SubElement(vocab, "topic", code="arts", status="canonical")
            timeline = ET.SubElement(root, "timeline")
            session = ET.SubElement(timeline, "session", id="u1", date="2000-01-01", title="Cedar")
            ET.SubElement(session, "line", id="u1L1", who="source").text = "Cedar was created by Iris."
            ET.SubElement(session, "fact", id="u1F1", topics="arts", src="u1L1", kind="relationship").text = "Cedar was created by Iris."
            path = Path(directory) / "book.xml"
            ET.ElementTree(root).write(path)
            store, vocabulary = Store.load([str(path)])
            plan = {"intent": "factual", "queries": [{"select": "facts", "where": {"grep": "Cedar"}, "pipe": []}]}
            with patch.object(compile_plan, "_compile_uncached", return_value=plan) as compiled, patch.object(
                retrieve, "_score_plan", side_effect=AssertionError("candidate scoring forbidden")):
                result = retrieve._run_retrieval(store, vocabulary, profile, "Who created Cedar?",
                    model="gpt-6-luna", budget=40)
            self.assertEqual(compiled.call_count, 1)
            self.assertEqual(result.plan, plan)
            self.assertTrue(result.rows)

    def test_no_extra_model_stages_enabled(self):
        for flag in ["SECOND_PASS", "ROUND2", "PLAN_REPAIR", "REFUSAL_REPLAN", "INFER_PASS",
                     "INFER_V2", "SUPPORT_CHECK", "INSTANCE_ALIGNMENT", "TOPIC_REFINEMENT"]:
            self.assertFalse(getattr(profile, flag))

    def test_reasoning_transport_and_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "calls.jsonl"
            response = {"model": "gpt-6-luna", "choices": [{"finish_reason": "stop", "message": {"content": '{"facts":[]}'}}], "usage": {"total_tokens": 8}}
            with patch.dict(os.environ, {"OPENAI_API_KEY": "fictional-key", "KITE_LLM_RETRIES": "0", "KITE_LLM_LOG": str(log)}), patch.object(
                llm.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as http:
                self.assertEqual(llm.llm_json("fictional source", model="gpt-6-luna"), {"facts": []})
                payload = json.loads(http.call_args.args[0].data)
            self.assertEqual(http.call_count, 1)
            self.assertEqual(payload["reasoning_effort"], "medium")
            self.assertNotIn("temperature", payload)
            self.assertNotIn("response_format", payload)
            self.assertNotIn("fictional-key", log.read_text())
            self.assertEqual(protocol.usage(log)["unknown_usage"], 0)

    def test_native_extraction_none_does_not_disable_compiler_reasoning(self):
        raw = {"facts": [{"content": "Cedar was made by Iris.", "kind": "relationship",
                          "topics": ["arts"], "entities": ["Cedar", "Iris"]}],
               "proposals": [], "entity_types": {}}
        plan = {"queries": [{"select": "facts", "where": {"grep": "Cedar"}, "pipe": []}],
                "intent": "factual"}
        replies = [io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(value)}}]}).encode()) for value in [raw, plan]]
        unit = {"id": "u", "title": "Cedar", "date": "2000-01-01",
                "utterances": [("uL1", "source", "", "Cedar was made by Iris.")]}
        vocabulary = Vocab()
        vocabulary.define_root("arts")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fictional-key", "KITE_LLM_RETRIES": "0"}), patch.object(
                llm.urllib.request, "urlopen", side_effect=replies) as http:
            native_extract.extract_facts(vocabulary, profile, unit, model="gpt-6-luna", log=lambda _: None)
            compile_plan._compile_uncached("Compile fictional Cedar question", "gpt-6-luna")
        requests = [json.loads(c.args[0].data) for c in http.call_args_list]
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["reasoning_effort"], "none")
        self.assertEqual(requests[0]["temperature"], 0.0)
        self.assertEqual(requests[1]["reasoning_effort"], "medium")
        self.assertNotIn("temperature", requests[1])

    def test_extraction_effort_separates_cache_without_invalidating_legacy_keys(self):
        legacy = SimpleNamespace(**{k: getattr(profile, k) for k in dir(profile) if not k.startswith("__")})
        del legacy.EXTRACT_REASONING_EFFORT
        unit = {"id": "u", "title": "Cedar", "date": "2000-01-01",
                "utterances": [("uL1", "source", "", "Cedar was made by Iris.")]}
        raw = {"facts": [{"content": "Cedar was made by Iris.", "kind": "relationship",
                          "topics": ["arts"], "entities": ["Cedar", "Iris"]}],
               "proposals": [], "entity_types": {}}

        def fresh():
            v = Vocab()
            v.define_root("arts")
            return v

        with tempfile.TemporaryDirectory() as directory, patch.object(native_extract, "llm_json", return_value=raw) as model:
            args = dict(model="gpt-6-luna", cache_dir=directory, log=lambda _: None)
            old = native_extract.extract_facts(fresh(), legacy, unit, **args)
            self.assertNotIn("reasoning_effort", model.call_args.kwargs)
            with self.assertRaisesRegex(RuntimeError, "cache-only"):
                native_extract.extract_facts(fresh(), profile, unit, cache_only=True, **args)
            self.assertEqual(model.call_count, 1)
            native_extract.extract_facts(fresh(), profile, unit, **args)
            self.assertEqual(model.call_args.kwargs["reasoning_effort"], "none")
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)
            self.assertEqual(native_extract.extract_facts(fresh(), legacy, unit, cache_only=True, **args), old)
            native_extract.extract_facts(fresh(), profile, unit, cache_only=True, **args)
            self.assertEqual(model.call_count, 2)

    def test_invalid_json_is_not_repaired_or_resampled(self):
        with patch.dict(os.environ, {"KITE_LLM_RETRIES": "0"}), patch.object(
            llm, "_http_llm", return_value='{"answer":"unfinished') as model:
            with self.assertRaises(Exception):
                llm.llm_json("fictional question")
        self.assertEqual(model.call_count, 1)

    def test_transport_failure_is_not_retried(self):
        with patch.dict(os.environ, {"KITE_LLM_RETRIES": "0"}), patch.object(
            llm, "_http_llm", side_effect=OSError("fictional network failure")) as model:
            with self.assertRaises(Exception):
                llm.llm_json("fictional question")
        self.assertEqual(model.call_count, 1)

    def test_failed_extraction_keeps_original_without_reextracting(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            base = Path(directory)
            data, memory, cache = base / "data", base / "book.xml", base / "cache"
            data.mkdir()
            doc = adapter.document("Cedar", "Cedar was made by Iris.")
            protocol.append(data / "corpus.jsonl", doc)
            protocol.dump(data / "manifest.json", {"sha256": {"corpus.jsonl": protocol.sha(data / "corpus.jsonl")}, "scope": "fictional"})
            with patch.object(protocol, "paths", return_value=(data, memory, base / "results", cache)), patch.object(
                build, "extract_facts", side_effect=RuntimeError("fictional failed call")) as extract:
                build.build("musique", "fictional")
                build.build("musique", "fictional", resume=True)
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(ET.parse(memory).find("timeline/session/line").text, "Cedar\nCedar was made by Iris.")
            self.assertFalse(protocol.read(memory.with_suffix(".manifest.json"))["extraction_complete"])
            self.assertEqual(len(protocol.read(memory.with_suffix(".manifest.json"))["failed_extractions"]), 1)


if __name__ == "__main__":
    unittest.main()
