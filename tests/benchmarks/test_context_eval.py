import json
import subprocess
import sys
import time

import pytest

from benchmarks.agent import codex_timing, context_eval, task_eval


def test_identical_text_sources_keep_the_original_segment_references():
    pack = {
        "spans": [
            {
                "evidence": [{"conversation_id": "first", "segment_id": "L10"}],
                "same_text_sources": [{"conversation_id": "second"}],
            },
            {"evidence": [{"conversation_id": "third", "segment_id": "L14"}]},
        ]
    }
    assert context_eval.evidence_ids(pack) == {"first#L10", "second#L10", "third#L14"}


def test_codex_trial_resumes_the_captured_session_and_keeps_default_runs_ephemeral(
    monkeypatch, tmp_path
):
    commands = []
    session = "a4773cf3-1c99-482c-93db-5e60f35b9eef"

    def fake_run(command, **kwargs):
        commands.append(command)
        stdout = "\n".join(
            json.dumps(event)
            for event in (
                {"type": "thread.started", "thread_id": session},
                {"type": "turn.completed", "usage": {"input_tokens": 10}},
            )
        )
        return subprocess.CompletedProcess(command, 0, stdout)

    monkeypatch.setattr(context_eval.subprocess, "run", fake_run)
    first = context_eval.codex_trial("codex", "model", tmp_path, "First", persist_session=True)
    second = context_eval.codex_trial(
        "codex", "model", tmp_path, "Next", resume_session=first["session_id"]
    )
    ordinary = context_eval.codex_trial("codex", "model", tmp_path, "Independent")
    assert first["session_id"] == second["session_id"] == session
    assert commands[1][-3:] == ["resume", session, "-"]
    assert all("--ephemeral" not in command for command in commands[:2])
    assert "--ephemeral" in commands[2] and "session_id" not in ordinary


def test_task_benchmark_requires_both_behavior_and_supporting_citation():
    evidence = {"boundary": ["first#L3"], "value": ["second#L5"]}
    scope = {"first#L3", "second#L5", "second#L8"}
    checks = {"boundary": False, "value": True}
    score = task_eval._score(checks, "[first#L3] [second#L8]", evidence, scope)
    assert score["passed_claims"] == 0
    assert score["claims"]["value"]["behavior_correct"]
    assert score["claims"]["boundary"]["supporting_citations"] == ["first#L3"]
    supported = task_eval._score(checks, "[second#L5]", evidence, scope)
    assert supported["passed_claims"] == 1 and not supported["full_pass"]
    outside = task_eval._score(checks, "[second#L5] [outside#L2]", evidence, scope)
    assert outside["passed_claims"] == 0


def test_submit_benchmark_counts_prefilled_evidence_even_without_a_tool_call():
    trial = {
        "case": "release",
        "repetition": 1,
        "seconds": 1.0,
        "access_prompt_bytes": 6,
        "prompt_bytes": 50,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "exit_code": 0,
        "tool_stdout_bytes": 15,
        "command_calls": 1,
        "score": {"full_pass": True},
    }
    rows = [
        {**trial, "arm": "kite", "source_tool_bytes": 12, "hook_context_bytes": 0},
        {**trial, "arm": "files", "source_tool_bytes": 15, "hook_context_bytes": 0},
        {**trial, "arm": "submit", "source_tool_bytes": 0, "hook_context_bytes": 100},
    ]
    summary = context_eval.summarize_host(rows, arms=("kite", "files", "submit"))
    assert summary["both_passed_pairs"] == 1
    assert summary["both_passed_efficiency"]["submit"]["source_context_bytes"] == 100
    assert summary["arms"]["submit"]["hook_deliveries"] == 1


def test_same_prompt_hook_uses_a_relative_runtime_from_the_benchmark_directory(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    tool = tmp_path / "bin" / "kite-tool"
    tool.parent.mkdir()
    tool.write_text(
        "#!/bin/sh\n"
        'test "$5" = "--question=Rollout?" || exit 1\n'
        'printf "%s\\n" "[release#L2] Rollout begins Monday."\n'
    )
    tool.chmod(0o700)
    project = tmp_path / "project"
    project.mkdir()
    launcher = project / "kite"
    context_eval.write_launcher(launcher, tmp_path / "memory", None, kite_tool="bin/kite-tool")
    metrics = tmp_path / "metrics.json"
    task_eval._submit_hook(tmp_path, launcher, metrics, task_eval._CONTEXT_HOOK, same_prompt=True)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "submit_hook.py")],
        input=json.dumps(
            {"hook_event_name": "UserPromptSubmit", "cwd": str(project), "prompt": "Rollout?"}
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "[release#L2] Rollout begins Monday." in context
    assert json.loads(metrics.read_text())["bytes"] == len(context.encode())


@pytest.mark.skipif(sys.platform == "win32", reason="timed pipe observation uses POSIX selectors")
def test_timing_observes_large_events_and_does_not_double_count_shell_intervals():
    code = "import json; print(json.dumps({'text': 'x' * 70000}), flush=True)"
    exit_code, events = codex_timing.read_events(
        [sys.executable, "-c", code], "", None, time.perf_counter()
    )
    assert exit_code == 0 and len(json.loads(events[0][1])["text"]) == 70000
    observed = [
        (1, {"type": "turn.started"}),
        (2, {"type": "item.started", "item": {"type": "command_execution", "id": "1"}}),
        (3, {"type": "item.started", "item": {"type": "command_execution", "id": "2"}}),
        (4, {"type": "item.completed", "item": {"type": "command_execution", "id": "1"}}),
        (5, {"type": "item.completed", "item": {"type": "command_execution", "id": "2"}}),
        (7, {"type": "turn.completed"}),
    ]
    timing, intervals = codex_timing.describe(observed)
    assert intervals == {"1": (2, 4), "2": (3, 5)}
    assert timing == {
        "startup_seconds": 1,
        "turn_seconds": 6,
        "shell_seconds": 3,
        "timed_shell_calls": 2,
        "before_first_shell_seconds": 1,
        "after_last_shell_seconds": 2,
    }


def test_comparison_benchmark_uses_disjoint_recording_windows(tmp_path):
    corpus = tmp_path / "transcripts"
    corpus.mkdir()
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    for name, day in (
        ("first", "2026-04-02"),
        ("gap", "2026-04-12"),
        ("second", "2026-04-20"),
    ):
        (corpus / f"{name}_transcript.txt").write_text(
            f"00:00\nSpeaker A\nChecklist approval is marked {name}.\n", encoding="utf-8"
        )
        (metadata / f"{name}.json").write_text(
            json.dumps({"conversation_id": name, "started_at": day}), encoding="utf-8"
        )
    case = {
        "id": "approval",
        "questions": ["Check checklist approval", "Check checklist approval again"],
        "start_date": "2026-04-01",
        "end_date": "2026-04-03",
        "compare_start_date": "2026-04-19",
        "compare_end_date": "2026-04-21",
        "evidence": ["first#L3", "second#L3"],
        "claims": {
            "sequence": {"pattern": "first.*second", "ref_groups": [["first#L3", "second#L3"]]},
        },
    }
    output = tmp_path / "evaluation"
    output.mkdir()
    sources = context_eval.read_corpus(corpus, "transcript-text", metadata)
    scope = context_eval.prepare_case_corpora(sources, [case], output, with_files=True)["approval"]
    assert scope.citations == set(case["evidence"])
    assert sorted(file.name for file in scope.files.rglob("*.txt")) == [
        "first_transcript.txt",
        "second_transcript.txt",
    ]
    row, rendered = context_eval.retrieval_case(
        scope.workspace, case, question=True, audit_retrieval=True
    )
    assert row["complete"]
    assert row["retrieval_audit"]["claims"]["sequence"] == {
        "recordings_located": True,
        "recordings_quoted": True,
        "evidence_quoted": True,
    }
    launcher = output / "kite"
    context_eval.write_launcher(
        launcher, scope.workspace, case["questions"], date_args=context_eval.recording_args(case)
    )
    result = subprocess.check_output([launcher, "context", "--text"], text=True)
    assert result.rstrip("\n") == rendered.rstrip("\n")
    first, second = result.split("Question 2")
    assert "first#L3" in first and "second#L3" not in first
    assert "second#L3" in second and "first#L3" not in second


@pytest.mark.parametrize(
    ("matching_lines", "host_follow_up"), [(False, False), (True, False), (False, True)]
)
def test_bounded_context_benchmark_shares_the_same_originals_between_arms(
    tmp_path, monkeypatch, matching_lines, host_follow_up
):
    corpus = tmp_path / "originals"
    corpus.mkdir()
    metadata = tmp_path / "dates"
    metadata.mkdir()
    originals = {
        "outside": b"00:00\nSpeaker A\nRelease checklist was postponed.\n",
        "inside": b"00:00\r\nSpeaker A\r\nThe release checklist is approved.\r\nRae owns the deployment.\r\n",
        "undated": b"00:00\nSpeaker A\nThe checklist is unconfirmed.\n",
    }
    for name, raw in originals.items():
        (corpus / f"{name}_transcript.txt").write_bytes(raw)
    for name, day in (("outside", "2026-03-31"), ("inside", "2026-04-01")):
        (metadata / f"{name}.json").write_text(
            json.dumps({"conversation_id": name, "started_at": day}), encoding="utf-8"
        )
    case = {
        "id": "release",
        "task": "Check the release checklist and deployment ownership.",
        "questions": ['Was the "release checklist" approved?', 'Who "owns the deployment"?'],
        "start_date": "2026-04-01",
        "end_date": "2026-04-01",
        "claims": {
            "approval": {"pattern": "approved", "refs": ["inside#L3"]},
            "owner": {"pattern": "Rae", "refs": ["inside#L4"]},
        },
    }
    output = tmp_path / "evaluation"
    output.mkdir(mode=0o700)
    sources = context_eval.read_corpus(corpus, "transcript-text", metadata)
    scopes = context_eval.prepare_case_corpora(sources, [case], output, with_files=True)
    scope = scopes["release"]
    assert scope.source_count == 1 and scope.citations == {"inside#L3", "inside#L4"}
    row, pack = context_eval.retrieval_case(
        scope.workspace, case, question=True, matching_lines=matching_lines
    )
    assert row["claims_with_evidence"] == row["claims_requiring_evidence"] == 2
    assert row["complete"]
    assert "Question 2" in pack and "outside" not in pack

    def host(binary, model, cwd, prompt):
        assert all(question in prompt for question in case["questions"])
        assert "2026-04-01 至 2026-04-01" in prompt
        if (cwd / "kite").is_file():
            assert "./kite context --text" in prompt
            assert ("--conversation-id" in prompt) == host_follow_up
            default = subprocess.check_output([cwd / "kite", "context", "--text"], text=True)
            assert "Question 2" in default and "inside#L3" in default and "inside#L4" in default
            if matching_lines:
                first, second = default.split("Question 2", 1)
                assert "inside#L4" not in first and "inside#L3" not in second
            result = json.loads(subprocess.check_output([cwd / "kite", "topics"], text=True))
            assert result["ok"]
            found = subprocess.check_output(
                [cwd / "kite", "context", "--term", "checklist", "--text"], text=True
            )
            assert "inside#L3" in found and "outside" not in found and "undated" not in found
        else:
            assert "--conversation-id" not in prompt
            visible = list((cwd / "transcripts").rglob("*.txt"))
            assert [item.relative_to(cwd / "transcripts").as_posix() for item in visible] == [
                "2026-04-01/inside_transcript.txt"
            ]
            assert visible[0].read_bytes() == originals["inside"]
        return {
            "exit_code": 0,
            "seconds": 0.1,
            "usage": {"input_tokens": 40, "cached_input_tokens": 10, "output_tokens": 3},
            "command_calls": 1,
            "tool_stdout_bytes": 20,
            "answer": json.dumps(
                {
                    "approval": {"value": "approved", "citations": ["inside#L3"]},
                    "owner": {"value": "Rae", "citations": ["inside#L4"]},
                }
            ),
        }

    monkeypatch.setattr(context_eval, "codex_trial", host)
    trials = list(
        context_eval.run_host(
            "codex",
            "test",
            corpus,
            [case],
            {case["id"]: pack},
            output / "memory",
            question=True,
            scopes=scopes,
            matching_lines=matching_lines,
            host_follow_up=host_follow_up,
        )
    )
    assert all(trial["score"]["full_pass"] for trial in trials)
    summary = context_eval.summarize_host(trials)
    assert summary["completed_pairs"] == summary["both_passed_pairs"] == 1
    assert summary["arms"]["kite"]["uncached_input_tokens"] == 30
    assert summary["arms"]["files"]["total_tokens"] == 43
    assert summary["both_passed_efficiency"]["files"]["cached_input_tokens"] == 10
    assert context_eval.summarize_host(trials[:1])["completed_pairs"] == 0


def test_text_benchmark_and_claim_grading_preserve_source_lines(tmp_path):
    (tmp_path / "notes.md").write_bytes(b"# Notes\r\n\r\nRae owns deployment.\r\n")
    sources = context_eval.read_corpus(tmp_path, "text")
    visible = context_eval.scope_citations(sources)
    assert visible == {"notes#text-line-1", "notes#text-line-3"}
    assert not context_eval.select_sources(sources, ("2026-01-01", None))
    specs = {
        "owner": {"pattern": "Rae", "refs": ["notes#text-line-3"]},
        "unknown": {"unknown": True},
    }
    answer = {
        "owner": {"value": "Rae", "citations": ["notes#text-line-1"]},
        "unknown": {"value": "unknown", "citations": []},
    }
    assert context_eval.grade_claims(json.dumps(answer), specs, visible)["passed_claims"] == 1
    answer["owner"]["citations"] = ["notes#text-line-3", "outside#text-line-8"]
    bad = context_eval.grade_claims(json.dumps(answer), specs, visible)
    assert bad["claims"]["owner"]["out_of_scope_citations"] == ["outside#text-line-8"]
    assert not bad["full_pass"]
    answer["owner"]["citations"].pop()
    assert context_eval.grade_claims(json.dumps(answer), specs, visible)["full_pass"]


def test_grouped_citations_require_a_complete_alternative():
    specs = {
        "target": {
            "pattern": "April",
            "ref_groups": [["planning#L8"], ["followup#L4", "followup#L9"]],
        },
        "cause": {"pattern": "review", "refs": ["review#L6"]},
        "approval": {"unknown": True},
    }
    refs = {"followup#L9", "recap#L3"}
    pack = {
        "packing_trace": {
            "total": 90,
            "candidates": [{"conversation_id": "followup"}, {"conversation_id": "review"}],
        }
    }
    audit = context_eval.retrieval_audit(pack, refs, specs)
    assert audit["candidate_spans_traced"] == 2
    assert audit["candidate_spans_total"] == 90
    assert audit["claims"] == {
        "target": {"recordings_located": True, "recordings_quoted": True, "evidence_quoted": False},
        "cause": {"recordings_located": True, "recordings_quoted": False, "evidence_quoted": False},
    }
    visible = refs | {"followup#L4", "planning#L8"}
    for citations, passed in (
        (["followup#L9", "recap#L3"], False),
        (["followup#L4", "followup#L9"], True),
        (["planning#L8"], True),
    ):
        answer = json.dumps({"target": {"value": "April", "citations": citations}})
        score = context_eval.grade_claims(answer, {"target": specs["target"]}, visible)
        assert score["full_pass"] is passed
    with pytest.raises(ValueError, match="ref_groups"):
        context_eval.claim_reference_groups({"ref_groups": [[]]})


def test_benchmark_copies_only_annotations_bound_to_the_same_source_lines(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "handoff.txt").write_text("Rae owns deployment.\n")
    (raw / "unannotated.txt").write_text("The build is ready.\n")
    sources = context_eval.read_corpus(raw, "text")
    record = json.loads(json.dumps(sources[0].record))
    record["source"]["payload"]["title"] = "A title from the reference workspace"
    record["annotations"] = {
        "facts": [
            {"content": "Rae owns deployment.", "segment_ids": ["text-line-1"], "topics": ["owner"]}
        ],
        "index_spans": [{"segment_ids": ["text-line-1"], "topics": ["pilot handoff"]}],
    }
    annotated = tmp_path / "annotated"
    context_eval.kite(annotated, "ingest", "--input", "-", payload=record)
    with context_eval.Workspace.open(annotated, read_only=True) as workspace:
        version = workspace.version()
        assert workspace.records(active_only=True)[0]["facts"]
    initial_hash = context_eval.corpus_hash(sources)
    assert context_eval.apply_index_annotations(sources, annotated) == version
    assert context_eval.corpus_hash(sources) == initial_hash
    assert sources[0].conversation.get("title") != record["source"]["payload"]["title"]
    assert sources[0].record["annotations"]["facts"] == []
    assert "annotations" not in sources[1].record
    stats = context_eval.annotation_summary(sources)
    assert stats["annotated_source_count"] == stats["index_span_count"] == 1
    assert len(stats["annotations_sha256"]) == 64
    evaluated = tmp_path / "evaluated"
    context_eval.kite(
        evaluated, "index-sources", "--input", "-", payload={"sources": [s.record for s in sources]}
    )
    pack = context_eval.kite(evaluated, "context", "--term", "pilot handoff")
    assert context_eval.evidence_ids(pack) == {"handoff#text-line-1"}
    assert (
        context_eval.kite(evaluated, "context", "--term", "A title from the reference")["spans"]
        == []
    )

    updated = context_eval.score_revisions(
        evaluated,
        [source.record for source in sources],
        [
            {
                "id": "owner",
                "evidence": "handoff#text-line-1",
                "replace": "Rae",
                "with": "Maya",
                "terms": ["deployment"],
            }
        ],
    )
    assert updated["cases"] == [{"id": "owner", "correct_context": True, "historical_quote": True}]
    assert context_eval.kite(evaluated, "context", "--term", "pilot handoff")["spans"] == []

    (raw / "handoff.txt").write_text("A different person owns deployment.\n")
    edited = context_eval.read_corpus(raw, "text")
    with pytest.raises(ValueError, match="annotation source lines differ"):
        context_eval.apply_index_annotations(edited, annotated)
    assert all("annotations" not in item.record for item in edited)
