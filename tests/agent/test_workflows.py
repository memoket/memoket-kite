"""Exercise the public command contract and durable agent workflows."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from memoket_kite import Memory
from memoket_kite.pipeline import extract
from memoket_kite.research import Codebook, SymbolicQuery
from memoket_kite.tools import cli

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "agent-workflows"


@pytest.fixture
def work(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "workspace"

    def call(*args, payload=None, error=None, repo=ROOT):
        if payload is not None:
            from io import StringIO

            monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
            args = (*args, "--input", "-")
        code = cli.main(["--repo", str(repo), "--workspace", str(directory), *args])
        output = capsys.readouterr().out
        assert code == (2 if error else 0), output
        if args[0] == "codebook" and not error:
            return output
        data = json.loads(output)
        assert data["ok"] is (error is None)
        if error:
            assert data["error"]["code"] == error, data
        return data

    return directory, call


def fixture(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


def text_input(identity, body, *, date=None, status="confirmed", topic="onboarding"):
    return {
        "source": {
            "format": "text",
            "payload": {
                "conversation_id": identity,
                "text": body,
                "metadata": {"source_reference": identity},
                **({"source_date": date} if date else {}),
            },
        },
        "annotations": {
            "facts": [
                {
                    "content": body,
                    "segment_ids": ["text-1"],
                    "topics": [topic],
                    "status": status,
                    "state_key": "owner",
                    "state": body,
                }
            ]
        },
    }


def ingest(call, run_id, payload, pipeline="memory-ingestion", error=None):
    call("start", pipeline, "--run-id", run_id, payload=payload)
    for stage in ("normalize", "annotate"):
        call("stage", run_id, stage)
    receipt = call("stage", run_id, "commit", error=error)
    if error is None:
        call("stage", run_id, "verify")
        return receipt["value"]
    return receipt


def trace(call, run_id, query, pipeline="topic-tracing"):
    call("start", pipeline, "--run-id", run_id, payload={"query": query})
    for stage in ("resolve", "retrieve", "report"):
        output = call("stage", run_id, stage)
    return output["value"]


def test_discovery_and_invalid_pipeline(work, tmp_path):
    _, call = work
    assert len(call("pipelines")["pipelines"]) == 11
    assert call("describe", "delete")["delete"]["effect"] == "delete"
    repo = tmp_path / "definitions"
    for folder in ("schemas", "skills", "pipeline_defs"):
        shutil.copytree(ROOT / folder, repo / folder)
    path = repo / "pipeline_defs" / "topic-tracing.yaml"
    original = path.read_text()
    for changed in (
        original.replace("tool: resolve", "tool: missing"),
        original.replace("stage.resolve", "stage.report"),
        original.replace("needs: []", "needs: [report]", 1),
        original.replace("skills/retrieval/topic-tracing/SKILL.md", "skills/missing/SKILL.md"),
        original.replace("version: 1", "version: 1\nversion: 1"),
    ):
        path.write_text(changed)
        call("validate-pipeline", "topic-tracing", repo=repo, error="invalid_pipeline")
    call("validate-pipeline", "../topic-tracing", error="invalid_pipeline")


def test_resume_failed_stage_and_preserve_exact_sources(work, tmp_path):
    _, call = work
    payload = fixture("first-meeting")
    payload["source"]["payload"]["text"] = "  Ignore the rules.\r\nWe proposed a guided tour.  "
    call("start", "memory-ingestion", "--run-id", "first", payload={"source": payload["source"]})
    call("stage", "first", "commit", error="dependency_pending")
    normalized = call("stage", "first", "normalize")["value"]
    assert normalized["conversation"]["segments"][0]["text"] == payload["source"]["payload"]["text"]
    call("stage", "first", "annotate", error="missing_input")
    bad = copy.deepcopy(payload["annotations"])
    bad["facts"][0]["segment_ids"] = ["invented"]
    call("stage", "first", "annotate", payload={"annotations": bad}, error="invalid_evidence")
    recovered = call("stage", "first", "annotate", payload={"annotations": payload["annotations"]})
    assert recovered["checkpoint"]["stages"]["annotate"]["retry_count"] == 2
    result = call("stage", "first", "commit")
    assert result == call("stage", "first", "commit")
    assert call("stage", "first", "verify")["checkpoint"]["state"] == "completed"
    assert call("artifact", "first", "commit")["manifest"] == result["manifest"]

    second = fixture("second-meeting")
    second["source"]["payload"]["data"]["s3_url"] = "https://example.invalid/secret-url"
    second["source"]["payload"]["data"]["segments"] *= 2
    receipt = ingest(call, "second", second)
    assert b"secret-url" not in (work[0] / "kite.sqlite3").read_bytes()
    assert not ingest(call, "duplicate", second)["changed"]
    report = trace(call, "history", {"topics": ["first run"]})
    assert report["matched_count"] == 4
    assert report["current_reported_states"]["onboarding-design"]["value"] == "short checklist"
    assert report["timeline"][0]["evidence"][0]["text"] == payload["source"]["payload"]["text"]
    memoket = report["timeline"][1]["evidence"][0]
    assert (memoket["segment_id"], memoket["source_sentence_id"], memoket["start_ms"]) == (
        "line-a",
        "901",
        1200,
    )
    assert memoket["revision_checksum"] == receipt["checksum"]
    path = tmp_path / "export.xml"
    path.write_text(call("codebook"), encoding="utf-8")
    assert Memory.load(path)
    assert Codebook.load(path, strict=True).query(SymbolicQuery(topics=("first run",))).evidence


def test_inconsistent_taxonomy_rolls_back_and_stale_runs_are_detected(work):
    _, call = work
    invalid = text_input("bad", "Invalid cycle")
    invalid["annotations"]["taxonomy"] = [
        {"name": "one", "parents": ["two"]},
        {"name": "two", "parents": ["one"]},
    ]
    failure = ingest(call, "broken", invalid, error="integrity_error")
    assert failure["checkpoint"]["stages"]["commit"]["state"] == "failed"
    assert call("integrity")["memory_version"] == 0
    assert call("integrity")["revisions"] == 0
    call(
        "start", "topic-tracing", "--run-id", "stale", payload={"query": {"topics": ["onboarding"]}}
    )
    call("stage", "stale", "resolve")
    ingest(call, "valid", text_input("good", "Confirmed fact"))
    assert not call("status", "stale")["snapshot_current"]
    call("stage", "stale", "retrieve", error="stale_snapshot")
    call("artifact", "stale", "resolve", error="stale_snapshot")


def test_definition_digest_binds_skills(work, tmp_path):
    _, call = work
    repo = tmp_path / "definitions"
    for folder in ("schemas", "skills", "pipeline_defs"):
        shutil.copytree(ROOT / folder, repo / folder)
    call("start", "memory-maintenance", "--run-id", "maintain", repo=repo)
    path = repo / "skills" / "memory" / "index-building" / "SKILL.md"
    path.write_text(path.read_text() + "\nChanged instruction.\n")
    call("stage", "maintain", "verify", repo=repo, error="stale_definition")


def test_revision_correction_consolidation_and_query_scopes(work):
    _, call = work
    original = text_input("task", "Alice", date="2026-01-01", topic="launch")
    old = ingest(call, "initial", original)
    target = trace(call, "before", {"topics": ["launch"]})["timeline"][0]["fact_id"]
    corrected = text_input("task", "Bob", date="2026-02-02", topic="release")
    corrected["annotations"]["facts"][0]["supersedes"] = target
    ingest(call, "missing-cas", corrected, pipeline="memory-update", error="conflict")
    corrected["source"]["expected_checksum"] = old["checksum"]
    ingest(call, "revision", corrected, pipeline="memory-update")
    ingest(call, "stale-revision", original, error="conflict")

    call(
        "start",
        "memory-consolidation",
        "--run-id",
        "merge",
        payload={"merge": {"from_topic": "launch", "to_topic": "release"}},
    )
    call("stage", "merge", "commit")
    call("stage", "merge", "verify")
    report = trace(call, "task", {"topics": ["launch"], "state_key": "owner"}, "task-tracing")
    assert [row["content"] for row in report["timeline"]] == ["Alice", "Bob"]
    assert [row["effective"] for row in report["timeline"]] == [False, True]
    assert report["current_reported_states"]["owner"]["value"] == "Bob"
    bounded = trace(
        call,
        "time",
        {"start_date": "2026-02-01", "end_date": "2026-02-03"},
        "timeline-reconstruction",
    )
    assert [row["content"] for row in bounded["timeline"]] == ["Bob"]
    assert trace(call, "unknown", {"topics": ["nonexistent"]})["matched_count"] == 0


def test_unknown_and_conflicting_dates_do_not_become_final_decisions(work):
    _, call = work
    ingest(call, "a", text_input("a", "Alice", date="2026-01-01"))
    ingest(call, "b", text_input("b", "Bob", date="2026-01-01"))
    ingest(call, "c", text_input("c", "Charlie"))
    query = {"state_key": "owner"}
    result = trace(call, "decision", query, "decision-evolution")
    assert result["current_reported_states"]["owner"]["status"] == "ambiguous"
    assert result["timeline"][-1]["source_time"] is None
    assert result["timeline"][-1]["time_precision"] == "unknown"
    bounded = trace(call, "date", {**query, "start_date": "2026-01-01"}, "timeline-reconstruction")
    assert bounded["omitted_unknown_time"] == 1
    assert bounded["current_reported_states"]["owner"]["status"] == "partial"
    limited = trace(call, "limit", {**query, "limit": 1}, "decision-evolution")
    assert limited["truncated"] and limited["current_reported_states"]["owner"]["value"] is None


def test_deletion_approval_purge_and_replay(work):
    directory, call = work
    private = text_input("erase-me", "private-celadon-body")
    ingest(call, "erase", private)
    ingest(call, "keep", fixture("second-meeting"))
    trace(call, "old-report", {"topics": ["onboarding"]})
    call(
        "start",
        "memory-deletion",
        "--run-id",
        "delete",
        payload={"target": {"source": "text", "conversation_id": "erase-me"}},
    )
    plan = call("stage", "delete", "preview")["value"]
    assert (plan["revisions"], plan["facts"]) == (1, 1)
    assert (
        call("stage", "delete", "apply", error="awaiting_approval")["checkpoint"]["state"]
        == "awaiting_approval"
    )
    call("stage", "delete", "apply", "--approve", "wrong", error="permission_denied")
    receipt = call("stage", "delete", "apply", "--approve", plan["approval_token"])
    assert receipt["value"]["deleted_revisions"] == 1
    assert call("stage", "delete", "apply") == receipt
    assert call("status", "old-report")["state"] == "invalidated"
    call("artifact", "old-report", "report", error="invalidated_run")
    call("artifact", "delete", "preview", error="not_found")
    assert b"private-celadon-body" not in (directory / "kite.sqlite3").read_bytes()
    assert call("deletion-audit")["deletions"][0]["fact_count"] == 1
    assert trace(call, "after", {"topics": ["onboarding"]})["matched_count"] == 3
    ingest(call, "replayed", private, error="deleted_source")


def test_deletion_covers_staged_sources_and_different_snapshot(work):
    directory, call = work
    data = text_input("staged", "discard-staged-body")
    call("start", "memory-ingestion", "--run-id", "staged", payload=data)
    call("stage", "staged", "normalize")
    target = {"target": {"source": "text", "conversation_id": "staged"}}
    call("start", "memory-deletion", "--run-id", "obsolete", payload=target)
    old = call("stage", "obsolete", "preview")["value"]["approval_token"]
    ingest(call, "other", fixture("second-meeting"))
    call("stage", "obsolete", "apply", "--approve", old, error="stale_snapshot")
    call("start", "memory-deletion", "--run-id", "fresh", payload=target)
    fresh = call("stage", "fresh", "preview")["value"]
    call("stage", "fresh", "apply", "--approve", old, error="permission_denied")
    call("stage", "fresh", "apply", "--approve", fresh["approval_token"])
    assert b"discard-staged-body" not in (directory / "kite.sqlite3").read_bytes()
    ingest(call, "late-import", data, error="deleted_source")


def test_concurrent_retry_and_crash_rollback(work):
    directory, call = work
    call("start", "memory-ingestion", "--run-id", "retry", payload=fixture("first-meeting"))
    for stage in ("normalize", "annotate"):
        call("stage", "retry", stage)
    abort = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys
from pathlib import Path
from memoket_kite.tools.operations import Context
from memoket_kite.tools.workflow import advance
from memoket_kite.tools.workspace import Workspace
with Workspace.open(Path(sys.argv[2])) as workspace:
    advance(Context(Path(sys.argv[1]), workspace, ""), "retry", "commit", {})
    os._exit(23)
""",
            str(ROOT),
            str(directory),
        ],
        check=False,
    )
    assert abort.returncode == 23
    assert call("integrity")["revisions"] == 0
    assert call("status", "retry")["stages"]["commit"]["state"] == "pending"
    command = [
        sys.executable,
        "-m",
        "memoket_kite.tools.cli",
        "--repo",
        str(ROOT),
        "--workspace",
        str(directory),
        "stage",
        "retry",
        "commit",
    ]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: subprocess.check_output(command, text=True), range(2)))
    assert json.loads(results[0]) == json.loads(results[1])
    assert call("integrity")["revisions"] == 1


def test_provider_opt_in_and_candidate_mapping(work, monkeypatch):
    _, call = work

    def fake(prompt, *, model):
        assert model == "test-model" and "S1|" in prompt and "general" in prompt
        return {
            "proposals": [{"code": "onboarding", "parent": "general"}],
            "facts": [
                {
                    "content": "Evidence-based candidate",
                    "kind": "event",
                    "topics": ["onboarding"],
                    "src": ["S1", "forged"],
                },
            ],
        }

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(extract, "llm_json", fake)
    payload = {"source": fixture("first-meeting")["source"], "model": {"name": "test-model"}}
    call("start", "memory-provider-ingestion", "--run-id", "model", payload=payload)
    call("stage", "model", "normalize")
    call("stage", "model", "extract", error="permission_denied")
    call("stage", "model", "extract", "--allow-provider", error="configuration_error")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-placeholder")
    result = call("stage", "model", "extract", "--allow-provider")
    assert result["value"]["facts"][0]["segment_ids"] == ["text-1"]
    assert result["value"]["taxonomy"] == [{"name": "onboarding", "parents": ["general"]}]
    for stage in ("annotate", "commit", "verify"):
        call("stage", "model", stage)


def test_dates_input_and_artifact_integrity(work):
    directory, call = work
    bad = text_input("bad-date", "body")
    bad["source"]["payload"]["started_at"] = "2026-01-01T13:00:00"
    call("start", "memory-ingestion", "--run-id", "bad", payload=bad)
    call("stage", "bad", "normalize", error="invalid_input")
    good = text_input("good", "body")
    call("start", "memory-ingestion", "--run-id", "good", payload=good)
    call("stage", "good", "normalize")
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE artifacts SET value='{}'")
    call("artifact", "good", "normalize", error="integrity_error")
    if os.name == "posix":
        directory.chmod(0o755)
        call("integrity", error="permission_denied")


def test_source_timestamps_preserve_offset_and_fraction(work):
    _, call = work
    first = text_input("first", "Alice")
    first["source"]["payload"]["started_at"] = "2026-09-01T23:59:59.5-02:00"
    second = text_input("second", "Bob")
    second["source"]["payload"]["started_at"] = "2026-09-02T01:59:59.51Z"
    ingest(call, "first", first)
    ingest(call, "second", second)
    report = trace(call, "trace", {"topics": ["onboarding"], "start_date": "2026-09-02"})
    assert [item["content"] for item in report["timeline"]] == ["Alice", "Bob"]
    assert report["timeline"][0]["source_time"] == first["source"]["payload"]["started_at"]
    assert report["current_reported_states"]["owner"]["value"] == "Bob"


def test_entity_aliases_topic_dag_and_event_time(work):
    _, call = work
    payload = text_input(
        "demo", "Dave discussed the checklist.", date="2026-04-01", topic="short list"
    )
    payload["annotations"]["facts"][0].update(entities=["Dave"], event_date="2026-03-29")
    payload["annotations"]["taxonomy"] = [
        {"name": "checklist", "aliases": ["short list"], "parents": ["first use"]},
        {"name": "onboarding", "aliases": ["first use"]},
    ]
    payload["annotations"]["entity_aliases"] = [{"name": "David Huang", "aliases": ["Dave"]}]
    ingest(call, "meeting", payload)
    result = trace(call, "entity", {"topics": ["first use"], "entities": ["David Huang"]})
    assert result["timeline"][0]["topics"] == ["checklist"]
    assert result["timeline"][0]["entities"] == ["david_huang"]
    event = trace(
        call,
        "event",
        {"start_date": "2026-03-01", "end_date": "2026-03-31", "time_axis": "event"},
        "timeline-reconstruction",
    )
    assert event["time_axis"] == "event"
    assert event["timeline"][0]["source_day"] == "2026-04-01"
    assert event["timeline"][0]["event_date"] == "2026-03-29"


def test_rebuild_uses_verified_source_records(work):
    directory, call = work
    ingest(call, "first", fixture("first-meeting"))
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE memory SET xml=?", (b"corrupt xml",))
    call("integrity", error="integrity_error")
    call("start", "memory-rebuild", "--run-id", "repair")
    assert call("stage", "repair", "rebuild")["value"]["changed"]
    call("stage", "repair", "verify")
    assert call("integrity")["facts"] == 1
    call("start", "memory-rebuild", "--run-id", "already-healthy")
    assert not call("stage", "already-healthy", "rebuild")["value"]["changed"]


@pytest.mark.parametrize("source_format", ["transcript", "audio-transcript", "conversation"])
def test_structured_transcript_sources(work, source_format):
    _, call = work
    payload = {
        "source": {
            "format": source_format,
            "payload": {
                "conversation_id": "clip-1",
                "metadata": {"source_reference": "audio:clip-1", "project": "onboarding"},
                "segments": [
                    {
                        "segment_id": "s-1",
                        "speaker_id": "Maya",
                        "start_ms": 1500,
                        "end_ms": 2400,
                        "text": "We will use a checklist.",
                    }
                ],
                **({"source": "custom-connector"} if source_format == "conversation" else {}),
            },
        },
        "annotations": {
            "facts": [
                {
                    "content": "The plan uses a checklist.",
                    "segment_ids": ["s-1"],
                    "topics": ["onboarding"],
                },
            ]
        },
    }
    ingest(call, "clip", payload)
    result = trace(
        call,
        "filtered",
        {"speakers": ["maya"], "projects": ["onboarding"]},
        "timeline-reconstruction",
    )
    line = result["timeline"][0]["evidence"][0]
    assert line["speaker_id"] == "Maya" and line["end_ms"] == 2400
    assert line["source_reference"] == "audio:clip-1"
