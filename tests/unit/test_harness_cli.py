"""The evaluator entry points, exercised without spending a model call.

Covering the helpers a harness calls does not cover the harness: argument
wiring, run-identity checks and provenance all live in `main()`, and a name
that one parser does not define fails there and nowhere else. These tests walk
`main()` end to end with the per-question work stubbed out, so the startup path
is exercised at unit-test cost.
"""

import contextlib
import hashlib
import json
import socket

import pytest

from benchmarks.common import manifest


def test_longmemeval_main_records_provenance_before_answering(corpus, tmp_path, monkeypatch):
    from benchmarks.longmemeval import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(evaluate, "select_indices", lambda data, count: [])
    result_dir = tmp_path / "longmemeval-unit"
    result_dir.mkdir()
    (result_dir / "failed.txt").write_text("stale failure\n")
    evaluate.main(["--n", "1", "--tag", "unit", "--workers", "1"])

    recorded = json.loads((result_dir / "manifest.json").read_text())
    assert recorded["binding"] == "benchmarks.longmemeval.profile"
    assert recorded["config"]["TOKEN_CAP"]
    # Declared, and recorded: a score states which rewrites it ran under.
    assert recorded["config"]["POSTPROC_RULES"] == "premise,hedge"
    assert recorded["answerable_predicate"]
    assert recorded["prompt_sha"]["ANSWER_PROMPT"]
    assert recorded["answer_model"]
    assert not (result_dir / "failed.txt").exists()


def test_locomo_main_accepts_its_own_arguments(corpus, tmp_path, monkeypatch):
    from benchmarks.locomo import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(evaluate, "evaluate_sample", lambda *a, **k: [])
    result_dir = tmp_path / "locomo-unit"
    result_dir.mkdir()
    (result_dir / "failed.txt").write_text("stale failure\n")
    evaluate.main(["--tag", "unit", "--workers", "1", "--samples", "0"])

    recorded = json.loads((result_dir / "manifest.json").read_text())
    assert recorded["binding"] == "benchmarks.locomo.profile"
    assert recorded["samples"] == [0]
    # the value is a configuration choice; what matters is that it is recorded
    assert isinstance(recorded["config"]["DUAL_DATE"], bool)
    assert recorded["config"]["POSTPROC_RULES"] == ""
    assert not (result_dir / "failed.txt").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--workers", "0", "--samples", "0"],
        ["--workers", "1", "--samples", "-1"],
        ["--workers", "1", "--samples", "0", "0"],
        ["--workers", "1", "--samples", "999"],
        ["--workers", "1", "--samples", "0", "--tag", "../../../outside"],
    ],
)
def test_locomo_rejects_invalid_run_identity_before_creating_it(
    corpus, tmp_path, monkeypatch, extra
):
    from benchmarks.locomo import evaluate

    run_root = tmp_path / "invalid-locomo-runs"
    monkeypatch.setattr(evaluate, "RESULTS_ROOT", run_root)
    with pytest.raises(SystemExit):
        evaluate.main(extra)
    assert not run_root.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--workers", "0", "--n", "1"],
        ["--workers", "1", "--n", "-1"],
        ["--workers", "1", "--n", "1", "--tag", "../../../outside"],
    ],
)
def test_longmemeval_rejects_invalid_run_identity_before_creating_it(
    corpus, tmp_path, monkeypatch, extra
):
    from benchmarks.longmemeval import evaluate

    run_root = tmp_path / "invalid-longmemeval-runs"
    monkeypatch.setattr(evaluate, "RESULTS_ROOT", run_root)
    with pytest.raises(SystemExit):
        evaluate.main(extra)
    assert not run_root.exists()


def test_longmemeval_build_clears_a_stale_failure_marker(tmp_path, monkeypatch):
    from benchmarks.longmemeval import build

    dataset = tmp_path / "dataset.json"
    dataset.write_text("[]", encoding="utf-8")
    output = tmp_path / "codebooks"
    output.mkdir()
    (output / "failed.txt").write_text("stale failure\n", encoding="utf-8")
    monkeypatch.setattr(build, "DATASET", dataset)
    monkeypatch.setattr(build, "OUTPUT_DIR", output)

    assert build.main(["--n", "0", "--workers", "1"]) == 0
    assert not (output / "failed.txt").exists()


def test_resuming_into_a_differently_configured_directory_is_refused(tmp_path):
    from benchmarks.longmemeval import profile

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    manifest.write(result_dir, profile, model="gpt-4.1-mini")
    stored = json.loads((result_dir / "manifest.json").read_text())
    stored["config"]["TOKEN_CAP"] = 1
    (result_dir / "manifest.json").write_text(json.dumps(stored))

    with pytest.raises(SystemExit, match="different"):
        manifest.write(result_dir, profile, resuming=True, model="gpt-4.1-mini")
    # …and an unchanged configuration resumes without complaint
    manifest.write(result_dir, profile, model="gpt-4.1-mini")
    manifest.write(result_dir, profile, resuming=True, model="gpt-4.1-mini")


def test_resume_refuses_when_the_model_or_environment_changed(tmp_path, monkeypatch):
    """A results file belongs to one whole system, not just to one commit.

    The model, the answer model, the question count and any environment
    override each change what gets written, so a fingerprint over the code
    alone lets two different systems append to the same file.
    """
    from benchmarks.longmemeval import profile

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    manifest.write(result_dir, profile, model="gpt-4.1-mini", answer_model="gpt-4.1-mini", n=500)

    for changed in ({"model": "gpt-4.1"}, {"answer_model": "gpt-4.1"}, {"n": 30}):
        kwargs = {"model": "gpt-4.1-mini", "answer_model": "gpt-4.1-mini", "n": 500, **changed}
        with pytest.raises(SystemExit, match="changed since"):
            manifest.write(result_dir, profile, resuming=True, **kwargs)

    monkeypatch.setenv("KITE_POSTPROC", "hedge")
    with pytest.raises(SystemExit, match="env_overrides"):
        manifest.write(
            result_dir,
            profile,
            resuming=True,
            model="gpt-4.1-mini",
            answer_model="gpt-4.1-mini",
            n=500,
        )
    # the refusal must leave the original provenance intact
    assert json.loads((result_dir / "manifest.json").read_text())["model"] == "gpt-4.1-mini"


def test_resume_refuses_results_that_have_no_manifest(tmp_path):
    from benchmarks.longmemeval import profile

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    with pytest.raises(SystemExit, match="no manifest"):
        manifest.write(result_dir, profile, resuming=True, model="gpt-4.1-mini")


def test_locomo_refuses_a_colliding_tag_without_touching_provenance(corpus, tmp_path, monkeypatch):
    from benchmarks.locomo import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(evaluate, "evaluate_sample", lambda *a, **k: [])
    evaluate.main(["--tag", "unit", "--workers", "1", "--samples", "0"])

    result_dir = tmp_path / "locomo-unit"
    sample_id = evaluate._load_dataset()[0]["sample_id"]
    (result_dir / f"results_{sample_id}.jsonl").write_text("")
    (result_dir / "manifest.json").write_text('{"sentinel": "original"}')

    with pytest.raises(SystemExit, match="output exists"):
        evaluate.main(["--tag", "unit", "--workers", "1", "--samples", "0"])
    assert json.loads((result_dir / "manifest.json").read_text()) == {"sentinel": "original"}


def test_an_unknown_postproc_rule_fails_before_any_question_is_answered(tmp_path, monkeypatch):
    from benchmarks.longmemeval import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    monkeypatch.setenv("KITE_POSTPROC", "premise,hedeg")

    def explode(*args, **kwargs):  # answering must never be reached
        raise AssertionError("a question was answered despite an unknown rule")

    monkeypatch.setattr(evaluate, "select_indices", explode)
    with pytest.raises(ValueError, match="hedeg"):
        evaluate.main(["--n", "1", "--tag", "unit-fail", "--workers", "1"])
    assert not (tmp_path / "longmemeval-unit-fail").exists()


def test_a_judge_verdict_is_not_reused_by_a_different_judge():
    from benchmarks.common.judging import judge_cache_key

    assert judge_cache_key("prompt", "gpt-4.1-mini") != judge_cache_key("prompt", "gpt-4.1")
    assert judge_cache_key("rubric A", "m") != judge_cache_key("rubric B", "m")
    assert judge_cache_key("prompt", "m") == judge_cache_key("prompt", "m")


def test_the_question_sampler_returns_the_size_it_was_asked_for():
    from benchmarks.longmemeval.build import select_indices

    data = [
        {"question_id": f"q{i}{'_abs' if i % 9 == 0 else ''}", "question_type": f"t{i % 6}"}
        for i in range(500)
    ]
    for count in (1, 50, 100, 470):
        chosen = select_indices(data, count)
        assert len(chosen) == len(set(chosen)) == count
    assert len(select_indices(data, 0)) == 500  # documented all-questions sentinel
    assert len(select_indices(data, 500)) == 500
    with pytest.raises(ValueError):
        select_indices(data, -1)


def test_a_truncated_tail_is_repaired_so_the_next_append_survives(tmp_path):
    """A truncated tail must be repaired on disk, not merely skipped in memory.

    The evaluator appends after reading, so a partial line left in the file is
    concatenated with the next record written onto it. Both are then lost: the
    resume reports success while completed, paid-for work disappears. The
    original bytes are moved aside rather than deleted, so nothing that was
    written is destroyed by the repair.
    """
    from benchmarks.common.resume import completed_rows

    path = tmp_path / "results.jsonl"
    path.write_text('{"question_id": "a"}\n{"question_i')
    assert [row["question_id"] for row in completed_rows(path)] == ["a"]
    assert (tmp_path / "results.jsonl.partial").exists()  # original preserved

    with path.open("a", encoding="utf-8") as stream:  # what the evaluator does
        stream.write(json.dumps({"question_id": "b"}) + "\n")
        stream.write(json.dumps({"question_id": "c"}) + "\n")
    assert [row["question_id"] for row in completed_rows(path)] == ["a", "b", "c"]


def test_damage_before_the_final_line_is_never_silently_repaired(tmp_path):
    from benchmarks.common.resume import completed_rows

    path = tmp_path / "results.jsonl"
    path.write_text('{"question_id": "a"}\n{"broken\n{"question_id": "c"}\n')
    with pytest.raises(RuntimeError, match="corrupt at line 2"):
        completed_rows(path)
    assert "broken" in path.read_text()  # left for inspection, not rewritten


def test_locomo_refuses_a_second_sample_under_a_tag_it_already_used(corpus, tmp_path, monkeypatch):
    """Two sample sets under one tag get scored together by the glob."""
    from benchmarks.locomo import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(evaluate, "evaluate_sample", lambda *a, **k: [])
    fixture = evaluate._load_dataset()[0]
    samples = [fixture, {**fixture, "sample_id": "synthetic-second"}]
    monkeypatch.setattr(evaluate, "_load_dataset", lambda: samples)
    evaluate.main(["--tag", "unit", "--workers", "1", "--samples", "0"])
    result_dir = tmp_path / "locomo-unit"
    first = samples[0]["sample_id"]
    (result_dir / f"results_{first}.jsonl").write_text("{}\n")

    with pytest.raises(SystemExit, match="output exists"):
        evaluate.main(["--tag", "unit", "--workers", "1", "--samples", "1"])
    assert json.loads((result_dir / "manifest.json").read_text())["samples"] == [0]


def test_resume_refuses_when_the_provider_endpoint_changed(tmp_path, monkeypatch):
    """The same model name on two endpoints is two systems."""
    from benchmarks.longmemeval import profile

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider-a.example/v1")
    manifest.write(result_dir, profile, model="gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider-b.example/v1")
    with pytest.raises(SystemExit, match="provider"):
        manifest.write(result_dir, profile, resuming=True, model="gpt-4.1-mini")


def test_resume_refuses_when_the_corpus_changed(tmp_path, monkeypatch):
    """Rebuilding a codebook changes the answers without changing the source."""
    from benchmarks.longmemeval import profile

    corpus = tmp_path / "codebooks"
    corpus.mkdir()
    (corpus / "a.xml").write_text("<Facts/>")
    result_dir = tmp_path / "run"
    result_dir.mkdir()
    manifest.write(result_dir, profile, corpus_sha=manifest.corpus_digest([corpus]))

    (corpus / "a.xml").write_text("<Facts><Fact/></Facts>")
    with pytest.raises(SystemExit, match="corpus_sha"):
        manifest.write(
            result_dir, profile, resuming=True, corpus_sha=manifest.corpus_digest([corpus])
        )


def test_a_tail_truncated_mid_character_is_still_recoverable(tmp_path):
    """Both harnesses write with ensure_ascii=False, so a kill lands mid-byte.

    Recovery therefore has to work on bytes: decoding the whole file first
    raises on the split character before any recovery logic runs, which makes
    a long run with non-ASCII answers unresumable.
    """
    from benchmarks.common.resume import completed_rows

    path = tmp_path / "results.jsonl"
    path.write_bytes(
        json.dumps({"question_id": "a", "answer": "中文答案"}, ensure_ascii=False).encode()
        + b"\n"
        + b'{"answer":"\xe4\xb8'  # killed between the bytes of one character
    )
    assert [row["question_id"] for row in completed_rows(path)] == ["a"]
    with path.open("a", encoding="utf-8") as stream:
        for question_id in ("b", "c"):
            stream.write(json.dumps({"question_id": question_id}, ensure_ascii=False) + "\n")
    assert [row["question_id"] for row in completed_rows(path)] == ["a", "b", "c"]


def test_a_fully_written_but_invalid_line_is_never_discarded(tmp_path):
    from benchmarks.common.resume import completed_rows

    path = tmp_path / "results.jsonl"
    path.write_bytes(b'{"question_id": "a"}\n{not json}\n')
    with pytest.raises(RuntimeError, match="corrupt"):
        completed_rows(path)


def test_recovery_never_overwrites_earlier_evidence(tmp_path):
    from benchmarks.common.resume import completed_rows

    path = tmp_path / "results.jsonl"
    for _ in range(2):
        path.write_bytes(b'{"question_id": "a"}\n{"trunc')
        completed_rows(path)
    saved = sorted(p.name for p in tmp_path.iterdir() if ".partial" in p.name)
    assert saved == ["results.jsonl.partial", "results.jsonl.partial1"]


def test_scoring_refuses_result_files_the_manifest_does_not_declare(tmp_path):
    from benchmarks.common import ownership

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results_owned.jsonl").write_text("{}\n")
    assert ownership.check(result_dir, ["owned"]) == [result_dir / "results_owned.jsonl"]

    (result_dir / "results_foreign.jsonl").write_text("{}\n")
    with pytest.raises(SystemExit, match="does not declare"):
        ownership.check(result_dir, ["owned"])


def test_a_moved_codebook_changes_the_corpus_identity(tmp_path):
    """Corpus identity is the set of paths, not the set of file names.

    Hashing basenames alone makes `q.xml` and `.trash/q.xml` indistinguishable,
    so retiring a codebook into a subdirectory would leave the digest unmoved.
    Debris from an interrupted build is excluded, so it cannot move the digest
    either.
    """
    corpus = tmp_path / "codebooks"
    (corpus / ".trash").mkdir(parents=True)
    (corpus / "q.xml").write_text("<Facts/>")
    before = manifest.corpus_digest([corpus])

    (corpus / "q.xml").rename(corpus / ".trash" / "q.xml")
    assert manifest.corpus_digest([corpus]) != before

    (corpus / ".trash" / "q.xml").rename(corpus / "q.xml")
    (corpus / ".partial.xml").write_text("<Facts/>")  # interrupted-build debris
    assert manifest.corpus_digest([corpus]) == before


def test_a_score_can_always_name_the_judge_that_produced_it(tmp_path):
    from benchmarks.common import judging

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "judged.jsonl").write_text("{}\n")
    (result_dir / "manifest.json").write_text("{}\n")
    (result_dir / "score.json").write_text("{}\n")
    args = {
        "source": result_dir / "results.jsonl",
        "judged": result_dir / "judged.jsonl",
        "protocol": "rubric",
        "run": result_dir / "manifest.json",
        "score": result_dir / "score.json",
    }
    judging.write_manifest(result_dir, model="gpt-4.1-mini", **args)
    assert judging.check_offline(result_dir, "gpt-4.1-mini", **args)[:2] == ("gpt-4.1-mini", False)
    with pytest.raises(SystemExit, match="was judged by"):
        judging.check_offline(result_dir, "some-other-judge", **args)
    with pytest.raises(SystemExit, match="would overwrite"):
        judging.refuse_overwrite(result_dir, "some-other-judge")

    # a rewritten verdict file, a swapped answer file or a reworded rubric must
    # each stop the score from being reported under the old manifest
    (result_dir / "judged.jsonl").write_text('{"tampered": true}\n')
    with pytest.raises(SystemExit, match="judged_sha"):
        judging.check_offline(result_dir, "gpt-4.1-mini", **args)
    (result_dir / "judged.jsonl").write_text("{}\n")
    (result_dir / "results.jsonl").write_text('{"swapped": true}\n')
    with pytest.raises(SystemExit, match="results_sha"):
        judging.check_offline(result_dir, "gpt-4.1-mini", **args)
    (result_dir / "results.jsonl").write_text("{}\n")
    with pytest.raises(SystemExit, match="protocol_sha"):
        judging.check_offline(result_dir, "gpt-4.1-mini", **{**args, "protocol": "reworded"})

    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(SystemExit, match="no judge manifest"):
        judging.check_offline(bare, "gpt-4.1-mini", **args)


def test_a_current_schema_manifest_cannot_downgrade_its_own_verification(tmp_path):
    """Deleting one field must not select the weaker path.

    The legacy branch skips the source and provider checks, so if a missing
    algorithm name selected it, deleting a key would be enough to downgrade
    verification. A manifest that claims the current schema is held to it, and
    a schema this code does not know is refused rather than guessed at.
    """
    from benchmarks.common import judging

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "judged.jsonl").write_text("{}\n")
    (result_dir / "manifest.json").write_text("{}\n")
    (result_dir / "score.json").write_text("{}\n")
    args = {
        "source": result_dir / "results.jsonl",
        "judged": result_dir / "judged.jsonl",
        "protocol": "rubric",
        "run": result_dir / "manifest.json",
        "score": result_dir / "score.json",
    }
    judging.write_manifest(result_dir, model="m", **args)

    stored = json.loads((result_dir / "judge_manifest.json").read_text())
    del stored["source_digest_algorithm"]
    (result_dir / "judge_manifest.json").write_text(json.dumps(stored))
    with pytest.raises(SystemExit, match="refusing to verify"):
        judging.check_offline(result_dir, "m", **args)

    stored["schema"] = 99
    (result_dir / "judge_manifest.json").write_text(json.dumps(stored))
    with pytest.raises(SystemExit, match="does not know how to verify"):
        judging.check_offline(result_dir, "m", **args)


def test_a_legacy_manifest_is_scored_only_on_request_and_still_checks_the_judge(tmp_path):
    from benchmarks.common import judging

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "judged.jsonl").write_text("{}\n")
    (result_dir / "manifest.json").write_text("{}\n")
    (result_dir / "score.json").write_text("{}\n")
    args = {
        "source": result_dir / "results.jsonl",
        "judged": result_dir / "judged.jsonl",
        "protocol": "rubric",
        "run": result_dir / "manifest.json",
        "score": result_dir / "score.json",
    }
    judging.write_manifest(result_dir, model="m", **args)
    stored = json.loads((result_dir / "judge_manifest.json").read_text())
    stored["schema"] = judging.LEGACY_SCHEMA
    stored.pop("source_digest_algorithm")
    (result_dir / "judge_manifest.json").write_text(json.dumps(stored))

    with pytest.raises(SystemExit, match="--allow-legacy"):
        judging.check_offline(result_dir, "m", **args)
    assert judging.check_offline(result_dir, "m", allow_legacy=True, **args)[:2] == ("m", True)

    # the rubric is still verified even on the weak path
    with pytest.raises(SystemExit, match="protocol_sha"):
        judging.check_offline(
            result_dir, "m", allow_legacy=True, **{**args, "protocol": "reworded"}
        )


def test_a_snapshot_read_once_cannot_drift_between_the_guard_and_the_manifest(tmp_path):
    """The judged bytes and the sealed bytes must come from a single read.

    The evaluator can append while judging runs. If the guard compares one read
    against another and the manifest then takes a third, a file that grows
    between the last two is published as the source of verdicts that never saw
    it. Digesting the snapshot binds what was actually judged.
    """
    from benchmarks.common import judging

    path = tmp_path / "results.jsonl"
    path.write_text('{"question_id": "a"}\n')
    snapshot = judging.read_snapshot([path])
    before = judging.snapshot_digest(snapshot)

    path.write_text('{"question_id": "a"}\n{"question_id": "b"}\n')  # evaluator appends
    assert judging.snapshot_digest(snapshot) == before  # what was judged is what is published
    assert judging.source_digest([path]) != before  # and the file on disk has moved


def test_the_source_digest_cannot_be_forged_by_a_filename():
    """Colons and newlines are legal in POSIX names, so they cannot delimit."""
    import tempfile
    from pathlib import Path

    from benchmarks.common import judging

    one = Path(tempfile.mkdtemp())
    (one / "a:x").write_bytes(b"1")
    (one / "b").write_bytes(b"2")
    two = Path(tempfile.mkdtemp())
    (two / "a").write_bytes(b"1")
    assert judging.source_digest(sorted(one.iterdir())) != judging.source_digest(
        sorted(two.iterdir())
    )


def test_a_partial_longmemeval_run_is_refused_before_any_judging(tmp_path, monkeypatch):
    """A stable snapshot proves nothing changed, not that the run finished."""
    from benchmarks.common import manifest
    from benchmarks.longmemeval import score

    expected = [f"q{i}" for i in range(500)]
    rows = [{"question_id": qid} for qid in expected]
    data = [
        {
            "question_id": qid,
            "question": "q",
            "answer": "a",
            "question_type": "t",
            "answer_session_ids": [],
        }
        for qid in expected
    ]
    result_dir = tmp_path / "longmemeval-partial"
    result_dir.mkdir()
    (result_dir / "manifest.json").write_text(
        json.dumps(
            {
                "n": 0,
                "effective_n": 500,
                "selected_sha": manifest.question_digest(expected),
                "corpus_sha": "c" * 16,
            }
        )
    )
    monkeypatch.setattr(manifest, "corpus_digest", lambda paths: "c" * 16)
    # `--n 0` means "run everything", so the request is not a count: completeness
    # is measured against `effective_n`, the size the selection resolved to.
    with pytest.raises(SystemExit, match="answered 2 of the 500"):
        score._canonical(rows[:2], result_dir, data)
    # the right COUNT of the wrong questions is not a complete run either
    other = [{"question_id": f"other{i}"} for i in range(500)]
    with pytest.raises(SystemExit, match="not the ones it"):
        score._canonical(other, result_dir, data)
    score._canonical(rows, result_dir, data)  # complete: no complaint

    stale = tmp_path / "longmemeval-stale"
    stale.mkdir()
    (stale / "manifest.json").write_text(json.dumps({"n": 500}))
    with pytest.raises(SystemExit, match="predates effective_n"):
        score._canonical(rows, stale, data)

    bare = tmp_path / "longmemeval-bare"
    bare.mkdir()
    with pytest.raises(SystemExit, match="no run manifest"):
        score._canonical(rows, bare, data)


def _stub_llm(monkeypatch, module, verdict):
    """Answer every judging call without touching a provider."""
    monkeypatch.setattr(module, "llm", lambda *a, **k: verdict, raising=False)
    monkeypatch.setattr(module, "llm_json", lambda *a, **k: {"label": "CORRECT"}, raising=False)


def test_longmemeval_online_scoring_runs_to_completion(corpus, tmp_path, monkeypatch):
    """The online path must reach its return, not just its last write.

    Scoring writes the judged file, the score and the seal before it returns,
    so a failure after those writes leaves a judge manifest that makes the
    retry impossible: the artifacts exist and the overwrite guard refuses to
    replace them. Only running `main()` to completion covers that tail.
    """
    from benchmarks.longmemeval import evaluate, score

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(evaluate, "select_indices", lambda data, count: [0])
    monkeypatch.setattr(evaluate, "answer_question", lambda *a, **k: None, raising=False)
    result_dir = tmp_path / "longmemeval-online"
    result_dir.mkdir()
    dataset = json.loads((evaluate.DATASET).read_text())[0]
    (result_dir / "results.jsonl").write_text(
        json.dumps(
            {
                "question_id": dataset["question_id"],
                "question": dataset["question"],
                "question_type": dataset["question_type"],
                "gold": str(dataset.get("answer", "")),
                "answer": "an answer",
                "telemetry": {},
            }
        )
        + "\n"
    )
    (result_dir / "manifest.json").write_text(
        json.dumps(
            {
                "effective_n": 1,
                "selected_sha": manifest.question_digest([dataset["question_id"]]),
                # the real digest, so the happy path of the corpus check runs
                "corpus_sha": manifest.corpus_digest(
                    [score.DATASET, score.CODEBOOKS_ROOT / "longmemeval"]
                ),
            }
        )
    )
    monkeypatch.setattr(score, "RESULTS_ROOT", tmp_path)
    _stub_llm(monkeypatch, score, "yes")

    assert score.main(["--tag", "online", "--workers", "1"]) == 0
    assert (result_dir / "score.json").exists()
    assert (result_dir / "judge_manifest.json").exists()


def test_identity_fields_are_mandatory_not_merely_checked_when_present(tmp_path):
    """Optional verification is no verification.

    A digest compared only when present is a digest an editor can remove: the
    check then passes and the field protects nothing. Every bound digest is
    therefore mandatory, and a manifest missing one is refused rather than
    verified on the fields that remain.
    """
    from benchmarks.common import judging
    from benchmarks.longmemeval import score

    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"effective_n": 1}))
    with pytest.raises(SystemExit, match="records no selected_sha"):
        score._canonical([{"question_id": "q1"}], run, [])

    judged = tmp_path / "judged"
    judged.mkdir()
    (judged / "results.jsonl").write_text("{}\n")
    (judged / "judged.jsonl").write_text("{}\n")
    (judged / "manifest.json").write_text("{}\n")
    (judged / "score.json").write_text("{}\n")
    args = {
        "source": judged / "results.jsonl",
        "judged": judged / "judged.jsonl",
        "protocol": "rubric",
        "run": judged / "manifest.json",
        "score": judged / "score.json",
    }
    judging.write_manifest(judged, model="m", **args)
    # Every bound digest, not just the one that failed first: deleting any of
    # them must be refused rather than silently skipped.
    for field in ("run_manifest_sha", "score_sha"):
        stored = json.loads((judged / "judge_manifest.json").read_text())
        del stored[field]
        (judged / "judge_manifest.json").write_text(json.dumps(stored))
        with pytest.raises(SystemExit, match=f"records no {field}"):
            judging.check_offline(judged, "m", **args)
        judging.write_manifest(judged, model="m", **args)
    stored = json.loads((judged / "judge_manifest.json").read_text())
    del stored["run_manifest_sha"]
    (judged / "judge_manifest.json").write_text(json.dumps(stored))
    with pytest.raises(SystemExit, match="records no run_manifest_sha"):
        judging.check_offline(judged, "m", **args)


def test_a_forged_gold_cannot_reach_the_judge(tmp_path):
    """Identity comes from the row; everything scored comes from the corpus.

    The row is written by the system under test, so its question and its gold
    are not evidence about the dataset. Comparing them would only detect a
    disagreement, which any tampering can avoid by being self-consistent;
    overwriting them from the corpus makes the row's copy irrelevant. The
    system's own answer is the one field left alone — it is what is scored.
    """
    from benchmarks.common import canonical

    expected = {"q1": {"question": "real question", "answer": "real gold", "kind": "t"}}
    rows = [{"question_id": "q1", "question": "forged", "gold": "TAMPERED", "answer": "a"}]
    reconciled = canonical.reconcile(
        rows,
        expected,
        key_of=lambda row: row["question_id"],
        fields=(("question", "question"), ("gold", "answer")),
    )
    assert reconciled[0]["gold"] == "real gold"  # overwritten, not trusted
    assert reconciled[0]["question"] == "real question"
    assert reconciled[0]["answer"] == "a"  # the system's answer is left alone


def test_duplicate_and_missing_records_are_both_refused():
    """A set of keys hides both at once.

    One question answered twice and one not answered at all give a row set that
    equals the dataset's key set exactly, so reconciliation has to count the
    rows rather than compare sets.
    """
    from benchmarks.common import canonical

    expected = {"q1": {"question": "a"}, "q2": {"question": "b"}}
    key = {"key_of": lambda row: row["question_id"], "fields": (("question", "question"),)}

    with pytest.raises(SystemExit, match="appears twice"):
        canonical.reconcile(
            [{"question_id": "q1"}, {"question_id": "q2"}, {"question_id": "q1"}],
            expected,
            **key,
        )
    with pytest.raises(SystemExit, match="1 unanswered"):
        canonical.reconcile([{"question_id": "q1"}], expected, **key)
    with pytest.raises(SystemExit, match="1 not in the dataset"):
        canonical.reconcile(
            [{"question_id": "q1"}, {"question_id": "q2"}, {"question_id": "q9"}],
            expected,
            **key,
        )


def test_locomo_online_scoring_runs_to_completion(corpus, tmp_path, monkeypatch):
    """Scoring LoCoMo online must run to completion and write both artifacts.

    Its denominator is the set of per-sample result files rather than a single
    one, so it reaches the seal by a different route than the LongMemEval
    scorer and needs its own end-to-end pass.
    """
    from benchmarks.locomo import evaluate, score

    data = evaluate._load_dataset()
    sample = data[0]
    scorable = [
        (index, item)
        for index, item in enumerate(sample["qa"])
        if item.get("category") in score.CATEGORIES
    ]
    result_dir = tmp_path / "locomo-online"
    result_dir.mkdir()
    (result_dir / f"results_{sample['sample_id']}.jsonl").write_text(
        "".join(
            json.dumps({"qa_idx": index, "answer": "x", "telemetry": {}}) + "\n"
            for index, _ in scorable
        )
    )
    (result_dir / "manifest.json").write_text(
        json.dumps(
            {
                "samples": [0],
                "corpus_sha": manifest.corpus_digest(
                    [score.DATASET, score.CODEBOOKS_ROOT / "locomo"]
                ),
            }
        )
    )
    monkeypatch.setattr(score, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(score, "llm_json", lambda *a, **k: {"label": "WRONG"}, raising=False)

    assert score.main(["--tag", "online", "--workers", "1"]) == 0
    assert json.loads((result_dir / "score.json").read_text())["overall"]["n"] == len(scorable)


def test_an_interrupted_publish_leaves_no_manifest_to_block_the_retry(tmp_path):
    """The manifest is the seal, so it must be written last.

    A manifest written before the score claims a completion that has not
    happened: a crash in between leaves a complete-looking artifact over a
    missing score, and the overwrite guard then refuses the rerun that would
    repair it. Writing the seal last makes an interrupted publish look exactly
    like a publish that never started.
    """
    from benchmarks.common import publish

    result_dir = tmp_path / "run"
    result_dir.mkdir()

    def explode(_sealed):
        raise RuntimeError("killed while sealing")

    with pytest.raises(RuntimeError):
        publish.publish(result_dir, judged="{}\n", score={"overall": {}}, manifest=explode)
    assert (result_dir / "judged.jsonl").exists()
    assert not (result_dir / "judge_manifest.json").exists()  # nothing claims completion
    assert not list(result_dir.glob("*.publishing"))  # no temporary left behind


def test_a_rewritten_headline_is_caught(tmp_path):
    """Readers consume score.json directly, so it belongs in the trusted set."""
    from benchmarks.common import judging

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    for name in ("results.jsonl", "judged.jsonl", "manifest.json"):
        (result_dir / name).write_text("{}\n")
    (result_dir / "score.json").write_text(json.dumps({"overall": {"accuracy": 0.84}}))
    args = {
        "source": result_dir / "results.jsonl",
        "judged": result_dir / "judged.jsonl",
        "protocol": "rubric",
        "run": result_dir / "manifest.json",
        "score": result_dir / "score.json",
    }
    judging.write_manifest(result_dir, model="m", **args)
    assert judging.check_offline(result_dir, "m", **args)[:2] == ("m", False)

    (result_dir / "score.json").write_text(json.dumps({"overall": {"accuracy": 1.0}}))
    with pytest.raises(SystemExit, match="score_sha"):
        judging.check_offline(result_dir, "m", **args)


def test_a_score_is_not_sealed_over_inputs_that_moved_while_it_was_judged(tmp_path):
    """Judging takes minutes; the seal must describe a state that existed.

    The manifest binds digests taken at seal time. If the run manifest or the
    answers were replaced after they were read, sealing them writes a
    consistent-looking artifact for a combination that never ran.
    """
    from benchmarks.common import publish

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "manifest.json").write_text('{"commit": "aaa"}\n')
    (result_dir / "results.jsonl").write_text('{"question_id": "q1"}\n')
    observed = publish.observe([result_dir / "manifest.json", result_dir / "results.jsonl"])
    (result_dir / "manifest.json").write_text('{"commit": "bbb"}\n')

    sealed = []
    with pytest.raises(SystemExit, match="changed while it was being judged"):
        publish.publish(
            result_dir,
            observed=observed,
            judged="{}\n",
            score={"overall": {"accuracy": 1.0}},
            manifest=lambda _sealed: sealed.append(True),
        )
    # refused BEFORE writing: no half-published artifact is left behind
    assert not sealed
    assert not (result_dir / "judged.jsonl").exists()
    assert not (result_dir / "score.json").exists()


def test_a_run_cannot_be_judged_against_a_corpus_that_has_since_changed(tmp_path, monkeypatch):
    """Rebuilding a codebook changes the answers without touching the source."""
    from benchmarks.longmemeval import score

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    records = [{"question_id": "q1", "answer": "x"}]
    (result_dir / "manifest.json").write_text(
        json.dumps(
            {
                "effective_n": 1,
                "selected_sha": manifest.question_digest(["q1"]),
                "corpus_sha": "0" * 16,
            }
        )
    )
    monkeypatch.setattr(manifest, "corpus_digest", lambda paths: "1" * 16)
    with pytest.raises(SystemExit, match="corpus on disk is now"):
        score._canonical(records, result_dir, [{"question_id": "q1"}])

    # ...and a run that never declared one is refused rather than waved
    # through: an absent digest is not a matching digest.
    (result_dir / "manifest.json").write_text(
        json.dumps({"effective_n": 1, "selected_sha": manifest.question_digest(["q1"])})
    )
    with pytest.raises(SystemExit, match="declares no corpus_sha"):
        score._canonical(records, result_dir, [{"question_id": "q1"}])


def test_evidence_recall_is_scored_against_the_dataset_not_the_row(corpus, tmp_path):
    """The gold sessions decide the denominator, so the row cannot supply them.

    A row that names the sessions it happened to retrieve reads as perfect
    evidence recall.
    """
    from benchmarks.longmemeval import score
    from benchmarks.longmemeval.protocol import summarize

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "manifest.json").write_text(
        json.dumps({"effective_n": 1, "selected_sha": manifest.question_digest(["q1"])})
    )
    forged = {
        "question_id": "q1",
        "answer": "x",
        "answer_session_ids": ["s_retrieved"],
        "pack_units": ["s_retrieved"],
        "question_type": "single-session-user",
        "ok": 1.0,
    }
    data = [
        {
            "question_id": "q1",
            "question": "q?",
            "answer": "gold",
            "question_type": "single-session-user",
            "answer_session_ids": ["s_gold"],
        }
    ]
    assert summarize([dict(forged)])["overall"]["evidence_recall"] == 1.0
    _unverified, bound = score._canonical([dict(forged)], result_dir, data, allow_legacy=True)
    assert bound[0]["answer_session_ids"] == ["s_gold"]
    assert summarize(bound)["overall"]["evidence_recall"] == 0.0


def test_offline_scoring_parses_exactly_the_bytes_it_verified(tmp_path):
    """The bytes that were verified are the bytes that get parsed.

    Hashing a file and then reopening it to read it leaves a window in which
    the two can differ, so verification returns the bytes it hashed and the
    caller scores that copy.
    """
    from benchmarks.common import judging

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "judged.jsonl").write_text('{"ok": 1.0}\n')
    (result_dir / "manifest.json").write_text("{}\n")
    (result_dir / "score.json").write_text("{}\n")
    args = {
        "source": result_dir / "results.jsonl",
        "judged": result_dir / "judged.jsonl",
        "protocol": "rubric",
        "run": result_dir / "manifest.json",
        "score": result_dir / "score.json",
    }
    judging.write_manifest(result_dir, model="m", **args)
    _model, _weak, verified = judging.check_offline(result_dir, "m", **args)
    # whatever happens to the file afterwards, the caller scores this copy
    (result_dir / "judged.jsonl").write_text('{"ok": 0.0}\n')
    assert json.loads(verified.decode("utf-8"))["ok"] == 1.0


def test_a_seal_cannot_bind_a_score_that_does_not_exist(tmp_path):
    """An empty digest would pass a presence check and verify nothing."""
    from benchmarks.common import judging

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "judged.jsonl").write_text("{}\n")
    (result_dir / "manifest.json").write_text("{}\n")
    with pytest.raises(SystemExit, match="does not exist"):
        judging.write_manifest(
            result_dir,
            model="m",
            source=result_dir / "results.jsonl",
            judged=result_dir / "judged.jsonl",
            protocol="rubric",
            run=result_dir / "manifest.json",
            score=result_dir / "score.json",
        )


def test_a_result_file_that_appears_during_judging_stops_the_seal(tmp_path):
    """A per-file digest cannot notice a file that was not there to digest.

    LoCoMo's denominator is the set of result files. One written after the set
    was fixed would join the next score without any recorded digest moving.
    """
    from benchmarks.common import publish

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results_a.jsonl").write_text("{}\n")
    observed = publish.observe([result_dir / "results_a.jsonl"])
    (result_dir / "results_b.jsonl").write_text("{}\n")

    def guard():
        declared = {"results_a.jsonl"}
        present = {path.name for path in result_dir.glob("results_*.jsonl")}
        if foreign := sorted(present - declared):
            raise SystemExit(f"undeclared results appeared: {', '.join(foreign)}")

    with pytest.raises(SystemExit, match="results_b.jsonl"):
        publish.publish(
            result_dir,
            observed=observed,
            guard=guard,
            judged="{}\n",
            score={},
            manifest=lambda _sealed: None,
        )
    assert not (result_dir / "judged.jsonl").exists()


def test_the_seal_digests_what_this_scorer_wrote_not_what_is_on_disk(tmp_path):
    """Re-reading binds whatever won the last write, which may not be ours.

    `judged_sha`, `score_sha` and `run_manifest_sha` must be taken from the
    payloads this scorer wrote. Taken by reopening the files, a concurrent
    scorer landing between the write and the seal yields a manifest that
    verifies someone else's bytes — internally consistent and wrong. Digesting
    what was written instead makes the overwritten file visibly tampered with.
    """
    from benchmarks.common import judging, publish

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "manifest.json").write_text("{}\n")
    mine = '{"ok": 1.0}\n'

    def seal(sealed):
        # a rival scorer wins the race in exactly this window
        (result_dir / "judged.jsonl").write_text('{"ok": 0.0}\n')
        return judging.write_manifest(
            result_dir,
            model="m",
            source=judging.read_snapshot([result_dir / "results.jsonl"]),
            judged=result_dir / "judged.jsonl",
            protocol="rubric",
            run=result_dir / "manifest.json",
            score=result_dir / "score.json",
            sealed=sealed,
        )

    record = publish.publish(
        result_dir, judged=mine, score={"overall": {"accuracy": 1.0}}, manifest=seal
    )
    assert record["judged_sha"] == hashlib.sha256(mine.encode()).hexdigest()[:16]
    # and the artifact is therefore correctly reported as tampered with
    with pytest.raises(SystemExit, match="judged_sha"):
        judging.check_offline(
            result_dir,
            "m",
            source=result_dir / "results.jsonl",
            judged=result_dir / "judged.jsonl",
            protocol="rubric",
            run=result_dir / "manifest.json",
            score=result_dir / "score.json",
        )


def test_only_one_writer_may_hold_a_result_tag(tmp_path):
    """Every other guard on a result tag assumes a single writer.

    `refuse_overwrite` is a no-op while `judge_manifest.json` is absent, which
    is precisely the state two freshly started scorers are both in, so the
    exclusion has to come from a lock. It is released on the way out of the
    block, including when the body raises.
    """
    from benchmarks.common import singlewriter

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    with singlewriter.held(result_dir):
        with pytest.raises(SystemExit, match="already being written"):
            with singlewriter.held(result_dir):
                raise AssertionError("a second writer must not get in")
    # released on the way out, including after a failure inside the block
    with contextlib.suppress(RuntimeError):
        with singlewriter.held(result_dir):
            raise RuntimeError("boom")
    with singlewriter.held(result_dir):
        pass


def test_a_lock_left_by_a_dead_process_is_reported_not_stolen(tmp_path):
    """Silently breaking a stale lock is the same race one level up."""
    from benchmarks.common import singlewriter

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / singlewriter.LOCK_NAME).write_text(
        json.dumps(
            {
                "pid": 2**22,  # above any real pid on this platform
                "host": socket.gethostname(),
                "purpose": "scoring",
                "started": "2026-01-01T00:00:00",
            }
        )
    )
    with pytest.raises(SystemExit, match="That process is gone"):
        with singlewriter.held(result_dir):
            pass
    assert (result_dir / singlewriter.LOCK_NAME).exists()  # not stolen


def test_an_evaluator_refuses_to_append_to_a_sealed_artifact(corpus, tmp_path, monkeypatch):
    """A sealed artifact is finished; adding answers invalidates the seal.

    The seal binds the answers as they stood when they were judged. `--resume`
    into a judged directory appends answers nothing has judged, leaving
    judged.jsonl describing a strict subset of the results while every recorded
    digest still verifies — a tampered run that no later check can detect.
    """
    from benchmarks.longmemeval import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    result_dir = tmp_path / "longmemeval-sealed"
    result_dir.mkdir()
    (result_dir / "results.jsonl").write_text("{}\n")
    (result_dir / "judge_manifest.json").write_text("{}\n")
    with pytest.raises(SystemExit, match="already been judged and sealed"):
        evaluate.main(["--tag", "sealed", "--resume", "--n", "1"])


def test_an_evaluator_and_a_scorer_cannot_hold_one_tag_at_once(corpus, tmp_path, monkeypatch):
    """They write the same files; the scorer's guards assume it is alone."""
    from benchmarks.common import singlewriter
    from benchmarks.longmemeval import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    result_dir = tmp_path / "longmemeval-busy"
    result_dir.mkdir()
    with singlewriter.held(result_dir, purpose="scoring"):
        with pytest.raises(SystemExit, match="already being written"):
            evaluate.main(["--tag", "busy", "--n", "1"])


def test_paid_verdicts_survive_a_crash_before_the_last_question(tmp_path):
    """Verdicts cost money, so the cache is checkpointed as it fills.

    Written only once at the end, every verdict earned before a crash has to be
    bought again on the retry. Checkpointing keeps the file on disk ahead of
    the last question.
    """
    from benchmarks.common import judging

    path = tmp_path / "judge_cache.json"
    cache = {}
    for index in range(25):
        cache[f"k{index}"] = 1.0
        judging.checkpoint(cache, path)
    assert path.exists(), "25 paid verdicts should already be on disk"
    assert len(json.loads(path.read_text())) == 25


def _sealed_locomo_dir(tmp_path, verdicts):
    """A judged LoCoMo result directory with a real, current-schema seal."""
    from benchmarks.common import judging, publish

    result_dir = tmp_path / "locomo-rel"
    result_dir.mkdir()
    (result_dir / "results_a.jsonl").write_text(
        "".join(json.dumps({"qa_idx": i, "answer": "x"}) + "\n" for i in range(len(verdicts)))
    )
    (result_dir / "manifest.json").write_text('{"samples": [0]}\n')
    judged = "".join(
        json.dumps({"qa_idx": i, "category": 1, "J": v, "gold": "g", "answer": "x"}) + "\n"
        for i, v in enumerate(verdicts)
    )
    publish.publish(
        result_dir,
        judged=judged,
        score={"overall": {"accuracy": sum(verdicts) / len(verdicts)}},
        manifest=lambda s: judging.write_manifest(
            result_dir,
            model="gpt-4.1-mini",
            source=judging.read_snapshot([result_dir / "results_a.jsonl"]),
            judged=result_dir / "judged.jsonl",
            protocol=__import__("benchmarks.reproduce.sealed", fromlist=["PROTOCOLS"]).PROTOCOLS[
                "locomo"
            ],
            run=result_dir / "manifest.json",
            score=result_dir / "score.json",
            sealed=s,
        ),
    )
    return result_dir


def test_a_release_cannot_be_packaged_over_a_tampered_verdict_file(tmp_path):
    """Row count is not integrity, and neither is the aggregate.

    Recomputing the metric from whatever verdicts are in the file accepts any
    edit that preserves the row count, and a pair of offsetting flips preserves
    the accuracy as well. A release therefore reads verdicts only through the
    judge seal, which binds the exact bytes — and an artifact carrying no seal
    is not releasable rather than releasable by default.
    """
    from benchmarks.reproduce import sealed

    result_dir = _sealed_locomo_dir(tmp_path, [1.0, 0.0, 1.0, 0.0])
    judged_path = result_dir / "judged.jsonl"
    clean = sealed.verified_records("locomo", judged_path)
    assert [row["J"] for row in clean] == [1.0, 0.0, 1.0, 0.0]

    rows = [json.loads(line) for line in judged_path.read_text().splitlines()]
    # score-preserving swap: same row count, same accuracy, different verdicts
    rows[1]["J"], rows[2]["J"] = 1.0, 0.0
    judged_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(SystemExit, match="judged_sha"):
        sealed.verified_records("locomo", judged_path)

    # and an artifact with no seal at all is not releasable
    (result_dir / "judge_manifest.json").unlink()
    with pytest.raises(SystemExit, match="no judge manifest"):
        sealed.verified_records("locomo", judged_path)


def test_a_release_records_who_judged_it(tmp_path):
    """The published score has to name the judge behind its verdicts."""
    from benchmarks.reproduce import sealed

    result_dir = _sealed_locomo_dir(tmp_path, [1.0, 1.0])
    binding = sealed.seal(result_dir)
    assert binding["judge_model"] == "gpt-4.1-mini"
    assert binding["schema"] == 4
    assert binding["judged_sha"] and binding["score_sha"] and binding["run_manifest_sha"]


def test_a_stored_working_diff_can_actually_be_applied(tmp_path):
    """A stored diff is the only way back to the tree that produced a score.

    `_git()` strips its output, which removes the diff's trailing newline, and
    `git apply` rejects a patch that does not end in one with "corrupt patch at
    line N". A diff that cannot be applied records nothing recoverable, so the
    newline is restored before the diff is stored.
    """
    import subprocess

    from benchmarks.common import manifest

    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q", ".")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (repo / "f.txt").write_text("one\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    (repo / "f.txt").write_text("two\n")

    monkey = subprocess.run(
        ["git", "diff", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout
    run("git", "checkout", "--", "f.txt")  # back to the committed state
    stripped = tmp_path / "stripped.patch"
    stripped.write_text(monkey.strip())
    assert run("git", "apply", "--check", str(stripped)).returncode != 0, (
        "a stripped diff must be the thing that fails, or this test proves nothing"
    )

    stored = tmp_path / "stored.patch"
    stored.write_text(monkey.strip() + "\n")  # the shape manifest.write() stores
    check = subprocess.run(
        ["git", "apply", "--check", str(stored)], cwd=repo, capture_output=True, text=True
    )
    assert check.returncode == 0, check.stderr
    # and the source of what gets stored keeps that newline
    assert manifest.working_diff() in ("", manifest.working_diff().rstrip("\n") + "\n")


def test_the_recorded_diff_digest_covers_the_stored_diff_file(tmp_path):
    """`diff_sha` must identify the bytes `working.diff` actually holds.

    The stored diff carries a trailing newline so that `git apply` accepts it.
    A digest taken over the stripped text therefore describes a string that is
    on no disk, and the manifest's fingerprint would not match its own
    attachment.
    """
    import hashlib

    from benchmarks.common import manifest

    diff = manifest.working_diff()
    if not diff:
        pytest.skip("clean tree: no working diff to bind")
    assert diff.endswith("\n")
    recorded = manifest.describe(_profile_stub())["diff_sha"]
    assert recorded == hashlib.sha256(diff.encode("utf-8")).hexdigest()[:16]


def _profile_stub():
    class _P:
        __name__ = "stub"
        POSTPROC_RULES = "premise"

    return _P()


def test_the_sealed_check_happens_inside_the_lock(corpus, tmp_path, monkeypatch):
    """Checked outside it, there is a real interleaving that defeats it.

    The evaluator sees no seal; the scorer takes the lock and seals; the
    evaluator then takes the lock and appends to a sealed tag. Sealing during
    the window is exactly what the lock is there to serialise.
    """
    from benchmarks.common import singlewriter
    from benchmarks.longmemeval import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    result_dir = tmp_path / "longmemeval-race"
    result_dir.mkdir()

    real = singlewriter.held

    @contextlib.contextmanager
    def seal_then_hand_over(directory, purpose="scoring"):
        # stand in for the scorer that wins the race while the evaluator waits
        (directory / "judge_manifest.json").write_text("{}\n")
        with real(directory, purpose=purpose):
            yield

    monkeypatch.setattr(evaluate.singlewriter, "held", seal_then_hand_over)
    with pytest.raises(SystemExit, match="already been judged and sealed"):
        evaluate.main(["--tag", "race", "--n", "1"])


def test_a_release_is_bound_to_the_corpus_the_run_declared(tmp_path, monkeypatch):
    """Counting `*.xml` says nothing about their contents.

    Swapping a codebook for a different one of the same name preserved both
    the count and the pinned dataset digests, so the release still packaged
    as verified over answers no longer derivable from what is on disk.
    """
    from benchmarks.common import manifest
    from benchmarks.reproduce import sealed

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "manifest.json").write_text(json.dumps({"corpus_sha": "1" * 16}))
    monkeypatch.setattr(manifest, "corpus_digest", lambda paths: "1" * 16)
    assert sealed.require_same_corpus("locomo", result_dir) == "1" * 16

    monkeypatch.setattr(manifest, "corpus_digest", lambda paths: "2" * 16)
    with pytest.raises(RuntimeError, match="corpus on disk is"):
        sealed.require_same_corpus("locomo", result_dir)

    (result_dir / "manifest.json").write_text("{}")
    with pytest.raises(RuntimeError, match="declares no corpus_sha"):
        sealed.require_same_corpus("locomo", result_dir)


def test_a_budgeted_pack_refuses_an_estimated_tokenizer(monkeypatch):
    """`chars//4` prices rows differently, so it admits a different pack.

    The fallback is fine for a report and fatal for admission: the same budget
    seats a different set of rows under the estimate, and the pipeline becomes
    a different pipeline without saying so.
    """
    from memoket_kite.pipeline import ledger, retrieve

    class _Budgeted:
        TOKEN_CAP = 1500

    class _Unbudgeted:
        TOKEN_CAP = 0

    from memoket_kite.errors import ConfigurationError

    monkeypatch.setattr(ledger, "_TOKEN_ENCODER", False)
    # A library error, deliberately: SystemExit derives from BaseException and
    # so passes straight through the except-Exception boundary an embedding
    # application uses to contain a failing dependency.
    with pytest.raises(ConfigurationError, match="o200k_base encoding is unavailable"):
        retrieve._checked_token_cap(_Budgeted())
    assert retrieve._checked_token_cap(_Unbudgeted()) == 0


def test_the_manifest_records_the_tokenizer_and_the_plan_cache_contents(tmp_path):
    """A run that cannot name either cannot be replicated.

    A run reads compiled plans from the cache and writes newly compiled ones
    back into it, so the path alone describes a directory whose contents the
    run itself changed. The digest names what was actually there. The tokenizer
    belongs beside it because the token cap decides which rows are admitted,
    and the estimate and the exact encoder admit different packs.
    """
    from benchmarks.common import manifest

    cache = tmp_path / "plans"
    cache.mkdir()
    (cache / "a.json").write_text('{"plan": 1}')
    before = manifest.plan_cache_digest(cache)
    assert before

    (cache / "b.json").write_text('{"plan": 2}')
    assert manifest.plan_cache_digest(cache) != before, "a written cache must be visible"

    described = manifest.describe(_profile_stub(), plan_cache_sha=before)
    assert described["tokenizer"] in ("o200k_base", "chars//4 (tiktoken unavailable)")
    assert described["plan_cache_sha"] == before
    assert "tokenizer" in manifest._IDENTITY and "plan_cache_sha" in manifest._IDENTITY


def test_a_release_names_the_commit_that_was_measured(tmp_path, monkeypatch):
    """Stamping HEAD says the numbers came from the code being published.

    They may have come from anything: an older commit, or a working tree that
    cannot be checked out at all. The run manifests know, so the release asks
    them, and refuses when they disagree with each other, when the tree was
    dirty, or when the measured code has moved since.
    """
    import subprocess

    from benchmarks.reproduce import package

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)

    def git(*args):
        subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "src" / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "measured")
    measured = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=repo, text=True).strip()
    monkeypatch.setattr(package, "REPO_ROOT", repo)

    def run_dir(name, commit, dirty=False):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({"commit": commit, "dirty": dirty}))
        return directory / "judged.jsonl"

    manifest = {"benchmarks": {"locomo": {"result_path": "a"}, "longmemeval": {"result_path": "b"}}}
    paths = {"a": run_dir("a", measured), "b": run_dir("b", measured)}
    monkeypatch.setattr(package, "repository_path", lambda value: paths[value])
    assert package._released_commit(manifest) == measured

    # Writing the measured score into the README and into the release manifest
    # are both commits, and both land after the measurement. Neither can change
    # a number — the release tooling only reads sealed artifacts — so both pass.
    (repo / "README.md").write_text("85.00\n")
    (repo / "benchmarks" / "reproduce").mkdir(parents=True)
    (repo / "benchmarks" / "reproduce" / "manifest.json").write_text('{"score": 0.85}')
    git("add", "-A")
    git("commit", "-qm", "docs: the measured score")
    assert package._released_commit(manifest) == measured

    # The pipeline moving since the measurement does not.
    (repo / "src" / "a.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "fix: something real")
    with pytest.raises(RuntimeError, match="measured file"):
        package._released_commit(manifest)

    # Nor does a profile, which is under `benchmarks` but not under `reproduce`.
    (repo / "src" / "a.py").write_text("x = 1\n")  # put the pipeline back
    (repo / "benchmarks" / "profile.py").write_text("TOKEN_CAP = 900\n")
    git("add", "-A")
    git("commit", "-qm", "tune: a different cap")
    with pytest.raises(RuntimeError, match="measured file"):
        package._released_commit(manifest)

    monkeypatch.setattr(package, "_current_commit", lambda: "d" * 40)
    with pytest.raises(RuntimeError, match="not an ancestor"):
        package._released_commit(manifest)

    (paths["b"].parent / "manifest.json").write_text(json.dumps({"commit": "e" * 40}))
    with pytest.raises(RuntimeError, match="different commits"):
        package._released_commit(manifest)

    (paths["b"].parent / "manifest.json").write_text(
        json.dumps({"commit": measured, "dirty": True, "diff_sha": "beef"})
    )
    with pytest.raises(RuntimeError, match="dirty tree"):
        package._released_commit(manifest)


def test_a_run_that_cannot_identify_its_tree_refuses_to_describe_itself(monkeypatch):
    """Returning an empty commit is worse than raising.

    Every run on a machine without git would record the identity `""`, and two
    such identities compare equal, so resume would accept a run from one tree
    into the results of another — exactly the substitution the manifest exists
    to prevent. Refusing to describe the run makes the absence loud.
    """
    import subprocess

    from benchmarks.common import manifest

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", unavailable)
    with pytest.raises(manifest.GitUnavailable, match="cannot record provenance"):
        manifest.describe(_profile_stub())


def test_the_provider_tripwire_survives_the_retry_loop():
    """The provider swallows every `Exception` and retries three times.

    A tripwire derived from `Exception` is therefore caught by the code it is
    meant to catch: the call still happens, three times, and the test learns
    only that something timed out.
    """
    import conftest

    assert issubclass(conftest._Reached, BaseException)
    assert not issubclass(conftest._Reached, Exception)


def test_a_release_names_the_models_that_were_actually_used(tmp_path):
    """The release quotes the run manifests, which are the ones that were there.

    A dated snapshot and the floating alias that currently resolves to it are
    different model identities. If the release names one while the runs recorded
    the other, anyone reproducing it pins a model that did not produce these
    numbers, so all three roles — compile, answer and judge — are checked
    against what the run and judge manifests recorded.
    """
    from benchmarks.reproduce import package

    result_dir = tmp_path / "run"
    result_dir.mkdir()
    (result_dir / "manifest.json").write_text(
        json.dumps({"model": "gpt-4.1-mini", "answer_model": "gpt-4.1-mini"})
    )
    (result_dir / "judge_manifest.json").write_text(json.dumps({"judge_model": "gpt-4.1-mini"}))

    honest = {"compile": "gpt-4.1-mini", "answer": "gpt-4.1-mini", "judge": "gpt-4.1-mini"}
    package._require_declared_models("locomo", result_dir, honest)

    for role in honest:
        with pytest.raises(RuntimeError, match="actually used"):
            package._require_declared_models(
                "locomo", result_dir, {**honest, role: "gpt-4.1-mini-2025-04-14"}
            )


def test_a_release_ships_the_plan_cache_the_run_actually_used(tmp_path):
    """The cache to ship is the one the run named and digested.

    A fixed path ships whatever happens to sit there at packaging time, which
    may be a cache from another run or an empty directory. Taking the path from
    the run manifest and re-checking its digest means the release either ships
    the plans that produced the score or refuses to be built.
    """
    from benchmarks.common import manifest as run_manifest
    from benchmarks.reproduce import package

    cache = tmp_path / "plans"
    cache.mkdir()
    (cache / "a.json").write_text('{"plan": 1}')
    result_dir = tmp_path / "run"
    result_dir.mkdir()

    def record(**fields):
        (result_dir / "manifest.json").write_text(json.dumps(fields))

    record(plan_cache=str(cache), plan_cache_sha=run_manifest.plan_cache_digest(cache))
    assert package._verified_plan_cache("locomo", result_dir) == cache

    (cache / "b.json").write_text('{"plan": 2}')  # cache moved since the run
    with pytest.raises(RuntimeError, match="restore it"):
        package._verified_plan_cache("locomo", result_dir)

    record(plan_cache=str(tmp_path / "gone"), plan_cache_sha="x")
    with pytest.raises(RuntimeError, match="is gone"):
        package._verified_plan_cache("locomo", result_dir)

    record()
    with pytest.raises(RuntimeError, match="no plan cache"):
        package._verified_plan_cache("locomo", result_dir)


def test_a_release_refuses_a_distribution_built_from_another_tree(tmp_path, monkeypatch):
    """Only the two complete artifacts built from this exact tree are releasable."""
    import base64
    import hashlib
    import io
    import tarfile
    import zipfile

    from benchmarks.reproduce import package

    tree = tmp_path / "repo"
    (tree / "src" / "memoket_kite").mkdir(parents=True)
    source = tree / "src" / "memoket_kite" / "postproc.py"
    current = b"RULE = 'current'\n"
    source.write_bytes(current)
    readme = b"# KITE\n"
    license_text = b"unit-test license\n"
    (tree / "README.md").write_bytes(readme)
    (tree / "LICENSE").write_bytes(license_text)
    notice_text = b"KITE\nCopyright 2026 Memoket Inc.\n"
    (tree / "NOTICE").write_bytes(notice_text)
    (tree / "pyproject.toml").write_bytes(package._PYPROJECT_BYTES)
    monkeypatch.setattr(package, "REPO_ROOT", tree)

    project_urls = "".join(
        f"Project-URL: {label}, {url}\n" for label, url in package._PROJECT_URLS.items()
    )
    optional_metadata = "".join(
        f"Provides-Extra: {extra}\n" for extra in sorted(package._PROVIDES_EXTRA)
    ) + "".join(f"Requires-Dist: {requirement}\n" for requirement in sorted(package._REQUIRES_DIST))

    def metadata(body=readme, *, old_repository=False):
        payload = (
            "Metadata-Version: 2.4\n"
            f"Name: {package._DISTRIBUTION}\n"
            f"Version: {package._VERSION}\n"
            f"Summary: {package._DESCRIPTION}\n"
            f"License-Expression: {package._LICENSE_EXPRESSION}\n"
            f"Requires-Python: {package._REQUIRES_PYTHON}\n"
            f"{project_urls}{optional_metadata}\n"
        ).encode() + body
        if old_repository:
            payload = payload.replace(
                b"Repository, https://github.com/memoket/memoket-kite",
                b"Repository, https://github.com/old-owner/old-name",
            )
        return payload

    root = "memoket_kite-0.1.0"
    egg = f"{root}/src/memoket_kite.egg-info"
    dist_info = "memoket_kite-0.1.0.dist-info"
    egg_names = {
        "src/memoket_kite.egg-info/PKG-INFO",
        "src/memoket_kite.egg-info/SOURCES.txt",
        "src/memoket_kite.egg-info/dependency_links.txt",
        "src/memoket_kite.egg-info/requires.txt",
        "src/memoket_kite.egg-info/top_level.txt",
    }
    sources = {
        "LICENSE",
        "NOTICE",
        "README.md",
        "pyproject.toml",
        "src/memoket_kite/postproc.py",
        *egg_names,
    }

    def build(
        *,
        sdist_source=current,
        wheel_source=current,
        sdist_extra=None,
        wheel_extra=None,
        metadata_payload=None,
        record_overrides=None,
    ):
        package_info = metadata_payload or metadata()
        sdist_payloads = {
            f"{root}/LICENSE": license_text,
            f"{root}/NOTICE": notice_text,
            f"{root}/PKG-INFO": package_info,
            f"{root}/README.md": readme,
            f"{root}/pyproject.toml": package._PYPROJECT_BYTES,
            f"{root}/setup.cfg": b"[egg_info]\ntag_build = \ntag_date = 0\n\n",
            f"{root}/src/memoket_kite/postproc.py": sdist_source,
            f"{egg}/PKG-INFO": package_info,
            f"{egg}/SOURCES.txt": ("\n".join(sorted(sources)) + "\n").encode(),
            f"{egg}/dependency_links.txt": b"\n",
            f"{egg}/requires.txt": b"",
            f"{egg}/top_level.txt": b"memoket_kite\n",
            **(sdist_extra or {}),
        }
        sdist = tmp_path / "memoket_kite-0.1.0.tar.gz"
        with tarfile.open(sdist, "w:gz") as bundle:
            for name, payload in sdist_payloads.items():
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))

        wheel_payloads = {
            "memoket_kite/postproc.py": wheel_source,
            f"{dist_info}/licenses/LICENSE": license_text,
            f"{dist_info}/licenses/NOTICE": notice_text,
            f"{dist_info}/METADATA": package_info,
            f"{dist_info}/WHEEL": (
                b"Wheel-Version: 1.0\nGenerator: unit\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
            ),
            f"{dist_info}/top_level.txt": b"memoket_kite\n",
            **(wheel_extra or {}),
        }
        record_name = f"{dist_info}/RECORD"
        record_rows = {
            name: (
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                .rstrip(b"=")
                .decode("ascii"),
                str(len(payload)),
            )
            for name, payload in wheel_payloads.items()
        }
        record_rows[record_name] = ("", "")
        record_rows.update(record_overrides or {})
        wheel_payloads[record_name] = "".join(
            f"{name},{digest},{size}\n" for name, (digest, size) in sorted(record_rows.items())
        ).encode()
        wheel = tmp_path / "memoket_kite-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as bundle:
            for name, payload in wheel_payloads.items():
                bundle.writestr(name, payload)
        return [sdist, wheel]

    package._require_fresh_distributions(build())
    with pytest.raises(RuntimeError, match="different tree"):
        package._require_fresh_distributions(build(sdist_source=b"RULE = 'stale'\n"))
    with pytest.raises(RuntimeError, match="different tree"):
        package._require_fresh_distributions(build(wheel_source=b"RULE = 'stale'\n"))

    pair = build()
    with pytest.raises(RuntimeError, match="exactly one wheel and one source"):
        package._require_fresh_distributions(pair[:1])
    extra_wheel = tmp_path / "memoket_kite-0.1.0-py3-none-other.whl"
    extra_wheel.write_bytes(b"not selected")
    with pytest.raises(RuntimeError, match="exactly one wheel and one source"):
        package._require_fresh_distributions([*pair, extra_wheel])
    extra_glob_match = tmp_path / "memoket_kite-0.1.0-private.bak"
    extra_glob_match.write_bytes(b"not a distribution")
    with pytest.raises(RuntimeError, match="exactly one wheel and one source"):
        package._require_fresh_distributions([*pair, extra_glob_match])

    with pytest.raises(RuntimeError, match="unexpected member"):
        package._require_fresh_distributions(
            build(wheel_extra={"kite/backdoor.py": b"EVIL = True\n"})
        )
    with pytest.raises(RuntimeError, match="unexpected member"):
        package._require_fresh_distributions(
            build(wheel_extra={"memoket_kite/private-corpus.txt": b"private\n"})
        )
    with pytest.raises(RuntimeError, match="unexpected member"):
        package._require_fresh_distributions(
            build(sdist_extra={f"{root}/private-corpus.txt": b"private\n"})
        )
    with pytest.raises(RuntimeError, match="Project-URL"):
        package._require_fresh_distributions(build(metadata_payload=metadata(old_repository=True)))
    with pytest.raises(RuntimeError, match="Name metadata"):
        package._require_fresh_distributions(
            build(
                metadata_payload=metadata().replace(
                    b"Name: memoket-kite", b"Name: old-distribution"
                )
            )
        )
    with pytest.raises(RuntimeError, match="stale README"):
        package._require_fresh_distributions(build(metadata_payload=metadata(b"# OLD\n")))
    with pytest.raises(RuntimeError, match="Provides-Extra"):
        package._require_fresh_distributions(
            build(
                metadata_payload=metadata().replace(
                    b"Provides-Extra: benchmark\n", b"Provides-Extra: stale\n"
                )
            )
        )
    with pytest.raises(RuntimeError, match="Requires-Dist"):
        package._require_fresh_distributions(
            build(
                metadata_payload=metadata().replace(
                    b"Requires-Dist: tiktoken>=0.7", b"Requires-Dist: tiktoken>=9"
                )
            )
        )
    wheel_member = "memoket_kite/postproc.py"
    with pytest.raises(RuntimeError, match="RECORD hash or size"):
        package._require_fresh_distributions(
            build(record_overrides={wheel_member: ("sha256=wrong", str(len(current)))})
        )
    correct_hash = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(current).digest()).rstrip(
        b"="
    ).decode("ascii")
    with pytest.raises(RuntimeError, match="RECORD hash or size"):
        package._require_fresh_distributions(
            build(record_overrides={wheel_member: (correct_hash, str(len(current) + 1))})
        )
    with pytest.raises(RuntimeError, match="RECORD hash or size"):
        package._require_fresh_distributions(build(record_overrides={wheel_member: ("", "")}))
    with pytest.raises(RuntimeError, match="leave its own hash and size empty"):
        package._require_fresh_distributions(
            build(record_overrides={f"{dist_info}/RECORD": ("sha256=wrong", "1")})
        )
    for stale_name, stale_payload in (
        (f"{root}/README.md", b"# OLD\n"),
        (f"{root}/LICENSE", b"old license\n"),
        (f"{root}/pyproject.toml", b"[project]\nname='old'\n"),
    ):
        with pytest.raises(RuntimeError, match="stale generated content"):
            package._require_fresh_distributions(build(sdist_extra={stale_name: stale_payload}))
    with pytest.raises(RuntimeError, match="stale generated content"):
        package._require_fresh_distributions(
            build(wheel_extra={f"{dist_info}/licenses/LICENSE": b"old license\n"})
        )


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, (True, False)),
        ({"KITE_ARM": "baseline"}, (False, False)),
        ({"KITE_ARM": "candidate"}, (True, True)),
        ({"KITE_ARM": "baseline", "KITE_ON": "1"}, (True, False)),
        ({"KITE_ARM": "candidate", "KITE_ON": "0"}, (False, True)),
        # A typo must not select an arm, and a non-boolean override must not
        # be guessed at: both fall back to the binding's own default.
        ({"KITE_ARM": "baselien"}, (True, False)),
        ({"KITE_ON": "yes"}, (True, False)),
        ({"KITE_ON": ""}, (True, False)),
    ],
)
def test_an_ablation_arm_is_read_when_it_is_asked_for(env, expected, monkeypatch):
    """The arm is read when a flag is consulted, not when the module loads.

    A module-level constant freezes the arm at import time, and the profile is
    imported once per process, so a harness or a test that sets the arm
    afterwards asks for `baseline` and silently gets `default`.
    """
    from benchmarks.common import experimental

    for name in ("KITE_ARM", "KITE_ON", "KITE_OFF"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert (experimental.flag("ON", True), experimental.flag("OFF", False)) == expected


def test_a_cost_knob_stays_outside_the_arms(monkeypatch):
    """`candidate` means "every mechanism on", not "spend freely".

    The support check costs an extra model call per answer, so it is a budget
    decision rather than a mechanism under study. Routing it through the arm
    would make selecting an arm silently change what a run costs, and would
    make the two arms differ in more than the thing being compared. It stays on
    its own switch, off unless that switch is set.
    """
    from benchmarks.common import experimental

    monkeypatch.delenv("KITE_SUPPORT_CHECK", raising=False)
    for arm in ("default", "baseline", "candidate"):
        monkeypatch.setenv("KITE_ARM", arm)
        assert experimental.enabled("SUPPORT_CHECK") is False
    monkeypatch.setenv("KITE_SUPPORT_CHECK", "1")
    assert experimental.enabled("SUPPORT_CHECK") is True


def test_one_oversized_row_cannot_empty_the_pack():
    """A row too large for an empty pack says nothing about what follows it.

    Rows are considered in rank order, so an oversized row can arrive first
    with nothing seeded ahead of it. Treating "does not fit" as "stop" then
    ends admission on the first row and hands the reader no evidence at all;
    the row is skipped and the smaller rows behind it are still admitted.
    """
    from memoket_kite.pipeline import retrieve

    huge = {"type": "line", "id": "L1", "unit": "u", "score": 1.0, "text": "x " * 5000}
    small = {"type": "line", "id": "L2", "unit": "u", "score": 1.0, "text": "tiny"}

    class _Store:
        units: dict = {}

        def hydrate(self, *args, **kwargs):
            return []

    rows = retrieve._select_evidence_rows_tokencap(
        [], [huge, small], _Store(), cap=50, unit_cap=9, row_cap=40, speaker=False
    )
    assert [row["id"] for row in rows] == ["L2"]


def test_a_named_question_selection_is_exactly_what_was_named(corpus, tmp_path, monkeypatch):
    """An id the corpus does not hold stops the run."""
    from benchmarks.longmemeval import evaluate

    monkeypatch.setattr(evaluate, "RESULTS_ROOT", tmp_path)
    with pytest.raises(SystemExit):
        evaluate.main(["--tag", "unit", "--question-id", "no-such-question"])


def test_a_solicited_judgement_is_never_rewritten_into_a_refusal():
    """The refusal rules answer "was the premise absent", which a solicited
    judgement has no premise to fail.

    A question that asks for an opinion is answered with reasoning, and sound
    reasoning routinely contains a hedge like "there is no evidence of a better
    time to buy". The hedge rule replaces the whole answer with the canonical
    refusal, so without this exemption it destroys correct advice. The
    exemption is recognised from the question's own wording, so it works on any
    corpus rather than on one dataset's labels.
    """
    from benchmarks.common.policies import PROTOCOL_ADVICE_QUESTION as ADVICE_QUESTION

    solicited = (
        "I'm trying to decide whether to buy a NAS device now or wait. What do you think?",
        "Do you think it might be time to replace the dresser?",
        "Do you think it would be a good idea to book it now?",
        "Could there be a reason my sourdough keeps failing?",
        "What should I cook this weekend?",
        "Any ideas for a birthday present?",
    )
    factual = (
        "When did I buy the NAS device?",
        "How many bikes did I service in March?",
        "Which restaurant did I go to last week?",
        "Did I mention my sister's birthday?",
    )
    for question in solicited:
        assert ADVICE_QUESTION.search(question), question
    for question in factual:
        assert not ADVICE_QUESTION.search(question), question


def test_the_advice_predicate_covers_the_frozen_preference_set():
    """The exemption is all-or-nothing per class, in both directions.

    Every preference question solicits an opinion, so any one the predicate
    misses is an answer the hedge rule is free to overwrite. No abstention
    question solicits one, so any it matches is a refusal the rules can no
    longer produce. Pinning both totals against the corpus makes a narrowing
    or a widening edit fail rather than merely shift a proportion.
    """
    import json

    from benchmarks.common.policies import PROTOCOL_ADVICE_QUESTION as ADVICE_QUESTION
    from benchmarks.longmemeval.evaluate import DATASET

    if not DATASET.exists():
        pytest.skip("frozen corpus not present")
    data = json.loads(DATASET.read_text())
    preference = [q for q in data if q["question_type"] == "single-session-preference"]
    abstention = [q for q in data if str(q["question_id"]).endswith("_abs")]
    assert sum(bool(ADVICE_QUESTION.search(q["question"])) for q in preference) == len(preference)
    assert sum(bool(ADVICE_QUESTION.search(q["question"])) for q in abstention) == 0


def test_protocol_families_live_in_the_benchmark_tree_not_the_package():
    """Protocol-specific question families stay out of the installable package.

    The binding's predicate is the library's generic pattern plus the families
    a benchmark protocol adds, and it is assembled in the benchmark tree and
    injected by the profiles. The library keeps only the generic pattern, so an
    application gets only the library predicate; both
    bindings run the composed predicate.
    """
    from benchmarks.common.policies import (
        PROTOCOL_ADVICE_FAMILIES,
        PROTOCOL_ADVICE_QUESTION,
    )
    from benchmarks.locomo import profile as locomo
    from benchmarks.longmemeval import profile as longmemeval
    from memoket_kite.pipeline.patterns import ADVICE_QUESTION

    protocol_specific = "Could there be a reason my sourdough keeps failing?"
    generic = "Any ideas for a birthday present?"
    assert PROTOCOL_ADVICE_QUESTION.search(protocol_specific)
    assert not ADVICE_QUESTION.search(protocol_specific)  # the package stays generic
    assert ADVICE_QUESTION.search(generic) and PROTOCOL_ADVICE_QUESTION.search(generic)
    for pattern in (PROTOCOL_ADVICE_FAMILIES,):
        assert pattern not in ADVICE_QUESTION.pattern
    assert locomo.ADVICE_QUESTION is PROTOCOL_ADVICE_QUESTION
    assert longmemeval.ADVICE_QUESTION is PROTOCOL_ADVICE_QUESTION
    # composition, not a second copy: the composed pattern embeds the
    # generic one verbatim, so the two cannot drift apart silently
    assert PROTOCOL_ADVICE_QUESTION.pattern.startswith(ADVICE_QUESTION.pattern)


def test_every_library_template_is_fingerprinted_not_only_the_listed_ones():
    """A score names every prompt it ran under, including the ones added since."""
    import ast
    import pathlib

    from benchmarks.common import manifest

    shas = manifest._library_prompt_shas()
    for module_name in manifest.HASHED_LIBRARY_PROMPTS:
        source = pathlib.Path("src/" + module_name.replace(".", "/") + ".py")
        for node in ast.parse(source.read_text()).body:
            if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if len(value.value) >= manifest._TEMPLATE_FLOOR:
                    assert f"{module_name}:{node.targets[0].id}" in shas

    # The answer template and the pick between drafts both decide what ships.
    assert "memoket_kite.prompts.answer:DEFAULT_ANSWER_PROMPT" in shas
    assert "memoket_kite.prompts.answer:SELF_CONSISTENCY_PICK_PROMPT" in shas
