"""Offline unit tests for the corpus-leak gate."""

import json

from benchmarks.tools import leak_check


def _corpus(tmp_path, name, documents):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(documents), encoding="utf-8")
    return path


def test_a_missing_corpus_is_reported_as_skipped_and_never_as_clean(tmp_path, monkeypatch, capsys):
    """Absent data and a clean audit read differently."""
    monkeypatch.setattr(leak_check, "CORPORA", {"absent": tmp_path / "nothing.json"})
    findings, skipped = leak_check.audit()
    assert findings == [] and skipped == ["absent"]

    assert leak_check.main([]) == 0
    printed = capsys.readouterr().out
    assert "SKIPPED absent" in printed
    assert "nothing was audited" in printed


def test_a_required_corpus_that_is_missing_fails_the_gate(tmp_path, monkeypatch):
    """Benchmark and release runs declare the corpus required, and get a failure."""
    monkeypatch.setattr(leak_check, "CORPORA", {"absent": tmp_path / "nothing.json"})
    assert leak_check.main(["--require-corpus"]) == 2


def test_a_term_planted_in_a_prompt_is_reported(tmp_path, monkeypatch):
    """A rare corpus term reaching shipped text. The corpus is invented here."""
    documents = [{"turn": "the zorbicular flange keeps slipping"} for _ in range(6)]
    documents.extend({"turn": "an ordinary unrelated conversation"} for _ in range(94))
    monkeypatch.setattr(leak_check, "CORPORA", {"fake": _corpus(tmp_path, "fake", documents)})
    monkeypatch.setattr(
        leak_check, "_binding_text", lambda benchmark: {"ANSWER_PROMPT": "mention zorbicular"}
    )
    monkeypatch.setattr(leak_check, "_library_text", dict)

    findings, skipped = leak_check.audit()
    assert skipped == []
    assert [item[2] for item in findings] == ["zorbicular"]
    assert leak_check.main([]) == 1


def test_a_term_planted_outside_a_prompt_is_reported_too(tmp_path, monkeypatch):
    """The scan covers every string a binding exports, not its prompts alone."""
    documents = [{"turn": "the zorbicular flange keeps slipping"} for _ in range(6)]
    documents.extend({"turn": "an ordinary unrelated conversation"} for _ in range(94))
    monkeypatch.setattr(leak_check, "CORPORA", {"fake": _corpus(tmp_path, "fake", documents)})
    monkeypatch.setattr(
        leak_check,
        "_binding_text",
        lambda benchmark: {"ANSWERABLE_BY_CONSTRUCTION": 'return "zorbicular" in question'},
    )
    monkeypatch.setattr(leak_check, "_library_text", dict)

    findings, _ = leak_check.audit()
    assert [(item[1], item[2]) for item in findings] == [
        ("ANSWERABLE_BY_CONSTRUCTION", "zorbicular")
    ]


def test_a_term_spread_across_the_corpus_is_task_language_and_passes(tmp_path, monkeypatch):
    documents = [{"turn": "the zorbicular flange keeps slipping"} for _ in range(80)]
    documents.extend({"turn": "an ordinary unrelated conversation"} for _ in range(20))
    monkeypatch.setattr(leak_check, "CORPORA", {"fake": _corpus(tmp_path, "fake", documents)})
    monkeypatch.setattr(
        leak_check, "_binding_text", lambda benchmark: {"ANSWER_PROMPT": "mention zorbicular"}
    )
    monkeypatch.setattr(leak_check, "_library_text", dict)

    findings, _ = leak_check.audit()
    assert findings == []


def test_the_real_bindings_and_library_are_in_scope():
    """The scan reaches the bindings and the library modules it names."""
    scanned = leak_check._binding_text("locomo")
    assert {"ANSWER_PROMPT", "COMPILE_PROMPT", "POSTPROC_RULES"} <= set(scanned)
    assert "ANSWERABLE_BY_CONSTRUCTION" in scanned
    library = leak_check._library_text()
    assert "Answer using only the supplied evidence" in library["memoket_kite.prompts.answer"]
    # A prompt assembled inside the function that sends it is in scope.
    assert "INSTANCE INDEX" in library["memoket_kite.pipeline.answer"]
    # Documentation and identifiers address a reader of the code, not a model.
    assert not any("isinstance" in text for text in library.values())
    assert not any("wrapped so the ledger always closes" in t for t in library.values())


def test_every_module_that_writes_into_a_prompt_is_in_scope():
    """A module that renders evidence is a module that can leak a corpus term."""
    assert "memoket_kite.pipeline.render" in leak_check.LIBRARY_MODULES
    assert "memoket_kite.pipeline.answer" in leak_check.LIBRARY_MODULES


def test_the_gate_reads_every_module_a_run_manifest_fingerprints():
    """A template worth naming in a manifest is worth auditing for leaks."""
    from benchmarks.common import manifest

    assert set(manifest.HASHED_LIBRARY_PROMPTS) <= set(leak_check.LIBRARY_MODULES)
