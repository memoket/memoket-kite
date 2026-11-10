"""Exercise the public command contract and durable agent workflows."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from threading import Event, Thread

import pytest

from memoket_kite import Memory
from memoket_kite.pipeline import extract
from memoket_kite.research import Codebook, SymbolicQuery
from memoket_kite.tools import audio, cli, operations, workspace
from memoket_kite.tools.contracts import ToolError, digest, encode, validate

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "agent-workflows"


@pytest.fixture
def work(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "workspace"

    def call(*args, payload=None, error=None, repo=ROOT, workspace_dir=None):
        if payload is not None:
            from io import StringIO

            monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
            args = (*args, "--input", "-")
        resources = [] if repo is None else ["--repo", str(repo)]
        code = cli.main([*resources, "--workspace", str(workspace_dir or directory), *args])
        output = capsys.readouterr().out
        assert code == (2 if error else 0), output
        if args[0] == "codebook" and not error:
            return output
        data = json.loads(output)
        if args[0] in {"audio-source", "file-source"} and not error:
            return data
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


@pytest.mark.parametrize(
    ("args", "usage"),
    [
        ([], "kite-tool "),
        (["private-command"], "kite-tool "),
        (["query", "--pipeline", "private-pipeline"], "kite-tool query "),
        (["query"], "kite-tool query "),
        (["query", "--input", "private-path", "--topic", "private-topic"], "kite-tool query "),
        (["evidence", "private-line", "--context", "private-value"], "kite-tool evidence "),
        (["ingest", "--input", "private-path", "--resume", "private-run"], "kite-tool ingest "),
        (["pipelines", "--private-option"], "kite-tool "),
    ],
)
def test_cli_reports_argument_errors_as_json_without_echoing_arguments(
    args, usage, tmp_path, capsys
):
    directory = tmp_path / "workspace"
    assert cli.main(["--workspace", str(directory), *args]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["ok"] is False and result["error"]["code"] == "invalid_arguments"
    assert f"usage: {usage}" in result["error"]["message"]
    assert "private" not in captured.out
    assert not directory.exists()


def test_cli_help_still_prints_usage(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["query", "--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "usage: kite-tool query " in captured.out and "--input FILE" in captured.out
    assert "--topic NAME" in captured.out
    assert captured.err == ""


def test_discovery_and_invalid_pipeline(work, tmp_path):
    _, call = work
    assert len(call("pipelines")["pipelines"]) == 12
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
    call("pipelines", repo=repo, error="invalid_pipeline")
    call("validate-pipeline", "../topic-tracing", error="invalid_pipeline")


def test_installable_extension_and_custom_connector(work, tmp_path):
    _, call = work
    extension = tmp_path / "extension"
    definitions = extension / "pipeline_defs"
    definitions.mkdir(parents=True)
    skill = extension / "skills" / "incident-review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Incident review\nUse incident timeline evidence.\n")
    original = (ROOT / "pipeline_defs" / "topic-tracing.yaml").read_text()
    custom = (
        original.replace("id: topic-tracing", "id: example-incident-review", 1)
        .replace("skills/retrieval/topic-tracing/SKILL.md", "skills/incident-review/SKILL.md")
        .replace(
            "skills/retrieval/evidence-verification/SKILL.md", "skills/incident-review/SKILL.md"
        )
    )
    (definitions / "example-incident-review.yaml").write_text(custom)
    (extension / "schemas").mkdir()
    (extension / "schemas" / "pipeline.schema.json").write_text("ignored by the tool")

    def custom_call(*args, **kwargs):
        return call("--extensions", str(extension), *args, repo=None, **kwargs)

    assert custom_call("resources")["extension_root"] == str(extension)
    catalog = custom_call("pipelines")
    assert len(catalog["pipelines"]) == 13
    assert "invalid_extensions" not in catalog
    built_in = "memory-ingestion"
    assert custom_call("validate-pipeline", built_in) == call("validate-pipeline", built_in)
    name = "example-incident-review"
    assert custom_call("validate-pipeline", name)["skill_root"] == str(extension)
    (definitions / "bad-encoding.yaml").write_bytes(b"\xff")
    (definitions / "bad-date.yaml").write_text("version: 2026-13-01\n")
    (definitions / "bad-syntax.yaml").write_text("stages: [\n")
    (definitions / "not-a-file.yaml").mkdir()
    (definitions / "bad-skill.yaml").write_text(
        custom.replace(name, "bad-skill").replace("SKILL.md", "unreadable.md")
    )
    skill.with_name("unreadable.md").write_bytes(b"\xff")
    partial = custom_call("pipelines")
    assert partial["pipelines"] == catalog["pipelines"]
    assert [(issue["id"], issue["code"]) for issue in partial["invalid_extensions"]] == [
        ("bad-date", "invalid_pipeline"),
        ("bad-encoding", "invalid_pipeline"),
        ("bad-skill", "invalid_pipeline"),
        ("bad-syntax", "invalid_pipeline"),
        ("not-a-file", "invalid_pipeline"),
    ]
    custom_call("start", "bad-syntax", error="invalid_pipeline")
    custom_call("validate-pipeline", "bad-skill", error="invalid_pipeline")

    # A local connector supplies a canonical conversation and retains the original message ID.
    exported = tmp_path / "incident.json"
    quote = "We moved the alert review to Tuesday."
    exported.write_text(
        json.dumps(
            {
                "conversation_id": "incident-42",
                "source": "sample-incident-log",
                "metadata": {"source_reference": "incident-42"},
                "segments": [{"segment_id": "update-1", "text": quote}],
            }
        )
    )
    source = call("file-source", str(exported), "--format", "conversation", repo=None)
    source["annotations"] = {
        "facts": [{"content": quote, "segment_ids": ["update-1"], "topics": ["alerts"]}]
    }
    ingest(call, "incident", source)
    custom_call("start", name, "--run-id", "review", payload={"query": {"topics": ["alerts"]}})
    custom_call("stage", "review", "resolve")
    assert call("status", "review", repo=None)["resume"]["reason"] == "invalid_pipeline"
    assert custom_call("status", "review")["resume"]["stages"][0]["id"] == "retrieve"

    skill.write_text("Changed instructions")
    assert custom_call("status", "review")["resume"]["reason"] == "stale_definition"
    skill.write_text("# Incident review\nUse incident timeline evidence.\n")
    relocated = tmp_path / "moved-extension"
    extension.rename(relocated)
    extension = relocated
    custom_call("stage", "review", "retrieve")
    result = custom_call("stage", "review", "report")["value"]
    evidence = result["timeline"][0]["evidence"][0]
    assert (result["trace"], evidence["source"], evidence["segment_id"], evidence["text"]) == (
        name,
        "sample-incident-log",
        "update-1",
        quote,
    )


def test_extension_rejects_invalid_roots_and_builtin_shadowing(work, tmp_path):
    _, call = work
    extension = tmp_path / "extension"

    def custom_call(*args, **kwargs):
        return call("--extensions", str(extension), *args, repo=None, **kwargs)

    custom_call("pipelines", error="invalid_input")
    definitions = extension / "pipeline_defs"
    definitions.mkdir(parents=True)
    (definitions / "topic-tracing.yaml").write_text("id: topic-tracing")
    error = custom_call("validate-pipeline", "topic-tracing", error="invalid_pipeline")
    assert "shadows bundled pipeline" in error["error"]["message"]
    catalog = custom_call("pipelines")
    bundled = call("pipelines")["pipelines"]
    assert catalog["pipelines"] == [row for row in bundled if row["id"] != "topic-tracing"]
    assert catalog["invalid_extensions"] == [{"id": "topic-tracing", **error["error"]}]
    custom_call("start", "topic-tracing", error="invalid_pipeline")
    assert call("validate-pipeline", "topic-tracing")["ok"]


def test_transcript_pages_preserve_segments_and_byte_boundaries(work):
    _, call = work
    segments = [
        {"segment_id": "first", "text": "Exact line.\r\n", "speaker_id": "Maya", "start_ms": 3},
        {"segment_id": "large", "text": "界" * 100, "start_ms": 8},
        {"segment_id": "last", "text": "Finish"},
    ]
    source = {
        "format": "transcript",
        "payload": {
            "conversation_id": "long-recording",
            "metadata": {"source_reference": "long-recording", "project": "hidden-metadata"},
            "segments": segments,
        },
    }
    call("start", "memory-ingestion", "--run-id", "long", payload={"source": source})
    call("stage", "long", "normalize", "--summary")
    pages = []
    offset = 0
    while offset is not None:
        page = call(
            "segments", "long", "normalize", "--offset", str(offset), "--byte-budget", "128"
        )
        pages.append(page)
        offset = page["next_offset"]
    assert [part for page in pages for part in page["segments"]] == segments
    assert [page["over_budget"] for page in pages] == [False, True, False]
    assert pages[0]["total_segments"] == 3 and pages[0]["conversation_id"] == "long-recording"
    assert "hidden-metadata" not in json.dumps(pages)
    assert all(
        page["segment_payload_bytes"]
        == len(json.dumps(page["segments"], separators=(",", ":"), sort_keys=True).encode())
        for page in pages
    )
    assert len(call("segments", "long", "normalize", "--limit", "1")["segments"]) == 1
    assert not call("segments", "long", "normalize", "--offset", "3")["segments"]
    for flag, value in (
        ("--offset", "-1"),
        ("--offset", "4"),
        ("--limit", "0"),
        ("--byte-budget", "127"),
    ):
        call("segments", "long", "normalize", flag, value, error="invalid_input")


def test_transcript_pages_obey_artifact_integrity_and_deletion(work):
    directory, call = work
    payload = text_input("target", "synthetic sensitive line")
    ingest(call, "target", payload)
    call("segments", "target", "commit", error="invalid_input")
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        row = db.execute(
            "SELECT value FROM artifacts WHERE run_id='target' AND stage='normalize'"
        ).fetchone()
        db.execute("UPDATE artifacts SET value='{}' WHERE run_id='target' AND stage='normalize'")
    call("segments", "target", "normalize", error="integrity_error")
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute(
            "UPDATE artifacts SET value='broken' WHERE run_id='target' AND stage='normalize'"
        )
    call("segments", "target", "normalize", error="integrity_error")
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE artifacts SET value=? WHERE run_id='target' AND stage='normalize'", row)
    _delete_source(call, "erase", {"source": "text", "conversation_id": "target"})
    call("segments", "target", "normalize", error="invalidated_run")


def test_stage_summary_preserves_artifacts_and_retry_errors(work):
    _, call = work
    source = text_input("summary-source", "private transcript in a saved artifact")
    call("start", "memory-ingestion", "--run-id", "summary", payload={"source": source["source"]})
    normalized = call("stage", "summary", "normalize", "--summary")
    artifact = call("artifact", "summary", "normalize")
    assert normalized == {
        "ok": True,
        "run_id": "summary",
        "stage_id": "normalize",
        "stage_state": "completed",
        "run_state": "running",
        "retry_count": 0,
        "manifest": artifact["manifest"],
    }
    assert artifact["value"]["conversation"]["segments"][0]["text"] == (
        "private transcript in a saved artifact"
    )
    assert call("stage", "summary", "normalize", "--summary") == normalized
    call("stage", "summary", "commit", "--summary", error="dependency_pending")
    failed = call("stage", "summary", "annotate", "--summary", error="missing_input")
    assert (failed["stage_id"], failed["stage_state"], failed["run_state"]) == (
        "annotate",
        "failed",
        "failed",
    )
    assert "manifest" not in failed
    annotation = call(
        "stage",
        "summary",
        "annotate",
        "--summary",
        payload={"annotations": source["annotations"]},
    )
    assert annotation["retry_count"] == 1
    call("stage", "summary", "commit", "--summary")
    assert call("stage", "summary", "verify", "--summary")["run_state"] == "completed"
    assert call("integrity")["revisions"] == 1


def test_discover_interrupted_runs_and_resume_without_disclosing_inputs(work):
    directory, call = work
    assert call("runs")["runs"] == []
    call("runs", "--limit", "0", error="invalid_input")
    call("runs", "--limit", "101", error="invalid_input")
    run_id = call("start", "memory-ingestion")["run_id"]
    source = text_input("source-private-id", "private-text-marker")
    assert call("status", run_id)["resume"]["stages"][0]["missing_inputs"] == {"source": "source"}
    call("stage", run_id, "normalize", payload={"source": source["source"]})
    call("stage", run_id, "annotate", error="missing_input")
    call("start", "memory-maintenance", "--run-id", "newer")
    runs = call("runs")["runs"]
    assert [run["run_id"] for run in runs] == ["newer", run_id]
    assert call("runs", "--limit", "1")["runs"] == runs[:1]
    assert set(runs[1]) == {
        "run_id",
        "pipeline_id",
        "state",
        "current_stage",
        "created_at",
        "updated_at",
        "snapshot_current",
    }
    status = call("status", run_id)
    assert status["resume"] == {
        "state": "available",
        "stages": [
            {
                "id": "annotate",
                "tool": "annotate",
                "skill": "skills/memory/fact-extraction/SKILL.md",
                "missing_inputs": {"annotations": "annotations"},
                "required_flags": [],
            }
        ],
    }
    assert "private-text-marker" not in json.dumps([runs, status])
    assert "source-private-id" not in json.dumps([runs, status])
    call("stage", run_id, "annotate", payload={"annotations": source["annotations"]})
    assert call("status", run_id)["resume"]["stages"][0]["id"] == "commit"
    call("stage", run_id, "commit")
    call("stage", run_id, "verify")
    assert call("status", run_id)["resume"] == {"state": "complete", "stages": []}

    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE runs SET inputs = ? WHERE id = ?", ('{"private-marker":NaN}', run_id))
    status = call("status", run_id)
    assert status["resume"] == {"state": "blocked", "reason": "integrity_error", "stages": []}
    assert "private-marker" not in json.dumps(status)


def test_run_listing_filters_before_paginating_in_creation_order(work):
    _, call = work
    call("start", "memory-ingestion", "--run-id", "first-pending")
    call("start", "memory-ingestion", "--run-id", "failed")
    call("stage", "failed", "normalize", error="missing_input")
    call("start", "memory-maintenance", "--run-id", "maintenance")
    call("start", "memory-ingestion", "--run-id", "later-pending")
    call(
        "ingest",
        "--run-id",
        "completed",
        payload=text_input("private-source-marker", "private-text-marker"),
    )

    filters = ("runs", "--pipeline", "memory-ingestion", "--unfinished", "--limit", "1")
    page = call(*filters)
    assert [run["run_id"] for run in page["runs"]] == ["later-pending"]
    assert page["next_before"] == "later-pending"
    call("start", "memory-ingestion", "--run-id", "created-between-pages")
    page = call(*filters, "--before", page["next_before"])
    assert [(run["run_id"], run["state"]) for run in page["runs"]] == [("failed", "failed")]
    assert page["next_before"] == "failed"
    page = call(*filters, "--before", page["next_before"])
    assert [run["run_id"] for run in page["runs"]] == ["first-pending"]
    assert page["next_before"] is None
    assert call(*filters, "--before", "first-pending")["runs"] == []
    assert call("runs", "--pipeline", "memory-maintenance")["runs"][0]["run_id"] == "maintenance"
    call("runs", "--before", "missing", error="not_found")
    assert "private" not in json.dumps(call("runs"))


def test_workflow_inspection_and_receipt_replay_read_committed_state_during_a_write(
    work, monkeypatch
):
    directory, call = work
    payload = text_input("inspection", "Use the committed transcript.")
    call("start", "memory-ingestion", "--run-id", "interrupted", payload=payload)
    normalized = call("stage", "interrupted", "normalize")
    checkpoint = call("status", "interrupted")
    listing = call("runs", "--unfinished")

    connect = sqlite3.connect

    def quick_connect(*args, **kwargs):
        kwargs["timeout"] = 0.1
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", quick_connect)
    with closing(connect(directory / "kite.sqlite3")) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE runs SET checkpoint='{}' WHERE id='interrupted'")
        assert call("runs", "--unfinished") == listing
        assert call("status", "interrupted") == checkpoint
        assert call("artifact", "interrupted", "normalize")["value"] == normalized["value"]
        segments = call("segments", "interrupted", "normalize")["segments"]
        assert segments[0]["text"] == "Use the committed transcript."
        writer.rollback()

        completed = call("ingest", "--resume", "interrupted")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE runs SET checkpoint='{}' WHERE id='interrupted'")
        assert call("ingest", "--resume", "interrupted") == completed
        writer.rollback()


def test_ingest_replay_checks_artifacts_even_when_the_run_is_completed(work):
    directory, call = work
    payload = text_input("inspection", "Use the committed transcript.")
    call("ingest", "--run-id", "completed", payload=payload)
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE artifacts SET value='{}' WHERE run_id='completed' AND stage='normalize'")
    result = call("ingest", "--resume", "completed", error="integrity_error")
    assert result["run_id"] == "completed" and result["stage_id"] == "normalize"
    assert "receipt" not in result


def test_workflow_inspection_and_resume_recover_an_interrupted_sqlite_write(work):
    directory, call = work
    payload = text_input("inspection", "Use the committed transcript.")
    call("start", "memory-ingestion", "--run-id", "interrupted", payload=payload)
    call("stage", "interrupted", "normalize")
    checkpoint = call("status", "interrupted")
    crash = """
import os
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA cache_size=4")
db.execute("BEGIN IMMEDIATE")
db.execute("UPDATE runs SET checkpoint=? WHERE id='interrupted'", ("x" * 500_000,))
os._exit(0)
"""
    args = [sys.executable, "-c", crash, str(directory / "kite.sqlite3")]
    subprocess.run(args, check=True, timeout=10)
    assert (directory / "kite.sqlite3-journal").exists()
    assert call("status", "interrupted") == checkpoint
    assert not (directory / "kite.sqlite3-journal").exists()

    subprocess.run(args, check=True, timeout=10)
    assert (directory / "kite.sqlite3-journal").exists()
    result = call("ingest", "--resume", "interrupted")
    assert result["receipt"]["changed"] and result["integrity"]["revisions"] == 1
    assert call("status", "interrupted")["state"] == "completed"


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
    status = call("status", "stale")
    assert not status["snapshot_current"]
    assert status["resume"] == {"state": "blocked", "reason": "stale_snapshot", "stages": []}
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
    assert call("status", "maintain", repo=repo)["resume"]["reason"] == "stale_definition"
    call("stage", "maintain", "verify", repo=repo, error="stale_definition")


def test_tool_version_only_invalidates_workflows_that_use_it(work, monkeypatch):
    _, call = work
    call("start", "topic-tracing", "--run-id", "trace")
    call("start", "memory-ingestion", "--run-id", "ingest")
    tool = operations.TOOLS["report"]
    monkeypatch.setitem(operations.TOOLS, "report", replace(tool, version=tool.version + 1))
    assert call("status", "trace")["resume"]["reason"] == "stale_definition"
    assert call("status", "ingest")["resume"]["state"] == "available"


def test_schema_changes_only_invalidate_workflows_that_bind_the_schema(work, monkeypatch):
    _, call = work
    call("start", "timeline-reconstruction", "--run-id", "timeline")
    call("start", "memory-ingestion", "--run-id", "import")
    schema = operations.SCHEMAS["source"]
    monkeypatch.setitem(operations.SCHEMAS, "source", {**schema, "description": "new contract"})
    assert call("status", "timeline")["resume"]["state"] == "available"
    assert call("status", "import")["resume"]["reason"] == "stale_definition"


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


def test_topic_reclassification_preserves_evidence_and_rejects_cycles(work):
    _, call = work
    source = text_input("launch", "The kickoff checklist is approved.", topic="onboarding")
    source["annotations"]["taxonomy"] = [
        {"name": "onboarding", "aliases": ["kickoff"], "parents": ["growth"]},
        {"name": "checklist", "parents": ["onboarding"]},
        {"name": "product", "aliases": ["product team"]},
    ]
    source["annotations"]["facts"][0]["topics"] = ["checklist"]
    original = ingest(call, "launch", source)
    before = trace(call, "old-parent", {"topics": ["growth"]})["timeline"][0]

    def move(run, topic, parents, error=None):
        call(
            "start",
            "topic-reclassification",
            "--run-id",
            run,
            payload={"move": {"topic": topic, "parents": parents}},
        )
        receipt = call("stage", run, "commit", error=error)
        if error is None:
            call("stage", run, "verify")
            return receipt["value"]

    receipt = move("move", "kickoff", ["product team"])
    assert receipt["changed"]
    assert not move("no-op", "onboarding", ["product"])["changed"]
    for topic in ("product", "kickoff"):
        row = trace(call, f"by-{topic}", {"topics": [topic]})["timeline"][0]
        assert row["fact_id"] == before["fact_id"] and row["evidence"] == before["evidence"]
    assert trace(call, "no-old-parent", {"topics": ["growth"]})["matched_count"] == 0
    assert call("sources")["sources"][0]["current_checksum"] == original["checksum"]

    move("cycle", "product", ["checklist"], error="integrity_error")
    move("missing", "kickoff", ["absent"], error="invalid_input")
    assert call("integrity")["memory_version"] == receipt["memory_version"]
    assert move("root", "kickoff", [])["changed"]
    assert trace(call, "root-read", {"topics": ["kickoff"]})["matched_count"] == 1
    assert trace(call, "no-new-parent", {"topics": ["product"]})["matched_count"] == 0


def test_topic_hierarchy_survives_merge_and_prunes_out_of_scope_parents(work):
    _, call = work
    child = text_input("child", "Approved checklist", topic="checklist")
    child["annotations"]["taxonomy"] = [{"name": "checklist", "parents": ["growth"]}]
    ingest(call, "child", child)
    for topic in ("product", "release"):
        parent = text_input(topic, f"{topic} note", topic=topic)
        parent["annotations"] = {"facts": [], "taxonomy": [{"name": topic}]}
        ingest(call, topic, parent)
    call(
        "start",
        "topic-reclassification",
        "--run-id",
        "move",
        payload={"move": {"topic": "checklist", "parents": ["product"]}},
    )
    call("stage", "move", "commit")
    call(
        "start",
        "memory-consolidation",
        "--run-id",
        "merge",
        payload={"merge": {"from_topic": "product", "to_topic": "release"}},
    )
    call("stage", "merge", "commit")
    assert trace(call, "merged", {"topics": ["release"]})["matched_count"] == 1

    snapshot = ET.fromstring(call("codebook", "--source", "text", "--conversation-id", "child"))
    assert snapshot.find(".//topic[@code='checklist']").get("parents") == ""
    assert snapshot.find(".//topic[@code='release']") is None
    _delete_source(call, "remove", {"source": "text", "conversation_id": "release"})
    assert call("integrity")["revisions"] == 2
    assert trace(call, "child-stays", {"topics": ["checklist"]})["matched_count"] == 1
    assert trace(call, "parent-removed", {"topics": ["release"]})["unresolved_topics"] == [
        "release"
    ]


def test_bounded_history_applies_only_corrections_through_the_selected_day(work):
    _, call = work
    previous = None
    for name, source_date, event_date, topic in (
        ("Alice", "2026-03-20", "2026-03-10", "launch"),
        ("Bob", "2026-06-05", "2026-04-10", "handover"),
        ("Rae", "2026-07-01", "2026-06-20", "handover"),
    ):
        payload = text_input(name, name, date=source_date, topic=topic)
        fact = payload["annotations"]["facts"][0]
        fact["event_date"] = event_date
        if previous:
            fact["supersedes"] = previous
        previous = ingest(call, f"import-{name}", payload)["revision_id"] + "F1"

    pipeline = "timeline-reconstruction"
    query = {"state_key": "owner", "end_date": "2026-04-30"}
    old = trace(call, "reported-by-april", query, pipeline)
    assert old["end_date"] == "2026-04-30"
    assert old["current_reported_states"]["owner"]["value"] == "Alice"
    assert old["timeline"][0]["effective"]
    by_event = trace(call, "events-by-april", {**query, "time_axis": "event"}, pipeline)
    assert by_event["current_reported_states"]["owner"]["value"] == "Bob"
    assert [row["effective"] for row in by_event["timeline"]] == [False, True]
    limited_scope = trace(
        call, "through-june", {"topics": ["launch"], "end_date": "2026-06-05"}, pipeline
    )
    assert not limited_scope["timeline"][0]["effective"]
    assert "owner" not in limited_scope["current_reported_states"]
    now = trace(call, "current", {"state_key": "owner"}, pipeline)
    assert now["current_reported_states"]["owner"]["value"] == "Rae"
    assert [row["effective"] for row in now["timeline"]] == [False, False, True]


def test_undated_correction_outside_the_filter_makes_only_its_state_partial(work):
    _, call = work
    original = text_input("initial", "Alice", date="2026-03-01", topic="launch")
    original["annotations"]["facts"][0]["event_date"] = "2026-02-26"
    original["annotations"]["facts"].append(
        {
            "content": "planning",
            "segment_ids": ["text-1"],
            "topics": ["launch"],
            "state_key": "phase",
            "state": "planning",
            "status": "confirmed",
            "event_date": "2026-02-26",
        }
    )
    predecessor = ingest(call, "initial", original)["revision_id"] + "F1"
    later = text_input("uncertain", "Bob", topic="handover")
    later["annotations"]["facts"][0].update(
        supersedes=predecessor, event_date="2026-04-30", event_date_basis="inferred"
    )
    ingest(call, "undated", later)

    for axis in ("source", "event"):
        query = {"topics": ["launch"], "end_date": "2026-03-31", "time_axis": axis}
        report = trace(call, f"bounded-{axis}", query, "timeline-reconstruction")
        assert report["omitted_unknown_time"] == report["omitted_inferred_time"] == 0
        assert report["current_reported_states"]["phase"]["value"] == "planning"
        assert report["current_reported_states"]["owner"]["status"] == "partial"
        assert report["current_reported_states"]["owner"]["value"] is None
        rows = {row["state_key"]: row for row in report["timeline"]}
        assert rows["owner"]["supersession_uncertain"]
        assert not rows["phase"]["supersession_uncertain"]

    latest = trace(call, "current", {"topics": ["launch"]})
    assert "owner" not in latest["current_reported_states"]


def test_same_source_amendment_keeps_citations_and_is_idempotent(work):
    directory, call = work
    quote = "Maya will observe the launch. Rae owns it. Review happens on Tuesday."
    original = text_input("amend-launch", quote, date="2026-11-01", topic="launch")
    original["annotations"]["facts"][0].update(content="Maya owns launch", state="Maya")
    old = ingest(call, "original", original)
    target = old["revision_id"] + "F1"
    source_version = old["memory_version"]
    amendment = copy.deepcopy(original)
    amendment["annotations"]["facts"] = [
        {
            "content": "Rae owns launch",
            "state_key": "owner",
            "state": "Rae",
            "segment_ids": ["text-1"],
            "topics": ["launch"],
            "status": "confirmed",
            "supersedes": target,
        },
        {
            "content": "Launch review happens on Tuesday",
            "segment_ids": ["text-1"],
            "topics": ["launch"],
        },
    ]
    ingest(call, "missing-checksum", amendment, pipeline="memory-update", error="conflict")
    amendment["source"]["expected_checksum"] = "0" * 64
    ingest(call, "wrong-checksum", amendment, pipeline="memory-update", error="conflict")
    amendment["source"]["expected_checksum"] = old["checksum"]
    assert not ingest(call, "ordinary-duplicate", amendment)["changed"]

    invalid = copy.deepcopy(amendment)
    invalid["annotations"]["taxonomy"] = [
        {"name": "launch", "parents": ["cycle"]},
        {"name": "cycle", "parents": ["launch"]},
    ]
    ingest(call, "invalid", invalid, pipeline="memory-update", error="integrity_error")
    assert call("integrity")["facts"] == 1
    assert call("integrity")["memory_version"] == source_version

    updated = ingest(call, "correction", amendment, pipeline="memory-update")
    assert updated["changed"] and updated["memory_version"] == source_version + 1
    assert (updated["revision_id"], updated["checksum"]) == (old["revision_id"], old["checksum"])
    assert not ingest(call, "amendment-replay", amendment, pipeline="memory-update")["changed"]
    history = call("source-history", "text", "amend-launch")
    assert history["current_checksum"] == old["checksum"]
    assert history["memory_version"] == updated["memory_version"]
    assert len(history["revisions"]) == 1 and history["revisions"][0]["facts"] == 3
    assert call("integrity")["memory_version"] == updated["memory_version"]
    result = trace(call, "after-amendment", {"topics": ["launch"]})
    assert result["matched_count"] == 3
    by_content = {row["content"]: row for row in result["timeline"]}
    assert by_content["Maya owns launch"]["fact_id"] == target
    assert not by_content["Maya owns launch"]["effective"]
    assert by_content["Rae owns launch"]["effective"]
    assert result["current_reported_states"]["owner"]["value"] == "Rae"
    for row in result["timeline"]:
        evidence = row["evidence"][0]
        assert (evidence["segment_id"], evidence["text"]) == ("text-1", quote)
    assert call("integrity")["source_lines"] == 1
    receipt = _delete_source(
        call, "erase-amended", {"source": "text", "conversation_id": "amend-launch"}
    )
    assert receipt["deleted_revisions"] == 1
    assert call("integrity")["facts"] == 0
    assert quote.encode() not in (directory / "kite.sqlite3").read_bytes()
    call(
        "start",
        "memory-update",
        "--run-id",
        "deleted-amendment",
        payload=amendment,
        error="deleted_source",
    )


def test_same_source_amendments_guard_competing_corrections(work):
    _, call = work
    original = text_input("same-log", "Original source evidence", date="2026-11-01")
    old = ingest(call, "original", original)
    corrections = {}
    for name in ("first", "second"):
        payload = copy.deepcopy(original)
        payload["source"]["expected_checksum"] = old["checksum"]
        payload["annotations"]["facts"][0].update(
            content=name, state=name, supersedes=old["revision_id"] + "F1"
        )
        corrections[name] = payload
        call("start", "memory-update", "--run-id", name, payload=payload)
        call("stage", name, "normalize")
        call("stage", name, "annotate")
    call("stage", "first", "commit")
    call("stage", "second", "commit", error="stale_snapshot")
    ingest(call, "second-retry", corrections["second"], pipeline="memory-update", error="conflict")
    assert call("integrity")["facts"] == 2


def test_pending_import_reuses_artifacts_after_interleaved_commits(work):
    _, call = work
    call("start", "memory-ingestion", "--run-id", "first", payload=fixture("first-meeting"))
    normalized = call("stage", "first", "normalize")["manifest"]
    annotated = call("stage", "first", "annotate")["manifest"]
    other = ingest(call, "second", fixture("second-meeting"))
    status = call("status", "first")
    assert not status["snapshot_current"]
    assert status["resume"]["state"] == "available"
    assert status["resume"]["stages"][0]["id"] == "commit"
    assert call("artifact", "first", "normalize")["manifest"] == normalized
    assert call("artifact", "first", "annotate")["manifest"] == annotated
    committed = call("stage", "first", "commit")
    assert committed["value"]["memory_version"] == other["memory_version"] + 1

    latest = ingest(call, "third", text_input("third", "A third conversation"))
    assert call("stage", "first", "commit")["manifest"] == committed["manifest"]
    verified = call("stage", "first", "verify")
    assert verified["value"]["revisions"] == 3
    assert verified["value"]["memory_version"] == latest["memory_version"]
    status = call("status", "first")
    assert status["resume"]["state"] == "complete"
    assert status["stages"]["annotate"]["attempts"] == status["stages"]["commit"]["attempts"] == 1


def test_interleaved_import_preserves_source_conflict_and_update_guards(work):
    _, call = work
    original = text_input("same", "Original record")
    different = text_input("same", "Different record")
    for run, payload in (("duplicate", original), ("conflicting", different)):
        call("start", "memory-ingestion", "--run-id", run, payload=payload)
        call("stage", run, "normalize")
        call("stage", run, "annotate")

    receipt = ingest(call, "original", original)
    duplicate = call("stage", "duplicate", "commit")["value"]
    assert duplicate == {**receipt, "changed": False}
    call("stage", "conflicting", "commit", error="conflict")
    different["source"]["expected_checksum"] = receipt["checksum"]
    call("start", "memory-ingestion", "--run-id", "update", payload=different)
    call("stage", "update", "normalize")
    call("stage", "update", "annotate")
    ingest(call, "new", text_input("new", "Another record"))
    call("stage", "update", "commit", error="stale_snapshot")
    assert call("integrity")["facts"] == 2


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
    status = call("status", "delete")
    assert status["resume"]["stages"][0]["required_flags"] == ["--approve"]
    unfinished = call("runs", "--unfinished")
    assert [(run["run_id"], run["state"]) for run in unfinished["runs"]] == [
        ("delete", "awaiting_approval")
    ]
    assert plan["approval_token"] not in json.dumps([status, unfinished])
    call("stage", "delete", "apply", "--approve", "wrong", error="permission_denied")
    receipt = call("stage", "delete", "apply", "--approve", plan["approval_token"])
    assert receipt["value"]["deleted_revisions"] == 1
    assert call("stage", "delete", "apply") == receipt
    status = call("status", "old-report")
    assert status["state"] == "invalidated"
    assert status["resume"]["reason"] == "invalidated_run"
    assert call("status", "delete")["resume"]["state"] == "complete"
    assert call("runs", "--unfinished")["runs"] == []
    call("artifact", "old-report", "report", error="invalidated_run")
    call("artifact", "delete", "preview", error="not_found")
    assert b"private-celadon-body" not in (directory / "kite.sqlite3").read_bytes()
    assert call("deletion-audit")["deletions"][0]["fact_count"] == 1
    assert trace(call, "after", {"topics": ["onboarding"]})["matched_count"] == 3
    call(
        "start", "memory-ingestion", "--run-id", "replayed", payload=private, error="deleted_source"
    )


@pytest.mark.parametrize(
    "target",
    [
        {"source": "text", "conversation_ids": []},
        {"source": "text", "conversation_ids": ["one", "one"]},
        {"source": "text", "conversation_id": "one", "conversation_ids": ["two"]},
        {"source": "text", "conversation_ids": [str(n) for n in range(101)]},
    ],
)
def test_batch_deletion_rejects_invalid_selectors(work, target):
    _, call = work
    call("start", "memory-deletion", payload={"target": target}, error="invalid_input")


def test_batch_deletion_targets_exact_ids_and_keeps_unrelated_records(work):
    directory, call = work
    first = text_input("remove-a", "private-marigold-a")
    second = text_input("remove-b", "private-marigold-b")
    staged = text_input("staged", "private-marigold-staged")
    other = text_input("keep", "retained same source")
    for name, payload in (("one", first), ("two", second), ("other", other)):
        ingest(call, name, payload)
    foreign = copy.deepcopy(first)
    foreign["source"] = {
        "format": "transcript",
        "payload": {
            "conversation_id": "remove-a",
            "metadata": {"source_reference": "remove-a"},
            "segments": [{"segment_id": "text-1", "text": "retained other source"}],
        },
    }
    foreign["annotations"]["facts"][0].update(content="retained other source", state="foreign")
    ingest(call, "foreign", foreign)
    call("start", "memory-ingestion", "--run-id", "staged", payload=staged)
    call("stage", "staged", "normalize")

    call(
        "start",
        "memory-deletion",
        "--run-id",
        "single",
        payload={"target": {"source": "text", "conversation_id": "remove-a"}},
    )
    single_token = call("stage", "single", "preview")["value"]["approval_token"]
    selector = {"source": "text", "conversation_ids": ["staged", "remove-b", "remove-a"]}
    call("start", "memory-deletion", "--run-id", "batch", payload={"target": selector})
    plan = call("stage", "batch", "preview")["value"]
    assert plan["target"]["conversation_ids"] == ["remove-a", "remove-b", "staged"]
    assert plan["conversation_ids_without_revisions"] == ["staged"]
    assert plan["staged_conversation_ids"] == ["staged"]
    assert (plan["revisions"], plan["facts"]) == (2, 2)
    call("stage", "batch", "apply", error="awaiting_approval")
    call("stage", "batch", "apply", "--approve", single_token, error="permission_denied")

    result = call("stage", "batch", "apply", "--approve", plan["approval_token"])
    assert result["value"]["deleted_revisions"] == 2
    assert call("stage", "batch", "apply") == result
    call("artifact", "staged", "normalize", error="invalidated_run")
    call("artifact", "batch", "preview", error="not_found")
    for name, payload in (("one", first), ("two", second), ("staged", staged)):
        call(
            "start",
            "memory-ingestion",
            "--run-id",
            f"replay-{name}",
            payload=payload,
            error="deleted_source",
        )
    alternate = copy.deepcopy(first)
    alternate["source"]["payload"]["conversation_id"] = "renamed"
    call(
        "start",
        "memory-ingestion",
        "--run-id",
        "renamed",
        payload=alternate,
        error="deleted_source",
    )
    call("start", "memory-ingestion", "--run-id", "late")
    alternate["source"]["payload"]["conversation_id"] = "remove-a"
    alternate["source"]["payload"]["metadata"]["source_reference"] = "other-reference"
    call(
        "stage",
        "late",
        "normalize",
        payload={"source": alternate["source"]},
        error="deleted_source",
    )
    assert call("status", "late")["stages"]["normalize"]["attempts"] == 0
    expected = {("text", "keep"), ("transcript", "remove-a")}
    sources = call("sources")["sources"]
    assert {(row["source"], row["conversation_id"]) for row in sources} == expected
    report = trace(call, "remaining", {"topics": ["onboarding"]})
    assert {
        (row["evidence"][0]["source"], row["evidence"][0]["conversation_id"])
        for row in report["timeline"]
    } == expected
    assert b"private-marigold" not in (directory / "kite.sqlite3").read_bytes()
    audit = call("deletion-audit")["deletions"]
    assert len(audit) == 1 and (audit[0]["revision_count"], audit[0]["fact_count"]) == (2, 2)
    assert "remove-a" not in json.dumps(audit)


def test_upgrade_scrubs_legacy_reimport_artifacts_before_serving_them(work):
    from memoket_kite.tools.contracts import digest

    directory, call = work
    for run_id, text in (("deleted", "legacy-private-ginger"), ("keep", "unrelated note")):
        payload = text_input(run_id, text)
        call("start", "memory-ingestion", "--run-id", run_id, payload=payload)
        call("stage", run_id, "normalize")
    path = directory / "kite.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO tombstones VALUES('conversation', ?)", (digest(["text", "deleted"]),)
        )
        db.execute("PRAGMA user_version=0")
    assert b"legacy-private-ginger" in path.read_bytes()
    call("artifact", "deleted", "normalize", error="invalidated_run")
    assert b"legacy-private-ginger" not in path.read_bytes()
    assert (
        call("artifact", "keep", "normalize")["value"]["conversation"]["conversation_id"] == "keep"
    )


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
    call(
        "start", "memory-ingestion", "--run-id", "late-import", payload=data, error="deleted_source"
    )


def test_source_deletion_tombstones_uncommitted_ids_and_references(work):
    directory, call = work
    ingest(call, "committed", text_input("committed", "stored target"))
    pending = text_input("pending", "pending target")
    staged = text_input("staged", "staged target")
    staged["source"]["payload"]["metadata"]["source_reference"] = "separate-source-reference"
    for run, source in (("pending", pending), ("staged", staged)):
        call("start", "memory-ingestion", "--run-id", run, payload=source)
    call("stage", "staged", "normalize")

    foreign = text_input("staged", "retained source")
    foreign["source"] = {
        "format": "conversation",
        "payload": {
            "source": "archive",
            "conversation_id": "staged",
            "metadata": {"source_reference": "separate-source-reference"},
            "segments": [{"segment_id": "text-1", "text": "retained source"}],
        },
    }
    ingest(call, "archive", foreign)
    call(
        "start",
        "memory-deletion",
        "--run-id",
        "delete-source",
        payload={"target": {"source": "text"}},
    )
    plan = call("stage", "delete-source", "preview")["value"]
    assert plan["staged_conversation_ids"] == ["pending", "staged"]
    assert (plan["revisions"], plan["facts"]) == (1, 1)
    assert "separate-source-reference" not in json.dumps(plan)
    call("stage", "delete-source", "apply", "--approve", plan["approval_token"])
    assert not call("sources", "--source", "text")["sources"]
    assert call("sources", "--source", "archive")["sources"][0]["conversation_id"] == "staged"
    assert b"staged target" not in (directory / "kite.sqlite3").read_bytes()
    for run, source in (("retry-pending", pending), ("retry-staged", staged)):
        call("start", "memory-ingestion", "--run-id", run, payload=source, error="deleted_source")
    staged["source"]["payload"]["conversation_id"] = "renamed"
    call("start", "memory-ingestion", "--run-id", "renamed", payload=staged, error="deleted_source")
    pending["source"]["payload"]["metadata"]["source_reference"] = "new-reference"
    call(
        "start", "memory-ingestion", "--run-id", "rebound", payload=pending, error="deleted_source"
    )
    assert ingest(call, "new", text_input("new", "new conversation"))["changed"]


@pytest.mark.parametrize("change", ["new-run", "new-artifact"])
def test_deletion_preview_rejects_changed_workflow_impact_without_memory_write(work, change):
    _, call = work
    ingest(call, "remove", text_input("remove", "delete this"))
    pending = text_input("pending", "work in progress")
    if change == "new-artifact":
        call("start", "memory-ingestion", "--run-id", "pending", payload=pending)
    target = {"target": {"source": "text", "conversation_id": "remove"}}
    call("start", "memory-deletion", "--run-id", "old", payload=target)
    old = call("stage", "old", "preview")["value"]
    if change == "new-artifact":
        call("stage", "pending", "normalize")
    else:
        call("start", "memory-ingestion", "--run-id", "pending", payload=pending)

    unchanged = call("integrity")
    assert unchanged["memory_version"] == old["memory_version"]
    call("stage", "old", "apply", "--approve", old["approval_token"], error="stale_preview")
    assert call("integrity") == unchanged
    assert call("deletion-audit")["deletions"] == []
    assert call("status", "old")["resume"]["reason"] == "stale_preview"

    call("start", "memory-deletion", "--run-id", "fresh")
    fresh = call("stage", "fresh", "preview", payload=target)["value"]
    assert fresh["runs_to_invalidate"] == old["runs_to_invalidate"] + 1 + (change == "new-run")
    assert fresh["run_artifacts_to_purge"] == (
        old["run_artifacts_to_purge"] + 1 + (change == "new-artifact")
    )
    call("stage", "fresh", "apply", "--approve", old["approval_token"], error="permission_denied")
    receipt = call("stage", "fresh", "apply", "--approve", fresh["approval_token"])
    assert receipt["value"]["deleted_revisions"] == 1


def test_verified_backup_resumes_runs_and_preserves_deletions(work, tmp_path):
    _, call = work
    gone = text_input("gone", "deleted content")
    ingest(call, "gone", gone)
    _delete_source(call, "remove", {"source": "text", "conversation_id": "gone"})
    ingest(call, "keep", text_input("keep", "keep this evidence"))
    draft = text_input("draft", "save this checkpoint")
    call("start", "memory-ingestion", "--run-id", "draft", payload=draft)
    call("stage", "draft", "normalize")
    target = {"source": "text", "conversation_id": "keep"}
    call("start", "memory-deletion", "--run-id", "pending-delete", payload={"target": target})
    original_token = call("stage", "pending-delete", "preview")["value"]["approval_token"]
    original = call("integrity")

    destination = tmp_path / "snapshot"
    receipt = call("backup", str(destination))
    assert receipt["memory_version"] == original["memory_version"]
    assert receipt["revisions"] == receipt["invalidated_approvals"] == 1
    assert (receipt["verified_runs"], receipt["verified_artifacts"]) == (5, 7)
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o700
        assert (destination / "kite.sqlite3").stat().st_mode & 0o777 == 0o600
    assert call("integrity", workspace_dir=destination) == original
    assert call("deletion-audit", workspace_dir=destination) == call("deletion-audit")
    assert call("status", "pending-delete", workspace_dir=destination)["state"] == "invalidated"
    call(
        "artifact", "pending-delete", "preview", workspace_dir=destination, error="invalidated_run"
    )
    assert (
        call("artifact", "pending-delete", "preview")["value"]["approval_token"] == original_token
    )
    call(
        "start",
        "memory-deletion",
        "--run-id",
        "new-preview",
        payload={"target": target},
        workspace_dir=destination,
    )
    copy_token = call("stage", "new-preview", "preview", workspace_dir=destination)["value"][
        "approval_token"
    ]
    assert copy_token != original_token
    call(
        "stage",
        "new-preview",
        "apply",
        "--approve",
        original_token,
        workspace_dir=destination,
        error="permission_denied",
    )

    status = call("status", "draft", workspace_dir=destination)
    assert status["resume"]["stages"][0]["id"] == "annotate"
    for stage in ("annotate", "commit", "verify"):
        call("stage", "draft", stage, workspace_dir=destination)
    assert call("integrity", workspace_dir=destination)["revisions"] == 2
    assert call("integrity") == original
    call(
        "start",
        "memory-ingestion",
        "--run-id",
        "replay",
        payload=gone,
        workspace_dir=destination,
        error="deleted_source",
    )


def test_backup_rejects_conflicts_and_cleans_failed_copies(work, tmp_path):
    directory, call = work
    output = tmp_path / "backup"
    call("backup", str(output), error="not_found")
    assert not directory.exists() and not output.exists()
    ingest(call, "keep", text_input("keep", "keep"))
    output.mkdir()
    sentinel = output / "original.txt"
    sentinel.write_text("existing output")
    call("backup", str(output), error="conflict")
    assert sentinel.read_text() == "existing output"
    broken = tmp_path / "broken"
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE memory SET xml=?", (b"corrupt Codebook",))
    call("backup", str(broken), error="integrity_error")
    assert not broken.exists()


def test_guarded_backup_rejects_history_missing_deletions(work, tmp_path):
    directory, call = work
    removed = text_input("gone", "private removed content")
    ingest(call, "gone", removed)
    kept = ingest(call, "keep", text_input("keep", "Keep this evidence"))
    old = tmp_path / "old"
    call("backup", str(old))
    target = {"source": "text", "conversation_id": "gone"}
    _delete_source(call, "remove", target)

    candidate = tmp_path / "candidate"
    guard = ("--require-deletions-from", str(directory))
    old_db = old / "kite.sqlite3"
    before = old_db.read_bytes()
    assert call("source-history", "text", "gone", workspace_dir=old)["revisions"]
    failure = call("backup", str(candidate), *guard, workspace_dir=old, error="stale_backup")
    assert "private removed content" not in json.dumps(failure)
    assert not candidate.exists() and old_db.read_bytes() == before

    first_safe_copy = tmp_path / "first-safe"
    result = call("backup", str(first_safe_copy), *guard)
    assert result["checked_tombstones"] == 2 and result["checked_deletion_receipts"] == 1
    _delete_source(call, "remove-again", target)
    # The earlier copy has both tombstones, but lacks the second receipt.
    call("backup", str(candidate), *guard, workspace_dir=first_safe_copy, error="stale_backup")
    assert not candidate.exists()

    current = tmp_path / "current"
    call("backup", str(current))
    result = call("backup", str(candidate), *guard, workspace_dir=current)
    assert result["checked_tombstones"] == result["checked_deletion_receipts"] == 2
    assert call("deletion-audit", workspace_dir=candidate) == call("deletion-audit")
    assert (
        call("source-history", "text", "keep", workspace_dir=candidate)["current_checksum"]
        == kept["checksum"]
    )
    call("source-history", "text", "gone", workspace_dir=candidate, error="not_found")
    call(
        "start",
        "memory-ingestion",
        "--run-id",
        "replay",
        payload=removed,
        workspace_dir=candidate,
        error="deleted_source",
    )

    missing = tmp_path / "missing"
    output = tmp_path / "failed"
    call("backup", str(output), "--require-deletions-from", str(missing), error="not_found")
    assert not missing.exists() and not output.exists()


def test_guarded_backup_pins_deletion_authority_through_publication(work, tmp_path, monkeypatch):
    directory, call = work
    call("integrity")
    source = tmp_path / "source"
    call("backup", str(source))
    database = directory / "kite.sqlite3"
    output = tmp_path / "published"
    link = os.link

    def publish(staging, destination):
        with closing(sqlite3.connect(database, timeout=0)) as writer:
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                writer.execute("BEGIN EXCLUSIVE")
        link(staging, destination)

    monkeypatch.setattr(workspace.os, "link", publish)
    guard = ("--require-deletions-from", str(directory))
    assert call("backup", str(output), *guard, workspace_dir=source)["checked_tombstones"] == 0
    with closing(sqlite3.connect(database, timeout=0)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.commit()
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    rejected = tmp_path / "rejected"
    call("backup", str(rejected), *guard, workspace_dir=source, error="invalid_input")
    assert not rejected.exists()


@pytest.mark.parametrize(
    "damage", ["input", "payload", "missing", "orphan", "foreign_key", "operation", "table"]
)
def test_workflow_integrity_and_backup_reject_broken_run_storage(work, tmp_path, damage):
    directory, call = work
    payload = text_input("draft", "private test transcript")
    call("start", "memory-ingestion", "--run-id", "draft", payload=payload)
    call("stage", "draft", "normalize")
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        if damage == "input":
            db.execute("UPDATE runs SET inputs='{}'")
        elif damage == "payload":
            db.execute("UPDATE artifacts SET value='{}'")
        elif damage == "missing":
            db.execute("DELETE FROM artifacts")
        elif damage == "orphan":
            db.execute(
                "INSERT INTO artifacts SELECT run_id, 'orphan', kind, digest, value FROM artifacts"
            )
        elif damage == "foreign_key":
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("UPDATE artifacts SET run_id='missing-run'")
        elif damage == "table":
            db.execute("DROP TABLE artifacts")
        else:
            checkpoint = json.loads(db.execute("SELECT checkpoint FROM runs").fetchone()[0])
            checkpoint["stages"]["normalize"]["artifact"]["operation_id"] = "0" * 64
            db.execute("UPDATE runs SET checkpoint=?", (json.dumps(checkpoint),))
    before = database.read_bytes()
    full = call("integrity", "--include-workflows", error="integrity_error")
    assert "private test transcript" not in json.dumps(full)
    assert database.read_bytes() == before
    assert call("integrity")["revisions"] == 0
    destination = tmp_path / "rejected"
    result = call("backup", str(destination), error="integrity_error")
    assert "private test transcript" not in json.dumps(result)
    assert not destination.exists()


def test_backup_verifies_stale_trace_artifacts_without_resuming_them(work, tmp_path):
    directory, call = work
    call("integrity", "--include-workflows", error="not_found")
    assert not directory.exists()
    ingest(call, "first", text_input("first", "Initial evidence"))
    trace(call, "old-trace", {"topics": ["onboarding"]})
    ingest(call, "second", text_input("second", "Later evidence"))
    database = directory / "kite.sqlite3"
    before = database.read_bytes()
    full = call("integrity", "--include-workflows")
    assert (full["revisions"], full["verified_runs"], full["verified_artifacts"]) == (2, 3, 11)
    assert database.read_bytes() == before
    destination = tmp_path / "copy"
    result = call("backup", str(destination))
    assert result["verified_runs"] == 3 and result["verified_artifacts"] == 11
    copied = call("status", "old-trace", workspace_dir=destination)
    assert copied["resume"]["reason"] == "stale_snapshot"


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
from queue import Queue
from threading import Event, Thread
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
                    "t": "2026-02-03",
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
    extract_stage = call("status", "model")["resume"]["stages"][0]
    assert (extract_stage["id"], extract_stage["required_flags"]) == (
        "extract",
        ["--allow-provider"],
    )
    call("stage", "model", "extract", error="permission_denied")
    call("stage", "model", "extract", "--allow-provider", error="configuration_error")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-placeholder")
    result = call("stage", "model", "extract", "--allow-provider")
    assert result["value"]["facts"][0]["segment_ids"] == ["text-1"]
    assert result["value"]["facts"][0]["event_date"] == "2026-02-03"
    assert result["value"]["facts"][0]["event_date_basis"] == "inferred"
    assert result["value"]["taxonomy"] == [{"name": "onboarding", "parents": ["general"]}]
    for stage in ("annotate", "commit", "verify"):
        call("stage", "model", stage)


def test_dates_input_and_artifact_integrity(work):
    directory, call = work
    bad = text_input("bad-date", "body")
    bad["source"]["payload"]["started_at"] = "2026-01-01T13:00:00"
    invalid = call(
        "start", "memory-ingestion", "--run-id", "bad", payload=bad, error="invalid_input"
    )
    assert (
        invalid["error"]["message"] == "conversation.started_at fails date-time format validation"
    )
    call("status", "bad", error="not_found")
    good = text_input("good", "body")
    call("start", "memory-ingestion", "--run-id", "bad", payload=good)
    call("stage", "bad", "normalize")
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE artifacts SET value='{}'")
    call("artifact", "bad", "normalize", error="integrity_error")
    if os.name == "posix":
        directory.chmod(0o755)
        call("integrity", error="permission_denied")


def test_ingest_validation_locates_fields_without_echoing_source_data(work):
    _, call = work
    payload = text_input("review", "private transcript marker")
    metadata = payload["source"]["payload"]["metadata"]
    del metadata["source_reference"]
    failure = call("ingest", "--run-id", "repair", payload=payload, error="invalid_input")
    assert failure["error"]["message"] == "conversation.metadata requires source_reference"
    metadata["source_reference"] = "private source marker"
    facts = payload["annotations"]["facts"]
    facts.append({**facts[0], "event_date": "private date marker"})
    failure = call("ingest", "--run-id", "repair", payload=payload, error="invalid_input")
    assert failure["error"]["message"] == (
        "input annotations.facts[1].event_date fails date format validation"
    )
    del facts[1]["event_date"]
    facts[1]["private field marker"] = "private value marker"
    failure = call("ingest", "--run-id", "repair", payload=payload, error="invalid_input")
    assert failure["error"]["message"] == "input annotations.facts[1] contains unsupported fields"
    assert "private" not in json.dumps(failure)
    call("status", "repair", error="not_found")


def test_validation_masks_keys_in_open_maps():
    with pytest.raises(ToolError) as exc:
        validate(
            {"type": "object", "additionalProperties": {"type": "integer"}},
            {"private key": "private value"},
            "mapping",
        )
    assert str(exc.value) == "mapping[*] fails type validation"


def test_late_source_validation_preserves_pending_checkpoint(work):
    directory, call = work
    marker = "synthetic-rejected-source-text"
    raw = {
        "id": 51,
        "segments": [{"sentences": [{"line_id": "line-a", "text": marker, "time": 1}]}],
    }
    source = {"format": "memoket", "payload": raw}
    call("start", "memory-ingestion", "--run-id", "pending")
    before = call("status", "pending")
    raw["date"] = "2026-02-30"
    result = call(
        "stage", "pending", "normalize", payload={"source": source}, error="invalid_input"
    )
    assert marker not in json.dumps(result)
    assert call("status", "pending") == before
    assert marker.encode() not in (directory / "kite.sqlite3").read_bytes()
    raw["date"] = "2026-02-28"
    accepted = call("stage", "pending", "normalize", payload={"source": source})
    assert accepted["checkpoint"]["stages"]["normalize"]["attempts"] == 1
    conversation = accepted["value"]["conversation"]
    assert conversation["conversation_id"] == "51"
    assert conversation["segments"][0]["text"] == marker


def test_ingest_command_commits_and_verifies_prepared_annotations(work):
    _, call = work
    payload = text_input("review", "Synthetic original evidence.")
    call("ingest", payload={"source": payload["source"]}, error="invalid_input")
    result = call("ingest", "--run-id", "prepared", payload=payload)
    assert result["run_id"] == "prepared" and result["receipt"]["changed"]
    assert result["integrity"]["revisions"] == result["integrity"]["facts"] == 1
    assert "Synthetic original evidence" not in json.dumps(result)
    assert call("status", "prepared")["state"] == "completed"
    call("ingest", "--run-id", "prepared", payload=payload, error="conflict")
    duplicate = call("ingest", payload=payload)
    assert not duplicate["receipt"]["changed"]
    assert duplicate["integrity"] == result["integrity"]


def test_ingest_command_rejects_a_different_write_tool(work, tmp_path):
    _, call = work
    repo = tmp_path / "alternate-workflow"
    for name in ("schemas", "skills", "pipeline_defs"):
        shutil.copytree(ROOT / name, repo / name)
    definition = repo / "pipeline_defs" / "memory-ingestion.yaml"
    definition.write_text(definition.read_text().replace("tool: commit", "tool: amend"))
    call("validate-pipeline", "memory-ingestion", repo=repo)
    call(
        "ingest",
        "--run-id",
        "override",
        repo=repo,
        error="invalid_pipeline",
        payload=text_input("review", "Synthetic original evidence."),
    )
    call("status", "override", error="not_found")


@pytest.mark.parametrize(
    ("failed_stage", "tool_name"), [("commit", "commit"), ("verify", "validate_commit")]
)
def test_ingest_command_stops_and_keeps_completed_stages_for_recovery(
    work, monkeypatch, failed_stage, tool_name
):
    _, call = work
    payload = text_input("review", "Synthetic original evidence.")
    tool = operations.TOOLS[tool_name]

    def fail_stage(context, **kwargs):
        raise ToolError("integrity_error", "synthetic stage failure")

    with monkeypatch.context() as patch:
        patch.setitem(operations.TOOLS, tool_name, replace(tool, function=fail_stage))
        result = call("ingest", "--run-id", "retry", payload=payload, error="integrity_error")
    assert (result["run_id"], result["stage_id"], result["stage_state"]) == (
        "retry",
        failed_stage,
        "failed",
    )
    stages = call("status", "retry")["stages"]
    assert stages["normalize"]["state"] == stages["annotate"]["state"] == "completed"
    if failed_stage == "commit":
        assert "receipt" not in result and stages["verify"]["state"] == "pending"
        assert call("integrity")["revisions"] == 0
    else:
        assert result["receipt"]["changed"] and stages["commit"]["state"] == "completed"
        assert call("integrity")["revisions"] == 1
    resumed = call("ingest", "--resume", "retry")
    assert resumed["receipt"]["changed"] and resumed["integrity"]["revisions"] == 1
    after = call("status", "retry")
    assert after["state"] == "completed" and after["stages"][failed_stage]["attempts"] == 2
    assert after["stages"]["normalize"]["attempts"] == after["stages"]["annotate"]["attempts"] == 1
    assert call("ingest", "--resume", "retry") == resumed
    assert call("status", "retry") == after


def test_ingest_resume_checks_the_run_and_required_inputs(work):
    directory, call = work
    call("ingest", "--resume", "unknown", error="not_found")
    assert not directory.exists()
    call("start", "memory-provider-ingestion", "--run-id", "provider")
    call("ingest", "--resume", "provider", error="invalid_pipeline")
    call("start", "memory-ingestion", "--run-id", "pending")
    before = call("status", "pending")
    call("ingest", "--resume", "pending", "--run-id", "rename", error="invalid_input")
    call("ingest", "--resume", "pending", error="missing_input")
    assert call("status", "pending") == before
    call("ingest", "--run-id", "removed", payload=text_input("removed", "Synthetic note."))
    _delete_source(call, "erase", {"source": "text", "conversation_id": "removed"})
    call("ingest", "--resume", "removed", error="invalidated_run")
    assert call("integrity")["revisions"] == 0


def test_ingest_resume_continues_after_an_interleaved_import(work):
    _, call = work
    call(
        "start",
        "memory-ingestion",
        "--run-id",
        "pending",
        payload=text_input("pending", "Pending."),
    )
    call("stage", "pending", "normalize")
    call("ingest", "--run-id", "other", payload=text_input("other", "Other."))
    result = call("ingest", "--resume", "pending")
    assert result["receipt"]["changed"] and result["integrity"]["revisions"] == 2
    assert call("status", "pending")["stages"]["normalize"]["attempts"] == 1
    call("ingest", "--resume", "other", error="stale_snapshot")


def test_ingest_update_uses_the_current_checksum_and_keeps_original_citations(work):
    _, call = work
    payload = text_input(
        "review", "Ada initially owned the review. Rae owns it now.", date="2026-04-02"
    )
    payload["annotations"]["facts"][0].update(content="Ada owns the review.", state="Ada")
    initial = call("ingest", "--run-id", "initial", payload=payload)["receipt"]
    correction = copy.deepcopy(payload)
    fact = correction["annotations"]["facts"][0]
    fact.update(
        content="Rae owns the review.", state="Rae", supersedes=initial["revision_id"] + "F1"
    )
    rejected = call("ingest", "--update", payload=correction, error="conflict")
    assert rejected["stage_id"] == "commit"
    correction["source"]["expected_checksum"] = "0" * 64
    call("ingest", "--update", payload=correction, error="conflict")
    correction["source"]["expected_checksum"] = initial["checksum"]
    amended = call("ingest", "--update", "--run-id", "corrected", payload=correction)
    assert (
        amended["receipt"]["changed"]
        and amended["receipt"]["revision_id"] == initial["revision_id"]
    )
    assert amended["integrity"]["revisions"] == 1 and amended["integrity"]["facts"] == 2
    assert not call("ingest", "--update", payload=correction)["receipt"]["changed"]
    report = call("query", "--pipeline", "task-tracing", payload={"query": {"state_key": "owner"}})[
        "value"
    ]
    assert report["current_reported_states"]["owner"]["value"] == "Rae"
    assert {row["state"]: row["effective"] for row in report["timeline"]} == {
        "Ada": False,
        "Rae": True,
    }
    assert all(
        row["evidence"][0]["text"] == payload["source"]["payload"]["text"]
        for row in report["timeline"]
    )


def test_ingest_resume_uses_the_saved_update_pipeline(work):
    _, call = work
    payload = text_input("review", "Ada owns the review.", date="2026-04-02")
    original = call("ingest", "--run-id", "original", payload=payload)["receipt"]
    revised = text_input("review", "Rae owns the review.", date="2026-04-03")
    revised["source"]["expected_checksum"] = original["checksum"]
    call("start", "memory-update", "--run-id", "revision", payload=revised)
    for stage in ("normalize", "annotate"):
        call("stage", "revision", stage)
    call("ingest", "--resume", "revision", "--update", error="invalid_input")
    resumed = call("ingest", "--resume", "revision")
    assert resumed["receipt"]["changed"] and resumed["integrity"]["revisions"] == 2
    assert call("status", "revision")["pipeline_id"] == "memory-update"
    assert call("ingest", "--resume", "revision") == resumed
    history = call("source-history", "text", "review")
    assert history["current_checksum"] == resumed["receipt"]["checksum"]
    assert history["revisions"][1]["checksum"] == original["checksum"]


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


def test_event_filter_intersects_project_speaker_and_dates(work):
    _, call = work
    for name, event, project, speaker, day in (
        ("selected", "Release Review", "launch", "dave", "2026-03-10"),
        ("other-event", "Incident Triage", "launch", "dave", "2026-03-11"),
        ("other-speaker", "Release Review", "launch", "rae", "2026-03-12"),
        ("other-project", "Release Review", "infra", "dave", "2026-03-13"),
        ("other-date", "Release Review", "launch", "dave", "2026-04-14"),
    ):
        quote = "Release review was confirmed."
        payload = {
            "source": {
                "format": "transcript",
                "payload": {
                    "conversation_id": name,
                    "source_date": day,
                    "metadata": {"source_reference": name, "project": project},
                    "segments": [{"segment_id": "s1", "speaker_id": speaker, "text": quote}],
                },
            },
            "annotations": {
                "facts": [
                    {
                        "content": quote,
                        "segment_ids": ["s1"],
                        "topics": ["launch"],
                        "events": [event],
                        "state_key": "review",
                        "state": "confirmed",
                        "status": "confirmed",
                    }
                ]
            },
        }
        ingest(call, f"import-{name}", payload)

    pipeline = "timeline-reconstruction"
    query = {
        "events": ["RELEASE-REVIEW"],
        "projects": ["launch"],
        "speakers": ["DAVE"],
        "start_date": "2026-03-01",
        "end_date": "2026-03-31",
    }
    result = trace(call, "event-filter", query, pipeline)
    assert result["resolved_events"] == ["release_review"] and not result["unresolved_events"]
    assert result["matched_count"] == 1
    row = result["timeline"][0]
    assert row["events"] == ["release_review"]
    assert row["evidence"][0]["conversation_id"] == "selected"
    assert row["evidence"][0]["text"] == quote

    report = trace(
        call, "mixed-events", {**query, "events": ["release review", "missing"]}, pipeline
    )
    assert report["matched_count"] == 1 and report["unresolved_events"] == ["missing"]
    assert report["current_reported_states"]["review"]["status"] == "partial"
    empty = trace(call, "unknown-event", {"events": ["missing"]}, pipeline)
    assert not empty["timeline"] and empty["unresolved_events"] == ["missing"]
    alternative = trace(
        call, "either-event", {**query, "events": ["release review", "incident triage"]}, pipeline
    )
    assert alternative["matched_count"] == 2
    assert not alternative["unresolved_events"]


def test_task_trace_reports_unresolved_entities_without_claiming_complete_state(work):
    _, call = work
    dave = text_input("handoff", "David owns the checklist.", date="2026-05-04")
    dave["annotations"]["facts"][0].update(entities=["Dave"], state="David")
    dave["annotations"]["entity_aliases"] = [{"name": "David Huang", "aliases": ["Dave"]}]
    ingest(call, "handoff", dave)
    other = text_input("other", "Rae owns the checklist.", date="2026-05-05")
    other["annotations"]["facts"][0].update(entities=["Rae"], state="Rae")
    ingest(call, "other", other)

    query = {"topics": ["onboarding"], "entities": ["Dave", "David Huang"]}
    complete = trace(call, "complete", query, "task-tracing")
    assert complete["resolved_entities"] == ["david_huang"]
    assert complete["unresolved_entities"] == []
    assert complete["current_reported_states"]["owner"]["value"] == "David"
    followed = trace(call, "follow-alias", {**query, "follow_states": True}, "task-tracing")
    assert followed["followed_state_keys"] == ["owner"]
    assert [row["state"] for row in followed["timeline"]] == ["David", "Rae"]
    assert followed["current_reported_states"]["owner"]["value"] == "Rae"

    either = trace(call, "either", {**query, "entities": ["Dave", "Rae"]}, "task-tracing")
    assert either["matched_count"] == 2
    assert either["resolved_entities"] == ["david_huang", "rae"]
    assert either["current_reported_states"]["owner"]["value"] == "Rae"

    mixed = trace(call, "mixed", {**query, "entities": ["Dave", "Unknown Person"]}, "task-tracing")
    assert mixed["timeline"] == complete["timeline"]
    assert mixed["matched_count"] == 1
    assert mixed["resolved_entities"] == ["david_huang"]
    assert mixed["unresolved_entities"] == ["Unknown Person"]
    assert mixed["current_reported_states"]["owner"] == {
        "status": "partial",
        "value": None,
        "fact_ids": [mixed["timeline"][0]["fact_id"]],
    }

    unknown = trace(call, "unknown", {"entities": ["Unknown Person"]}, "task-tracing")
    assert unknown["resolved_entities"] == []
    assert unknown["unresolved_entities"] == ["Unknown Person"]
    assert unknown["timeline"] == [] and unknown["current_reported_states"] == {}


def test_follow_state_history_preserves_scope_and_pagination(work):
    _, call = work
    for identity, name, day, topic, source, project, key in (
        ("first", "David", "2026-05-04", "checklist", "text", "pilot", "checklist-owner"),
        ("handoff", "Rae", "2026-05-05", "checklist", "text", "pilot", "checklist-owner"),
        ("future", "Future", "2026-05-08", "checklist", "text", "pilot", "checklist-owner"),
        ("private", "Other", "2026-05-06", "checklist", "archive", "pilot", "checklist-owner"),
        ("different", "Other", "2026-05-06", "checklist", "text", "other", "checklist-owner"),
        ("off-topic", "Other", "2026-05-06", "unrelated", "text", "pilot", "checklist-owner"),
        ("different-key", "Other", "2026-05-06", "checklist", "text", "pilot", "different-owner"),
    ):
        payload = text_input(identity, f"{name} owns the task.", date=day, topic=topic)
        raw = payload["source"]["payload"]
        raw["metadata"]["project"] = project
        payload["annotations"]["facts"][0].update(state_key=key, state=name)
        if source != "text":
            payload["source"]["format"] = "conversation"
            raw.update(source=source, segments=[{"segment_id": "text-1", "text": raw.pop("text")}])
        ingest(call, identity, payload)

    query = {
        "text": "David owns",
        "topics": ["checklist"],
        "source": "text",
        "projects": ["pilot"],
        "end_date": "2026-05-07",
        "follow_states": True,
    }
    pipeline = "task-tracing"
    result = trace(call, "follow", query, pipeline)
    assert call("query", "--pipeline", pipeline, payload={"query": query})["value"] == result
    assert result["matched_count"] == 2
    assert result["followed_state_keys"] == ["checklist-owner"]
    assert [row["evidence"][0]["conversation_id"] for row in result["timeline"]] == [
        "first",
        "handoff",
    ]
    assert result["current_reported_states"]["checklist-owner"]["value"] == "Rae"

    first = trace(call, "recent", {**query, "limit": 1}, pipeline)
    second = trace(call, "older", {**query, "limit": 1, "cursor": first["next_cursor"]}, pipeline)
    assert [page["timeline"][0]["state"] for page in (first, second)] == ["Rae", "David"]
    assert second["next_cursor"] is None
    absent = trace(call, "no-anchor", {**query, "start_date": "2026-05-05"}, pipeline)
    assert absent["timeline"] == absent["followed_state_keys"] == []


def test_conversation_scope_keeps_state_following_and_citations_in_one_source(work):
    _, call = work
    earlier = text_input("earlier", "Private initial owner.", date="2026-05-03")
    external_fact = ingest(call, "earlier", earlier)["revision_id"] + "F1"
    selected = text_input("shared-id", "Ada owns the review.", date="2026-05-04")
    selected["annotations"]["facts"][0].update(state="Ada", supersedes=external_fact)
    first = ingest(call, "first", selected)
    changed = text_input("shared-id", "Rae owns the review.", date="2026-05-05")
    changed["source"]["expected_checksum"] = first["checksum"]
    changed["annotations"]["facts"][0].update(state="Rae", supersedes=first["revision_id"] + "F1")
    latest = ingest(call, "update", changed, pipeline="memory-update")
    unrelated = text_input("other-id", "Private other review.", date="2026-05-06")
    ingest(call, "other", unrelated)
    archive = text_input("shared-id", "Private archived review.", date="2026-05-06")
    archive["source"]["format"] = "conversation"
    raw = archive["source"]["payload"]
    raw.update(source="archive", segments=[{"segment_id": "text-1", "text": raw.pop("text")}])
    ingest(call, "archive", archive)

    query = {
        "source": "text",
        "conversation_id": "shared-id",
        "topics": ["onboarding"],
        "text": "Ada owns",
        "follow_states": True,
    }
    pipeline = "task-tracing"
    report = trace(call, "scoped", query, pipeline)
    assert call("query", "--pipeline", pipeline, payload={"query": query})["value"] == report
    assert [row["state"] for row in report["timeline"]] == ["Ada", "Rae"]
    assert [row["supersedes"] for row in report["timeline"]] == [None, first["revision_id"] + "F1"]
    assert report["current_reported_states"]["owner"]["value"] == "Rae"
    assert {
        (line["source"], line["conversation_id"])
        for row in report["timeline"]
        for line in row["evidence"]
    } == {("text", "shared-id")}
    assert "Private" not in json.dumps(report) and external_fact not in json.dumps(report)
    page = call("query", "--pipeline", pipeline, payload={"query": {**query, "limit": 1}})["value"]
    assert page["next_cursor"]
    assert page["timeline"][0]["supersedes"] == first["revision_id"] + "F1"
    next_query = {**query, "limit": 1, "cursor": page["next_cursor"]}
    continuation = call("query", "--pipeline", pipeline, payload={"query": next_query})["value"]
    assert [row["state"] for row in continuation["timeline"]] == ["Ada"]
    assert continuation["timeline"][0]["supersedes"] is None
    call(
        "query",
        "--pipeline",
        pipeline,
        payload={"query": {**next_query, "conversation_id": "other-id"}},
        error="invalid_input",
    )
    incomplete_scope = {key: value for key, value in query.items() if key != "source"}
    call(
        "start",
        pipeline,
        "--run-id",
        "ambiguous",
        payload={"query": incomplete_scope},
        error="invalid_input",
    )
    call("status", "ambiguous", error="not_found")
    namespace_scope = {key: value for key, value in query.items() if key != "conversation_id"}
    broader = call("query", "--pipeline", pipeline, payload={"query": namespace_scope})["value"]
    ada = next(row for row in broader["timeline"] if row["state"] == "Ada")
    assert ada["supersedes"] == external_fact
    correction = text_input("later", "Private subsequent owner.", date="2026-05-07")
    correction["annotations"]["facts"][0]["supersedes"] = latest["revision_id"] + "F1"
    correction_id = ingest(call, "later", correction)["revision_id"] + "F1"
    scoped = call("query", "--pipeline", pipeline, payload={"query": query})["value"]
    assert not scoped["timeline"][-1]["effective"]
    assert "owner" not in scoped["current_reported_states"]
    assert correction_id not in json.dumps(scoped) and "Private" not in json.dumps(scoped)


def test_trace_cursor_reads_all_windows_without_repeating_evidence(work):
    _, call = work
    for name, event in (
        ("oldest", {"event_date": "2026-03-01"}),
        ("early", {"event_date": "2026-03-02"}),
        ("middle", {"event_date": "2026-03-02"}),
        ("late", {"event_date": "2026-03-04"}),
        ("latest", {"event_date": "2026-03-05"}),
        ("inferred", {"event_date": "2026-03-06", "event_date_basis": "inferred"}),
        ("unknown", {}),
    ):
        payload = text_input(name, name, date="2026-04-01")
        payload["annotations"]["facts"][0].update(event)
        ingest(call, f"import-{name}", payload)
    query = {"topics": ["onboarding"], "time_axis": "event", "limit": 2}
    cursor = None
    pages = []
    while True:
        scoped = {**query, "cursor": cursor} if cursor else query
        report = trace(call, f"page-{len(pages)}", scoped)
        assert report["matched_count"] == 7 and report["truncated"]
        assert report["current_reported_states"]["owner"]["status"] == "partial"
        pages.append([row["state"] for row in report["timeline"]])
        cursor = report["next_cursor"]
        if cursor is None:
            break
        assert len(pages) < 4
    assert pages[0] == ["late", "latest"]
    assert set(pages[1]) == {"early", "middle"}
    assert pages[2:] == [["oldest", "inferred"], ["unknown"]]
    assert len({state for page in pages for state in page}) == 7


def test_direct_query_matches_staged_report_and_creates_no_workflow_data(work, monkeypatch):
    directory, call = work
    query = {"topics": ["onboarding"], "limit": 1}
    call("query", payload={"query": query}, error="not_found")
    assert not directory.exists()
    for name, date in (("first", "2026-06-01"), ("second", "2026-06-02")):
        ingest(call, name, text_input(name, name, date=date))
    expected = trace(call, "staged", query)
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM index_seal")
    before = database.read_bytes()
    rebuilt = 0
    build_index = workspace.build_index

    def count_rebuilds(*args, **kwargs):
        nonlocal rebuilt
        rebuilt += 1
        return build_index(*args, **kwargs)

    monkeypatch.setattr(workspace, "build_index", count_rebuilds)

    direct = call("query", payload={"query": query})["value"]
    assert direct == expected and direct["next_cursor"]
    assert rebuilt == 1
    page = call("query", payload={"query": {**query, "cursor": direct["next_cursor"]}})["value"]
    assert page["timeline"][0]["evidence"][0]["conversation_id"] == "first"
    assert page["next_cursor"] is None
    assert rebuilt == 2
    empty = call("query", "--pipeline", "timeline-reconstruction", payload={"query": {}})
    assert empty["value"]["matched_count"] == 2
    call("query", payload={"query": {"topics": []}}, error="invalid_input")
    call("query", payload={"query": {}, "source": {}}, error="invalid_input")
    call("query", payload={"query": {}}, error="invalid_input")
    assert database.read_bytes() == before
    ingest(call, "third", text_input("third", "third", date="2026-06-03"))
    call(
        "query",
        payload={"query": {**query, "cursor": direct["next_cursor"]}},
        error="stale_snapshot",
    )
    updated = call("query", payload={"query": query})["value"]
    assert updated["matched_count"] == 3
    assert updated["timeline"][0]["evidence"][0]["conversation_id"] == "third"


def test_direct_topic_arguments_match_json_without_writing_memory(work):
    directory, call = work
    ingest(call, "first", fixture("first-meeting"))
    ingest(call, "second", fixture("second-meeting"))
    database = directory / "kite.sqlite3"
    before = database.read_bytes()
    topics = ["first run", "不存在的主题"]
    single = call("query", "--topic", topics[0])["value"]
    assert single["current_reported_states"]["onboarding-design"]["value"] == "short checklist"
    report = call("query", "--topic", topics[0], "--topic", topics[1])["value"]
    assert report == call("query", payload={"query": {"topics": topics}})["value"]
    assert report["unresolved_topics"] == [topics[1]]
    call("query", "--topic", " ", error="invalid_input")
    assert database.read_bytes() == before


def test_evidence_budget_pages_keep_exact_quotes_and_chronological_windows(work):
    _, call = work
    for name, quote, day in (
        ("old", "Old", "2026-05-01"),
        ("large", "界" * 600, "2026-05-02"),
        ("recent", "Recent", "2026-05-03"),
        ("latest", "Latest", "2026-05-04"),
        ("unknown", "Unknown", None),
    ):
        ingest(call, name, text_input(name, quote, date=day))
    query = {"topics": ["onboarding"], "limit": 5, "evidence_byte_budget": 1100}
    first = trace(call, "staged", query)
    assert first == call("query", payload={"query": query})["value"]
    assert first["next_cursor"]
    call(
        "query",
        payload={"query": {**query, "evidence_byte_budget": 1200, "cursor": first["next_cursor"]}},
        error="invalid_input",
    )
    second = call("query", payload={"query": {**query, "cursor": first["next_cursor"]}})["value"]
    third = call("query", payload={"query": {**query, "cursor": second["next_cursor"]}})["value"]
    assert third["next_cursor"] is None
    assert [
        [row["evidence"][0]["conversation_id"] for row in report["timeline"]]
        for report in (first, second, third)
    ] == [["recent", "latest"], ["large"], ["old", "unknown"]]
    assert second["timeline"][0]["evidence"][0]["text"] == "界" * 600
    for report, oversized in ((first, False), (second, True), (third, False)):
        payload = [row["evidence"] for row in report["timeline"]]
        size = len(encode(payload).encode())
        assert report["evidence_budget"] == {
            "limit_bytes": 1100,
            "payload_bytes": size,
            "over_budget": oversized,
        }
        assert (size > 1100) == oversized
        assert report["matched_count"] == 5 and report["truncated"]
        assert report["current_reported_states"]["owner"]["status"] == "partial"


def test_direct_query_leaves_legacy_schema_upgrade_to_workspace_commands(work):
    directory, call = work
    ingest(call, "initial", text_input("initial", "Original evidence"))
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE index_seal")
    before = database.read_bytes()
    query = {"query": {"topics": ["onboarding"]}}
    call("query", payload=query, error="workspace_upgrade_required")
    assert database.read_bytes() == before
    call("integrity")
    assert call("query", payload=query)["value"]["matched_count"] == 1


def test_evidence_context_reads_the_cited_revision_until_deletion(work):
    directory, call = work
    payload = {
        "source": {
            "format": "transcript",
            "payload": {
                "conversation_id": "handoff",
                "started_at": "2026-06-01T10:00:00Z",
                "metadata": {"source_reference": "fixture:handoff"},
                "segments": [
                    {"segment_id": "intro", "text": "Review the timeline.", "speaker_id": "Rae"},
                    {
                        "segment_id": "owner",
                        "text": "I own the launch.",
                        "speaker_id": "Ada",
                        "start_ms": 500,
                    },
                    {"segment_id": "close", "text": "Next topic.", "speaker_id": "Rae"},
                ],
            },
        },
        "annotations": {
            "facts": [
                {"content": "Ada owns the launch.", "segment_ids": ["owner"], "topics": ["launch"]}
            ]
        },
    }
    first = ingest(call, "first", payload)
    report = call("query", payload={"query": {"topics": ["launch"]}})["value"]
    citation = report["timeline"][0]["evidence"][0]
    line_id = citation["source_line_id"]
    database = directory / "kite.sqlite3"
    original = database.read_bytes()
    exact = call("evidence", line_id)["value"]
    assert exact["evidence"] == citation
    assert exact["before"] == exact["after"] == []
    assert exact["source_revision_active"] and exact["source_time"] == "2026-06-01T10:00:00Z"
    context = call("evidence", line_id, "--context", "20")["value"]
    before, after = context["before"][0], context["after"][0]
    assert [row["segment_id"] for row in (before, context["evidence"], after)] == [
        "intro",
        "owner",
        "close",
    ]
    assert all(row["revision_checksum"] == first["checksum"] for row in (before, after))
    assert call("evidence", before["source_line_id"], "--context", "1")["value"]["before"] == []
    call("evidence", line_id, "--context", "21", error="invalid_input")
    call("evidence", "owner", error="invalid_input")
    call("evidence", f"{line_id[:-1]}4", error="not_found")
    assert database.read_bytes() == original

    revised = copy.deepcopy(payload)
    revised["source"]["expected_checksum"] = first["checksum"]
    revised["source"]["payload"]["segments"][1]["text"] = "Rae now owns the launch."
    revised["annotations"]["facts"][0]["content"] = "Rae now owns the launch."
    latest = ingest(call, "revised", revised, pipeline="memory-update")
    old = call("evidence", line_id, "--context", "1")["value"]
    assert old["evidence"] == citation and not old["source_revision_active"]
    assert old["before"] == context["before"] and old["after"] == context["after"]
    before_history = database.read_bytes()
    assert call("source-history", "transcript", "handoff") == {
        "ok": True,
        "source": "transcript",
        "conversation_id": "handoff",
        "current_checksum": latest["checksum"],
        "memory_version": latest["memory_version"],
        "revisions": [
            {
                "revision_id": receipt["revision_id"],
                "checksum": receipt["checksum"],
                "active": active,
                "source_day": "2026-06-01",
                "segments": 3,
                "facts": 1,
            }
            for receipt, active in ((latest, True), (first, False))
        ],
    }
    call("source-history", "text", "handoff", error="not_found")
    call("source-history", "invalid/source", "handoff", error="invalid_input")
    assert database.read_bytes() == before_history

    _delete_source(call, "delete", {"source": "transcript", "conversation_id": "handoff"})
    call("evidence", line_id, error="not_found")
    call("source-history", "transcript", "handoff", error="not_found")


def test_evidence_byte_budget_keeps_the_cited_line_and_contiguous_neighbors(work):
    directory, call = work
    payload = text_input("context", "保留最终设计")
    source = payload["source"]
    source["format"] = "transcript"
    del source["payload"]["text"]
    source["payload"]["segments"] = [
        {"segment_id": "far-before", "text": "Short."},
        {"segment_id": "near-before", "text": "长" * 500},
        {"segment_id": "text-1", "text": "保留最终设计"},
        {"segment_id": "near-after", "text": "后续工作"},
        {"segment_id": "far-after", "text": "下一段"},
    ]
    receipt = ingest(call, "context", payload)
    line_id = f"{receipt['revision_id']}L3"
    database = directory / "kite.sqlite3"
    before = database.read_bytes()
    full = call("evidence", line_id, "--context", "20")["value"]
    assert [row["segment_id"] for row in full["before"]] == ["far-before", "near-before"]
    expected = {"evidence": full["evidence"], "before": [], "after": full["after"]}
    budget = len(encode(expected).encode("utf-8"))
    result = call("evidence", line_id, "--context", "20", "--byte-budget", str(budget))["value"]
    assert {key: result[key] for key in expected} == expected
    assert result["context_budget"] == {
        "limit_bytes": budget,
        "payload_bytes": budget,
        "over_budget": False,
        "omitted_before": 2,
        "omitted_after": 0,
    }

    small = call("evidence", line_id, "--context", "20", "--byte-budget", "128")["value"]
    assert small["evidence"] == full["evidence"] and small["evidence"]["text"] == "保留最终设计"
    assert small["before"] == small["after"] == []
    assert small["context_budget"]["over_budget"]
    assert (
        small["context_budget"]["omitted_before"] == small["context_budget"]["omitted_after"] == 2
    )
    for invalid in ("127", "1048577"):
        call("evidence", line_id, "--byte-budget", invalid, error="invalid_input")
    assert database.read_bytes() == before


def test_related_trace_labels_are_ranked_and_bound_to_the_evidence_page(work):
    _, call = work
    for number in range(1, 7):
        payload = text_input(f"log-{number}", f"Evidence {number}", date=f"2026-06-0{number}")
        payload["source"]["payload"]["metadata"]["project"] = "main" if number < 6 else "other"
        fact = payload["annotations"]["facts"][0]
        if number < 5:
            fact.update(topics=["rollout", "checklist"], entities=["Rae"])
        elif number == 5:
            fact.update(topics=["launch", "security"], entities=["Ada"])
        else:
            fact.update(topics=["rollout", "restricted"], entities=["Other project"])
        if number == 1:
            payload["annotations"]["taxonomy"] = [
                {"name": "launch", "aliases": ["launch program"]},
                {"name": "rollout", "parents": ["launch"]},
            ]
        ingest(call, f"import-{number}", payload)

    query = {"topics": ["launch program"], "projects": ["main"]}
    full = trace(call, "full", query)
    first_ids = [row["fact_id"] for row in full["timeline"][:3]]
    last_id = full["timeline"][-1]["fact_id"]
    assert full["resolved_topics"] == ["launch", "rollout"]
    assert full["matched_count"] == 5
    assert full["related_topics"] == [
        {"code": "checklist", "fact_count": 4, "fact_ids": first_ids},
        {"code": "security", "fact_count": 1, "fact_ids": [last_id]},
    ]
    assert full["related_entities"] == [
        {"code": "rae", "fact_count": 4, "fact_ids": first_ids},
        {"code": "ada", "fact_count": 1, "fact_ids": [last_id]},
    ]
    page = trace(call, "page", {**query, "limit": 1})
    assert page["next_cursor"]
    assert page["related_topics"] == [full["related_topics"][1]]
    assert page["related_entities"] == [full["related_entities"][1]]
    followup = trace(call, "followup", {"topics": ["checklist"], "entities": ["rae"]})
    assert followup["matched_count"] == 4
    assert [label["code"] for label in followup["related_topics"]] == ["rollout"]


def test_trace_cursor_rejects_changes_to_filters_and_snapshot(work, tmp_path):
    directory, call = work
    for identity in ("a", "b"):
        ingest(call, f"import-{identity}", text_input(identity, identity))
    query = {"topics": ["onboarding"], "limit": 1}
    cursor = trace(call, "first", query)["next_cursor"]
    assert cursor

    def reject(name, selection, error, *, destination=None):
        call(
            "start",
            "topic-tracing",
            "--run-id",
            name,
            payload={"query": selection},
            workspace_dir=destination,
        )
        call("stage", name, "resolve", error=error, workspace_dir=destination)

    with workspace.Workspace.open(directory, read_only=True) as current:
        identity, version = current.snapshot_identity()
    previous_scope = [identity, version, "topic-tracing", query]
    legacy_cursor = f"{version}.1.{digest([previous_scope, 1])}"
    reject("old-ordering", {**query, "cursor": legacy_cursor}, "invalid_input")
    reject("changed-filter", {**query, "cursor": cursor, "limit": 2}, "invalid_input")
    copy = tmp_path / "copy"
    call("backup", str(copy))
    reject("other-workspace", {**query, "cursor": cursor}, "invalid_input", destination=copy)
    ingest(call, "third", text_input("c", "c"))
    reject("changed-memory", {**query, "cursor": cursor}, "stale_snapshot")


def test_event_state_uses_event_dates_and_preserves_unordered_evidence(work):
    _, call = work
    for name, day, event in (
        ("Rae", "2026-04-01", "2026-03-28"),
        ("Sam", "2026-04-02", "2026-03-10"),
    ):
        payload = text_input(name, name, date=day)
        payload["annotations"]["facts"][0]["event_date"] = event
        ingest(call, f"import-{name}", payload)

    query = {"state_key": "owner"}
    pipeline = "timeline-reconstruction"
    by_source = trace(call, "by-source", query, pipeline)
    by_event = trace(call, "by-event", {**query, "time_axis": "event"}, pipeline)
    assert [row["state"] for row in by_event["timeline"]] == ["Sam", "Rae"]
    assert by_event["timeline"][-1]["source_day"] == "2026-04-01"
    assert by_source["current_reported_states"]["owner"]["value"] == "Sam"
    assert by_event["current_reported_states"]["owner"]["value"] == "Rae"

    tie = text_input("tie", "Kim", date="2026-04-03")
    tie["annotations"]["facts"][0]["event_date"] = "2026-03-28"
    ingest(call, "import-tie", tie)
    report = trace(call, "tied-event", {**query, "time_axis": "event"}, pipeline)
    states = report["current_reported_states"]["owner"]
    assert states["status"] == "ambiguous" and states["value"] is None
    assert {row["state"] for row in report["timeline"] if row["fact_id"] in states["fact_ids"]} == {
        "Kim",
        "Rae",
    }

    ingest(call, "unknown-event", text_input("no-date", "Noah", date="2026-04-04"))
    dated = trace(
        call,
        "dated-events",
        {**query, "time_axis": "event", "start_date": "2026-03-01"},
        pipeline,
    )
    assert dated["matched_count"] == 3
    assert dated["omitted_unknown_time"] == 1 and not dated["truncated"]
    assert dated["current_reported_states"]["owner"]["status"] == "partial"


def test_inferred_dates_are_separate_from_explicit_and_unknown_dates(work):
    _, call = work
    for name, source_date, event in (
        ("Explicit", "2026-04-01", {"event_date": "2026-03-28"}),
        ("Inferred", "2026-04-02", {"event_date": "2026-03-30", "event_date_basis": "inferred"}),
        ("Unknown", "2026-04-03", {}),
    ):
        payload = text_input(name, name, date=source_date)
        payload["annotations"]["facts"][0].update(event)
        ingest(call, f"import-{name}", payload)

    query = {"state_key": "owner", "time_axis": "event"}
    pipeline = "timeline-reconstruction"
    result = trace(call, "all-events", query, pipeline)
    assert [(row["event_date_basis"], row["event_date"]) for row in result["timeline"]] == [
        ("explicit", "2026-03-28"),
        ("inferred", "2026-03-30"),
        ("unknown", None),
    ]
    assert result["current_reported_states"]["owner"]["status"] == "ambiguous"
    bounded = trace(call, "bounded", {**query, "start_date": "2026-03-01"}, pipeline)
    assert [row["state"] for row in bounded["timeline"]] == ["Explicit"]
    assert bounded["omitted_unknown_time"] == bounded["omitted_inferred_time"] == 1
    assert not bounded["truncated"]
    assert bounded["current_reported_states"]["owner"]["status"] == "partial"
    limited = trace(call, "limited", {**query, "limit": 1}, pipeline)
    assert [row["state"] for row in limited["timeline"]] == ["Explicit"]
    assert limited["truncated"]
    by_source = trace(call, "source", {**query, "time_axis": "source"}, pipeline)
    assert by_source["current_reported_states"]["owner"]["value"] == "Unknown"
    assert by_source["omitted_inferred_time"] == 0
    codebook = ET.fromstring(call("codebook"))
    exported = {(fact.text or "").strip(): fact.get("t") for fact in codebook.findall(".//fact")}
    assert exported["Explicit"] == "2026-03-28"
    assert not exported["Inferred"] and not exported["Unknown"]


@pytest.mark.parametrize(("axis", "unknown", "inferred"), [("source", 3, 0), ("event", 2, 1)])
def test_date_omissions_affect_only_relevant_reported_states(work, axis, unknown, inferred):
    _, call = work
    obsolete = text_input("obsolete", "planning")
    obsolete["annotations"]["facts"][0].update(state_key="phase")
    predecessor = ingest(call, "obsolete", obsolete)["revision_id"] + "F1"

    dated = text_input("dated", "Rae owns delivery, which is in progress.", date="2026-04-02")
    owner = dated["annotations"]["facts"][0]
    owner.update(state="Rae", event_date="2026-04-02")
    dated["annotations"]["facts"].append(
        {
            **owner,
            "content": "Delivery is in progress.",
            "state_key": "phase",
            "state": "in progress",
            "supersedes": predecessor,
        }
    )
    ingest(call, "dated", dated)

    undated = text_input("undated", "Sam reports taking ownership.")
    undated["annotations"]["facts"][0].update(
        state="Sam", event_date="2026-04-03", event_date_basis="inferred"
    )
    ingest(call, "undated", undated)
    proposal = text_input("proposal", "Pause delivery?", status="proposal")
    proposal["annotations"]["facts"][0].update(state_key="phase", state="paused")
    ingest(call, "proposal", proposal)

    query = {"topics": ["onboarding"], "end_date": "2026-04-08", "time_axis": axis}
    pipeline = "timeline-reconstruction"
    report = trace(call, "bounded", query, pipeline)
    assert call("query", "--pipeline", pipeline, payload={"query": query})["value"] == report
    assert report["matched_count"] == 2 and not report["truncated"]
    assert (report["omitted_unknown_time"], report["omitted_inferred_time"]) == (unknown, inferred)
    assert report["uncertain_time_state_keys"] == ["owner"]
    assert report["current_reported_states"]["owner"]["status"] == "partial"
    assert report["current_reported_states"]["owner"]["value"] is None
    assert report["current_reported_states"]["phase"]["value"] == "in progress"


def test_event_date_provenance_is_validated_and_cannot_assert_recency_alone(work):
    _, call = work
    payload = text_input("incident", "Rae", date="2026-04-01")
    fact = payload["annotations"]["facts"][0]
    fact["event_date_basis"] = "inferred"
    call("start", "memory-ingestion", "--run-id", "bad", payload=payload, error="invalid_input")
    fact["event_date"] = "2026-03-30"
    receipt = ingest(call, "estimate", payload)
    query = {"time_axis": "event", "state_key": "owner"}
    state = trace(call, "estimate-only", query, "timeline-reconstruction")
    assert state["current_reported_states"]["owner"]["status"] == "partial"
    assert state["current_reported_states"]["owner"]["value"] is None
    assert state["timeline"][0]["evidence"][0]["text"] == "Rae"
    fact["event_date_basis"] = "explicit"
    payload["source"]["expected_checksum"] = receipt["checksum"]
    assert ingest(call, "explicit-citation", payload, pipeline="memory-update")["changed"]
    del fact["event_date_basis"]
    assert not ingest(call, "explicit-replay", payload, pipeline="memory-update")["changed"]


def test_source_state_orders_adjacent_segments_without_ordering_overlaps(work):
    _, call = work
    segments = [
        {"segment_id": "a", "text": "Rae", "start_ms": 0, "end_ms": 2000},
        {"segment_id": "b", "text": "Sam", "start_ms": 1000, "end_ms": 2000},
        {"segment_id": "c", "text": "Kim", "start_ms": 2000, "end_ms": 3000},
    ]
    payload = {
        "source": {
            "format": "transcript",
            "payload": {
                "conversation_id": "meeting",
                "segments": segments,
                "metadata": {"source_reference": "meeting"},
            },
        },
        "annotations": {
            "facts": [
                {
                    "content": line["text"],
                    "segment_ids": [line["segment_id"]],
                    "topics": topics,
                    "state_key": "owner",
                    "state": line["text"],
                    "status": "confirmed",
                }
                for line, topics in zip(
                    segments, (["overlap", "adjacent"], ["overlap"], ["adjacent"])
                )
            ]
        },
    }
    ingest(call, "meeting", payload)
    overlapping = trace(call, "overlap", {"topics": ["overlap"]})
    adjacent = trace(call, "adjacent", {"topics": ["adjacent"]})
    assert overlapping["current_reported_states"]["owner"]["status"] == "ambiguous"
    assert adjacent["current_reported_states"]["owner"]["value"] == "Kim"
    assert overlapping["timeline"][0]["evidence"][0]["end_ms"] == 2000


@pytest.mark.parametrize(
    ("short_end_ms", "status", "owner"), [(1000, "confirmed", "Ada"), (4000, "ambiguous", None)]
)
def test_source_timeline_uses_cited_intervals_across_recordings(work, short_end_ms, status, owner):
    _, call = work
    for name, stamp, begin, end in (
        ("Ada", "2026-11-01T23:59:00Z", 65_000, 66_000),
        ("Rae", "2026-11-02T08:00:02+08:00", 0, short_end_ms),
    ):
        segment = {"segment_id": "text-1", "text": name, "start_ms": begin, "end_ms": end}
        payload = text_input(name, name)
        source = payload["source"]
        source["format"] = "transcript"
        del source["payload"]["text"]
        source["payload"].update(started_at=stamp, segments=[segment])
        ingest(call, f"recording-{name}", payload)

    pipeline = "task-tracing"
    query = {"state_key": "owner"}
    report = trace(call, "owner", query, pipeline)
    assert call("query", "--pipeline", pipeline, payload={"query": query})["value"] == report
    assert [row["state"] for row in report["timeline"]] == ["Rae", "Ada"]
    assert [row["source_day"] for row in report["timeline"]] == ["2026-11-02", "2026-11-01"]
    state = report["current_reported_states"]["owner"]
    assert (state["status"], state["value"]) == (status, owner)
    assert state["fact_ids"] == [
        row["fact_id"] for row in report["timeline"] if row["state"] == owner or owner is None
    ]
    first = trace(call, "recent-evidence", {**query, "limit": 1}, pipeline)
    assert [row["state"] for row in first["timeline"]] == ["Ada"]


@pytest.mark.parametrize("correction_is_later", [True, False])
def test_cross_recording_correction_uses_cited_segment_times(work, correction_is_later):
    _, call = work

    def recording(name, stamp, offset):
        payload = text_input(name, name)
        source = payload["source"]
        source["format"] = "transcript"
        del source["payload"]["text"]
        source["payload"].update(
            started_at=stamp,
            segments=[{"segment_id": "text-1", "text": name, "start_ms": offset}],
        )
        return payload

    # The earlier recording captures its cited line after the later recording's line.
    earlier_recording = recording("Ada", "2026-11-01T23:59:00Z", 65_000)
    later_recording = recording("Rae", "2026-11-02T08:00:02+08:00", 0)
    original, corrected = (
        (later_recording, earlier_recording)
        if correction_is_later
        else (earlier_recording, later_recording)
    )
    first = ingest(call, "first", original)
    target = first["revision_id"] + "F1"
    corrected["annotations"]["facts"][0]["supersedes"] = target
    before = call("integrity")
    if correction_is_later:
        updated = ingest(call, "corrected", corrected, pipeline="memory-update")
        assert updated["changed"]
        result = trace(call, "history", {"state_key": "owner"}, "task-tracing")
        assert [row["state"] for row in result["timeline"]] == ["Rae", "Ada"]
        assert [row["effective"] for row in result["timeline"]] == [False, True]
        assert result["timeline"][1]["supersedes"] == target
        assert result["current_reported_states"]["owner"]["value"] == "Ada"
        assert not ingest(call, "replay", corrected, pipeline="memory-update")["changed"]
    else:
        rejected = ingest(call, "too-early", corrected, pipeline="memory-update", error="conflict")
        assert "predates" in rejected["error"]["message"]
        assert call("integrity") == before


def test_same_revision_correction_rejects_earlier_cited_line(work):
    _, call = work
    payload = text_input("meeting", "Rae")
    source = payload["source"]
    source["format"] = "transcript"
    del source["payload"]["text"]
    source["payload"]["segments"] = [
        {"segment_id": "earlier", "text": "Ada", "start_ms": 0, "end_ms": 1000},
        {"segment_id": "text-1", "text": "Rae, confirmed by Ada", "start_ms": 1000},
    ]
    original = ingest(call, "first", payload)
    source["expected_checksum"] = original["checksum"]
    fact = payload["annotations"]["facts"][0]
    fact.update(
        content="Ada",
        state="Ada",
        segment_ids=["earlier"],
        supersedes=original["revision_id"] + "F1",
    )
    ingest(call, "too-early", payload, pipeline="memory-update", error="conflict")
    assert call("integrity")["facts"] == 1
    fact.update(content="Rae, confirmed by Ada", state="Rae", segment_ids=["text-1"])
    amended = ingest(call, "fix-annotation", payload, pipeline="memory-update")
    assert amended["changed"] and amended["revision_id"] == original["revision_id"]


def test_legacy_index_is_checked_once_and_integrity_still_rebuilds(work, monkeypatch):
    directory, call = work
    ingest(call, "first", fixture("first-meeting"))
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("DELETE FROM index_seal")
    calls = []
    original = workspace.build_index

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(workspace, "build_index", counted)
    report = trace(call, "legacy", {"topics": ["first run"]})
    assert report["matched_count"] > 0
    assert len(calls) == 1
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        assert db.execute("SELECT version FROM index_seal").fetchone() == (1,)
    call("integrity")
    assert len(calls) == 2


def test_index_seal_detects_annotation_drift_and_can_be_rebuilt(work):
    directory, call = work
    ingest(call, "first", fixture("first-meeting"))
    path = directory / "kite.sqlite3"
    with sqlite3.connect(path) as db:
        payload = db.execute("SELECT payload FROM revisions").fetchone()[0]
        changed = json.loads(payload)
        changed["facts"][0]["status"] = "open"
        db.execute("UPDATE revisions SET payload=?", (json.dumps(changed),))
    call("integrity", error="integrity_error")
    result = call("codebook", "--source", "text", "--active-only", error="integrity_error")
    assert "source snapshot" in result["error"]["message"]
    call(
        "start",
        "topic-tracing",
        "--run-id",
        "corrupted",
        payload={"query": {"topics": ["first run"]}},
    )
    call("stage", "corrupted", "resolve", error="integrity_error")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE revisions SET payload=?", (payload,))
        db.execute("UPDATE index_seal SET checksum=?", ("0" * 64,))
    call("start", "memory-rebuild", "--run-id", "rebuild")
    assert call("stage", "rebuild", "rebuild")["value"]["changed"]
    assert call("integrity")["memory_version"] == 2


def test_rebuild_uses_verified_source_records(work):
    directory, call = work
    ingest(call, "first", fixture("first-meeting"))
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        db.execute("UPDATE memory SET xml=?", ("corrupt xml",))
    call("integrity", error="integrity_error")
    call("start", "memory-rebuild", "--run-id", "repair")
    assert call("stage", "repair", "rebuild")["value"]["changed"]
    call("stage", "repair", "verify")
    assert call("integrity")["facts"] == 1
    call("start", "memory-rebuild", "--run-id", "already-healthy")
    assert not call("stage", "already-healthy", "rebuild")["value"]["changed"]


@pytest.mark.parametrize("damage", ["annotation", "missing_seal"])
def test_rebuild_requires_intact_source_seal(work, damage):
    directory, call = work
    receipt = ingest(call, "first", text_input("first", "Synthetic original evidence."))
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        if damage == "annotation":
            record = json.loads(db.execute("SELECT payload FROM revisions").fetchone()[0])
            record["facts"][0]["status"] = "proposal"
            db.execute("UPDATE revisions SET payload=?", (json.dumps(record),))
        else:
            db.execute("DELETE FROM source_seal")
    call("start", "memory-rebuild", "--run-id", "repair")
    error = call("stage", "repair", "rebuild", error="integrity_error")
    assert "source snapshot" in error["error"]["message"]
    assert "Synthetic original evidence" not in json.dumps(error)
    assert call("status", "repair")["memory_version"] == receipt["memory_version"]


def test_integrity_establishes_source_seal_for_legacy_workspace(work):
    directory, call = work
    original = ingest(call, "first", text_input("first", "Synthetic original evidence."))
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        original_index_seal = db.execute("SELECT checksum FROM index_seal").fetchone()[0]
        db.execute("DROP TABLE source_seal")
        db.execute("PRAGMA user_version=1")
    before = database.read_bytes()
    report = call("query", payload={"query": {"topics": ["onboarding"]}})["value"]
    assert report["matched_count"] == 1 and database.read_bytes() == before
    assert "Synthetic original evidence." in call("codebook", "--source", "text")
    assert database.read_bytes() == before
    assert call("integrity")["memory_version"] == original["memory_version"]
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT version FROM source_seal").fetchone() == (
            original["memory_version"],
        )
        assert db.execute("SELECT checksum FROM index_seal").fetchone() == (original_index_seal,)


@pytest.mark.parametrize("damage", ["sealed_annotations", "legacy_index", "seal_version"])
def test_ordinary_writes_do_not_reseal_damaged_snapshot(work, damage):
    directory, call = work
    source = text_input("original", "Rae owns the review.")
    original = ingest(call, "original", source)
    call(
        "start",
        "memory-deletion",
        "--run-id",
        "remove",
        payload={"target": {"source": "text", "conversation_id": "original"}},
    )
    token = call("stage", "remove", "preview")["value"]["approval_token"]
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        raw = db.execute("SELECT payload FROM revisions").fetchone()[0]
        record = json.loads(raw)
        if damage == "sealed_annotations":
            record["facts"][0]["status"] = "open"
        elif damage == "legacy_index":
            db.execute("DELETE FROM index_seal")
            record["facts"][0]["content"] = "Altered stored annotation"
        else:
            db.execute("UPDATE index_seal SET version=version+1")
        db.execute("UPDATE revisions SET payload=?", (json.dumps(record),))

    call("stage", "remove", "apply", "--approve", token, error="integrity_error")
    added = text_input("next", "Next review.")
    ingest(call, "next", added, error="integrity_error")
    ingest(call, "duplicate", source, error="integrity_error")
    amended = copy.deepcopy(source)
    amended["source"]["expected_checksum"] = original["checksum"]
    amended["annotations"]["facts"][0]["content"] = "Rae is the assigned reviewer."
    ingest(call, "amend", amended, pipeline="memory-update", error="integrity_error")
    assert call("status", "next")["memory_version"] == original["memory_version"]
    assert call("deletion-audit")["deletions"] == []
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT count(*) FROM revisions").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM tombstones").fetchone() == (0,)
        db.execute("UPDATE revisions SET payload=?", (raw,))
        if damage == "seal_version":
            db.execute("UPDATE index_seal SET version=version-1")
    recovered = call("stage", "next", "commit")["value"]
    assert recovered["changed"] and recovered["memory_version"] == original["memory_version"] + 1
    assert call("stage", "next", "verify")["value"]["revisions"] == 2


@pytest.mark.parametrize("damage", ["json", "structure", "facts", "identity"])
def test_damaged_source_record_returns_private_structured_error(work, damage):
    directory, call = work
    marker = "synthetic-private-citation-marker"
    source = text_input("review", marker)
    receipt = ingest(call, "original", source)
    line_id = f"{receipt['revision_id']}L1"
    database = directory / "kite.sqlite3"
    with sqlite3.connect(database) as db:
        raw = db.execute("SELECT payload FROM revisions").fetchone()[0]
        record = json.loads(raw)
        if damage == "json":
            raw = raw[:-1]
        elif damage == "structure":
            del record["conversation"]["metadata"]
            raw = json.dumps(record)
        elif damage == "facts":
            record["facts"] = {"invalid": marker}
            raw = json.dumps(record)
        else:
            record["checksum"] = "0" * 64
            raw = json.dumps(record)
        db.execute("UPDATE revisions SET payload=?", (raw,))

    corrupted = database.read_bytes()
    for args in (("evidence", line_id), ("source-history", "text", "review")):
        result = call(*args, error="integrity_error")
        assert marker not in json.dumps(result)
    assert database.read_bytes() == corrupted

    call("start", "memory-rebuild", "--run-id", "rebuild")
    call("stage", "rebuild", "rebuild", error="integrity_error")
    assert call("status", "rebuild")["memory_version"] == receipt["memory_version"]
    call(
        "start",
        "memory-deletion",
        "--run-id",
        "remove",
        payload={"target": {"source": "text", "conversation_id": "review"}},
    )
    call("stage", "remove", "preview", error="integrity_error")
    assert call("deletion-audit")["deletions"] == []


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


def test_local_audio_command_builds_ingestion_input(work, tmp_path, monkeypatch):
    workspace, call = work
    recording, model = tmp_path / "meeting.mp3", tmp_path / "tiny.bin"
    recording.write_bytes(b"fixture audio")
    model.touch()
    temporary = []
    body = '  Ravi said "Ship the checklist."  '

    def transcribe(command, **kwargs):
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["check"] and kwargs["timeout"] == 42
        if command[0] == "ffmpeg":
            assert command[command.index("-protocol_whitelist") + 1] == "file"
            assert command[command.index("-i") + 1] == str(recording.resolve())
            Path(command[-1]).write_bytes(b"waveform")
            temporary.append(Path(command[-1]).parent)
        else:
            assert command[0] == "whisper-cli" and "-oj" in command
            assert command[command.index("-l") + 1] == "en"
            Path(command[-1]).with_suffix(".json").write_text(
                json.dumps(
                    {
                        "params": {"model": "do-not-keep-this-path"},
                        "result": {"language": "en"},
                        "transcription": [{"text": body, "offsets": {"from": 80, "to": 2160}}],
                    }
                )
            )

    monkeypatch.setattr(audio, "which", lambda command: command)
    monkeypatch.setattr(audio.subprocess, "run", transcribe)
    output = call(
        "audio-source",
        str(recording),
        "--model",
        str(model),
        "--source-id",
        "recording:planning",
        "--language",
        "en",
        "--timeout",
        "42",
    )
    assert not workspace.exists()
    assert len(temporary) == 1 and not temporary[0].exists()
    payload = output["source"]["payload"]
    assert payload["language"] == "en"
    assert payload["metadata"]["audio_sha256"] == hashlib.sha256(recording.read_bytes()).hexdigest()
    assert "do-not-keep-this-path" not in json.dumps(output)
    assert "started_at" not in payload
    output["annotations"] = {
        "facts": [
            {
                "content": "Ravi confirmed the checklist.",
                "segment_ids": ["audio-1"],
                "topics": ["onboarding"],
                "status": "confirmed",
            }
        ]
    }
    ingest(call, "audio", output)
    report = trace(call, "trace", {"topics": ["onboarding"]})
    assert report["timeline"][0]["evidence"][0]["text"] == body
    assert report["timeline"][0]["evidence"][0]["end_ms"] == 2160
    assert report["timeline"][0]["source_time"] is None


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (FileNotFoundError(), "missing_dependency"),
        (subprocess.TimeoutExpired("ffmpeg", 1), "transcription_timeout"),
        (subprocess.CalledProcessError(1, "ffmpeg"), "transcription_error"),
    ],
)
def test_local_audio_failures_are_structured_and_leave_no_workspace(
    work,
    tmp_path,
    monkeypatch,
    failure,
    code,
):
    workspace, call = work
    recording, model = tmp_path / "meeting.mp3", tmp_path / "tiny.bin"
    recording.touch()
    model.touch()
    temporary = []

    def fail(command, **kwargs):
        temporary.append(Path(command[-1]).parent)
        raise failure

    monkeypatch.setattr(audio, "which", lambda command: command)
    monkeypatch.setattr(audio.subprocess, "run", fail)
    call(
        "audio-source", str(recording), "--model", str(model), "--source-id", "meeting", error=code
    )
    assert not workspace.exists()
    assert len(temporary) == 1 and not temporary[0].exists()


@pytest.mark.parametrize(
    ("response", "code"),
    [
        ({"transcription": []}, "no_speech"),
        ({"transcription": [{"text": "a sentence without offsets"}]}, "transcription_error"),
    ],
)
def test_local_audio_rejects_unusable_transcript(work, tmp_path, monkeypatch, response, code):
    workspace, call = work
    recording, model = tmp_path / "recording.wav", tmp_path / "tiny.bin"
    recording.touch()
    model.touch()

    def transcribe(command, **kwargs):
        if command[0] == "whisper-cli":
            Path(command[-1]).with_suffix(".json").write_text(json.dumps(response))

    monkeypatch.setattr(audio, "which", lambda command: command)
    monkeypatch.setattr(audio.subprocess, "run", transcribe)
    call(
        "audio-source", str(recording), "--model", str(model), "--source-id", "meeting", error=code
    )
    assert not workspace.exists()


def test_local_audio_reports_missing_executable_before_decoding(work, tmp_path, monkeypatch):
    workspace, call = work
    recording, model = tmp_path / "recording.wav", tmp_path / "tiny.bin"
    recording.touch()
    model.touch()
    monkeypatch.setattr(
        audio, "which", lambda command: None if command == "whisper-cli" else command
    )
    call(
        "audio-source",
        str(recording),
        "--model",
        str(model),
        "--source-id",
        "meeting",
        error="missing_dependency",
    )
    assert not workspace.exists()


def test_cli_resources_are_independent_of_the_working_directory(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "consumer"
    cwd.mkdir()
    (cwd / "pipeline_defs").mkdir()
    (cwd / "pipeline_defs" / "memory-ingestion.yaml").write_text("invalid: local definition\n")
    monkeypatch.chdir(cwd)
    assert cli.main(["resources"]) == 0
    resources = json.loads(capsys.readouterr().out)
    assert Path(resources["root"]) == ROOT
    assert Path(resources["agent_instructions"]).name == "AGENTS.md"
    assert Path(resources["agent_guide"]).is_file()
    assert cli.main(["validate-pipeline", "memory-ingestion"]) == 0
    assert json.loads(capsys.readouterr().out)["definition"]["id"] == "memory-ingestion"
    assert cli.main(["--repo", str(cwd), "validate-pipeline", "memory-ingestion"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_pipeline"


def test_file_source_preserves_text_identity_and_revisions(work, tmp_path):
    workspace, call = work
    path = tmp_path / "first meeting.txt"
    original = "  Réunion notes\r\nAda owns the work.\r\n"
    path.write_bytes(original.encode())
    source = call(
        "file-source",
        str(path),
        "--format",
        "text",
        "--source-id",
        "handoff",
        "--source-date",
        "2026-11-07",
    )
    assert not workspace.exists()
    assert source["source"]["payload"]["segments"][0]["text"] == original
    manual = text_input("handoff", original, date="2026-11-07")
    committed = ingest(call, "manual", manual)
    source["annotations"] = manual["annotations"]
    duplicate = ingest(call, "duplicate", source)
    assert not duplicate["changed"]
    assert duplicate["checksum"] == committed["checksum"]

    revised = "Ada handed the work to Rae.\n"
    path.write_bytes(revised.encode())
    source = call(
        "file-source",
        str(path),
        "--format",
        "text",
        "--source-id",
        "handoff",
        "--source-date",
        "2026-11-08",
        "--expected-checksum",
        committed["checksum"],
    )
    source["annotations"] = text_input("handoff", revised)["annotations"]
    updated = ingest(call, "updated", source, pipeline="memory-update")
    assert updated["changed"]
    assert updated["checksum"] != committed["checksum"]
    result = trace(call, "timeline", {"topics": ["onboarding"]})
    assert [item["evidence"][0]["text"] for item in result["timeline"]] == [original, revised]


def test_file_source_split_lines_preserves_quotes_line_numbers_and_source_checksum(work, tmp_path):
    workspace, call = work
    path = tmp_path / "planning.txt"
    quote = "  Ada will own the launch.  "
    original = f"Launch planning\r\n\r\n{quote}\r\n\t\rUnrelated detail.\n".encode()
    path.write_bytes(original)
    command = (
        "file-source",
        str(path),
        "--format",
        "text",
        "--source-id",
        "launch",
        "--split-lines",
    )
    prepared = call(*command)
    assert not workspace.exists()
    conversation = prepared["source"]["payload"]
    assert conversation["metadata"]["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert conversation["segments"] == [
        {"segment_id": "text-line-1", "text": "Launch planning"},
        {"segment_id": "text-line-3", "text": quote},
        {"segment_id": "text-line-5", "text": "Unrelated detail."},
    ]
    prepared["annotations"] = {
        "facts": [
            {
                "content": "Ada will own the launch.",
                "segment_ids": ["text-line-3"],
                "topics": ["launch"],
                "status": "confirmed",
            }
        ]
    }
    first = ingest(call, "first", prepared)
    duplicate = ingest(call, "duplicate", prepared)
    assert duplicate["checksum"] == first["checksum"] and not duplicate["changed"]
    evidence = trace(call, "trace", {"topics": ["launch"]})["timeline"][0]["evidence"][0]
    assert (evidence["source_reference"], evidence["segment_id"], evidence["text"]) == (
        "launch",
        "text-line-3",
        quote,
    )

    path.write_bytes(original + b"\n")
    updated = call(*command, "--expected-checksum", first["checksum"])
    updated["annotations"] = prepared["annotations"]
    assert updated["source"]["payload"]["segments"] == conversation["segments"]
    receipt = ingest(call, "updated", updated, pipeline="memory-update")
    assert receipt["changed"] and receipt["checksum"] != first["checksum"]

    path.write_text(" \n\t", encoding="utf-8")
    call(*command, error="invalid_input")
    path.write_text("valid line\n\v", encoding="utf-8")
    call(*command, error="invalid_input")
    call("file-source", str(path), "--format", "memoket", "--split-lines", error="invalid_input")


def test_file_source_imports_subrip_cues_without_inferring_speakers(work, tmp_path):
    directory, call = work
    path = tmp_path / "captions.srt"
    raw = "\ufeff7\r\n01:00:02,010 --> 01:00:04,300\r\nRae: First line.\r\n<i>Second line.</i>\r\n\r\n11\r\n01:00:04,300 --> 01:00:05,000\r\nNext cue.\r\n"
    path.write_bytes(raw.encode())
    result = call(
        "file-source",
        str(path),
        "--format",
        "srt",
        "--source-id",
        "rollout",
        "--source-date",
        "2026-05-06",
    )
    assert not directory.exists()
    conversation = result["source"]["payload"]
    assert conversation["source"] == "srt" and conversation["source_date"] == "2026-05-06"
    assert conversation["metadata"]["source_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert conversation["segments"][0] == {
        "segment_id": "srt-id-7",
        "source_sentence_id": "7",
        "start_ms": 3602010,
        "end_ms": 3604300,
        "text": "Rae: First line.\n<i>Second line.</i>",
    }
    assert conversation["segments"][1]["segment_id"] == "srt-id-11"


def test_webvtt_citations_keep_timing_and_explicit_voice(work, tmp_path):
    _, call = work
    path = tmp_path / "captions.vtt"
    path.write_text(
        "WEBVTT - synthetic meeting\nKind: captions\n\n"
        "NOTE authorized fixture\n\n"
        "STYLE\n::cue { color: lime; }\n\n"
        "REGION\nid: main\n\n"
        "decision\n00:01.250 --> 00:04.000 position:50%\n<v.main Rae>We approved PDF.</v>\n\n"
        "00:03.500 --> 00:06.100\n<v Ada>Yes.</v>\n<v Rae>Agreed.</v>\n",
        encoding="utf-8",
    )
    command = ("file-source", str(path), "--format", "vtt", "--source-id", "review")
    source = call(*command)
    first, second = source["source"]["payload"]["segments"]
    assert first["segment_id"] == "vtt-id-decision" and first["speaker_id"] == "Rae"
    assert first["source_sentence_id"] == "decision"
    assert (first["start_ms"], first["end_ms"]) == (1250, 4000)
    assert second["segment_id"] == "vtt-cue-2" and "speaker_id" not in second
    assert second["text"] == "<v Ada>Yes.</v>\n<v Rae>Agreed.</v>"
    source["annotations"] = {
        "facts": [
            {
                "content": "PDF was approved.",
                "segment_ids": ["vtt-id-decision"],
                "topics": ["export"],
                "status": "confirmed",
            }
        ]
    }
    receipt = ingest(call, "first", source)
    assert not ingest(call, "repeat", source)["changed"]
    report = call("query", payload={"query": {"topics": ["export"], "speakers": ["rae"]}})
    evidence = report["value"]["timeline"][0]["evidence"][0]
    assert evidence["source"] == "vtt" and evidence["text"] == first["text"]
    assert (evidence["start_ms"], evidence["end_ms"]) == (1250, 4000)
    context = call("evidence", evidence["source_line_id"], "--context", "1")["value"]
    assert context["after"][0]["text"] == second["text"]

    path.write_text(path.read_text().replace("00:04.000", "00:04.250"), encoding="utf-8")
    revision = call(*command, "--expected-checksum", receipt["checksum"])
    revision["annotations"] = source["annotations"]
    assert ingest(call, "updated", revision, pipeline="memory-update")["changed"]
    assert call("evidence", evidence["source_line_id"])["value"]["evidence"]["end_ms"] == 4000


@pytest.mark.parametrize(
    ("fmt", "body"),
    [
        ("vtt", "00:00.000 --> 00:01.000\nMissing header."),
        ("vtt", "WEBVTT\n00:00.000 --> 00:01.000\nMissing blank line."),
        ("srt", "1\n00:00:61,000 --> 00:01:03,000\nInvalid second."),
        ("srt", "1\n00:00:02,000 --> 00:00:01,000\nReversed."),
        (
            "srt",
            "1\n00:00:01,000 --> 00:00:02,000\nFirst.\n\n1\n00:00:02,000 --> 00:00:03,000\nDuplicate.",
        ),
    ],
)
def test_file_source_rejects_malformed_caption_cues(work, tmp_path, fmt, body):
    directory, call = work
    path = tmp_path / f"captions.{fmt}"
    path.write_text(body, encoding="utf-8")
    call(
        "file-source", str(path), "--format", fmt, "--source-id", "captions", error="invalid_input"
    )
    assert not directory.exists()


def test_file_source_minimizes_memoket_export_before_output(work, tmp_path):
    workspace, call = work
    path = tmp_path / "export.json"
    manual = fixture("second-meeting")
    raw = manual["source"]["payload"]
    raw["data"]["download_url"] = "https://example.invalid/secret-export"
    path.write_text(json.dumps(raw), encoding="utf-8")
    source = call("file-source", str(path), "--format", "memoket")
    assert not workspace.exists()
    assert "secret-export" not in json.dumps(source)
    normalized = source["source"]["payload"]
    assert normalized["conversation_id"] == "184"
    assert normalized["source_date"] == "2026-09-04"
    assert normalized["segments"][0]["speaker_id"] == "Maya"
    assert normalized["segments"][0]["start_ms"] == 1200
    path.write_text(json.dumps(raw["data"]), encoding="utf-8")
    assert call("file-source", str(path), "--format", "memoket") == source
    first = ingest(call, "manual", manual)
    source["annotations"] = manual["annotations"]
    second = ingest(call, "export", source)
    assert not second["changed"]
    assert first["checksum"] == second["checksum"]


def memoket_api_page(lines, *, offset=0, text_offset=0, next_offset=None, next_text_offset=None):
    return {
        "error": None,
        "items": [
            {
                "conversation_id": "product-review",
                "has_more": next_offset is not None,
                "line_count": len(lines),
                "lines": lines,
                "next_offset": next_offset,
                "next_text_offset": next_text_offset,
                "offset": offset,
                "text_offset": text_offset,
                "total_line_count": 2,
            }
        ],
        "next_offset": next_offset,
        "truncated": next_offset is not None,
    }


@pytest.fixture
def memoket_api_transcript():
    first = {
        "id": "review-line-1",
        "speaker": "Dee",
        "started_offset_ms": 1234,
        "ended_offset_ms": 3856,
        "text": "🟢 We selected the green layout.",
        "text_offset": 0,
        "text_truncated": False,
    }
    second = {
        **first,
        "id": "review-line-2",
        "speaker": None,
        "started_offset_ms": 4000,
        "ended_offset_ms": 7300,
        "text": "Please send the mockup tomorrow.",
    }
    # The first page splits the line after the emoji and following space.
    pages = [
        memoket_api_page(
            [{**first, "text": first["text"][:2], "text_truncated": True}],
            next_offset=0,
            next_text_offset=2,
        ),
        memoket_api_page(
            [{**first, "text": first["text"][2:], "text_offset": 2}],
            text_offset=2,
            next_offset=1,
            next_text_offset=0,
        ),
        memoket_api_page([second], offset=1),
    ]
    return pages, memoket_api_page([first, second])


def test_memoket_api_transcript_preserves_citations_across_pages(
    work, tmp_path, memoket_api_transcript
):
    workspace, call = work
    pages, single = memoket_api_transcript
    pages[0]["note"] = "private response metadata"
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"pages": pages}), encoding="utf-8")
    source = call("file-source", str(path), "--format", "memoket")
    assert not workspace.exists()
    path.write_text(json.dumps(single), encoding="utf-8")
    assert call("file-source", str(path), "--format", "memoket") == source
    normalized = source["source"]["payload"]
    assert "private response metadata" not in json.dumps(source)
    assert normalized["metadata"] == {"source_reference": "product-review"}
    assert "started_at" not in normalized and "source_date" not in normalized
    assert normalized["segments"][0] == {
        "segment_id": "review-line-1",
        "speaker_id": "Dee",
        "start_ms": 1234,
        "end_ms": 3856,
        "text": "🟢 We selected the green layout.",
    }
    assert "speaker_id" not in normalized["segments"][1]
    annotations = {
        "facts": [
            {
                "content": "The team selected the green layout.",
                "segment_ids": ["review-line-1"],
                "topics": ["layout"],
                "status": "confirmed",
            }
        ]
    }
    payload = {
        "source": {"format": "memoket", "payload": {"pages": pages}},
        "annotations": annotations,
    }
    first = call("ingest", "--run-id", "paged", payload=payload)["receipt"]
    second = call("ingest", "--run-id", "single", payload={**source, "annotations": annotations})[
        "receipt"
    ]
    assert first["changed"] and not second["changed"] and first["checksum"] == second["checksum"]
    report = call("query", payload={"query": {"topics": ["layout"]}})["value"]
    (citation,) = report["timeline"][0]["evidence"]
    assert citation["source"] == "memoket" and citation["conversation_id"] == "product-review"
    assert (
        citation["segment_id"] == "review-line-1"
        and citation["text"] == normalized["segments"][0]["text"]
    )
    assert citation["revision_checksum"] == first["checksum"]
    assert call("integrity", "--include-workflows")["revisions"] == 1
    assert b"private response metadata" not in (workspace / "kite.sqlite3").read_bytes()


def test_memoket_api_transcript_preserves_recording_metadata(work, memoket_api_transcript):
    _, call = work
    pages, _ = memoket_api_transcript
    recording = {
        "id": "product-review",
        "title": "Product review",
        "started_at": "2026-04-02T10:00:00+08:00",
        "language_code": "en",
        "participant_details": ["private participant metadata"],
    }
    # UTF-16 cursors also count the full emoji and the trailing space.
    pages[0]["items"][0]["next_text_offset"] = 3
    pages[1]["items"][0]["text_offset"] = 3
    pages[1]["items"][0]["lines"][0]["text_offset"] = 3
    source = {"source": {"format": "memoket", "payload": {"pages": pages, "recording": recording}}}
    call("start", "memory-ingestion", "--run-id", "recording", payload=source)
    conversation = call("stage", "recording", "normalize")["value"]["conversation"]
    assert conversation["segments"][0]["text"] == "🟢 We selected the green layout."
    assert conversation["title"] == "Product review" and conversation["language"] == "en"
    assert conversation["started_at"] == recording["started_at"]
    assert "private participant metadata" not in json.dumps(conversation)


def test_file_source_reads_memoket_pages_and_selects_recording_metadata(
    work, tmp_path, memoket_api_transcript
):
    directory, call = work
    pages, single = memoket_api_transcript
    recording = {
        "id": "product-review",
        "title": "Product review",
        "started_at": "2026-04-02T10:00:00+08:00",
        "language_code": "en",
        "participant_details": ["private speaker details"],
    }
    metadata = {
        "error": None,
        "items": [{"id": "other-recording", "title": "private title"}, recording],
    }
    paths = [tmp_path / f"page-{number}.json" for number in range(len(pages))]
    metadata_path = tmp_path / "recordings.json"
    for path, page in zip(paths, pages, strict=True):
        path.write_text(json.dumps(page), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    command = (
        "file-source",
        str(paths[0]),
        "--format",
        "memoket",
        "--recording-metadata",
        str(metadata_path),
    )
    source = call(*command, "--page", str(paths[1]), "--page", str(paths[2]))
    assert "private" not in json.dumps(source)
    conversation = source["source"]["payload"]
    assert conversation["started_at"] == recording["started_at"]
    assert conversation["title"] == recording["title"] and conversation["language"] == "en"
    assert [line["segment_id"] for line in conversation["segments"]] == [
        "review-line-1",
        "review-line-2",
    ]
    call(*command, "--page", str(paths[2]), "--page", str(paths[1]), error="invalid_input")
    call(*command, "--page", str(tmp_path / "missing"), error="invalid_input")
    assert not directory.exists()
    for path, page in zip(paths, pages, strict=True):
        assert json.loads(path.read_text(encoding="utf-8")) == page
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == metadata
    paths[0].write_text(json.dumps(single), encoding="utf-8")
    assert call(*command) == source
    metadata_path.write_text(json.dumps(recording), encoding="utf-8")
    assert call(*command) == source
    metadata["items"].append(recording)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    result = call(*command, error="invalid_input")
    assert result["error"]["message"] == "Memoket metadata must contain one matching recording"
    call(
        "file-source",
        str(paths[0]),
        "--format",
        "text",
        "--source-id",
        "notes",
        "--page",
        str(paths[1]),
        error="invalid_input",
    )


def test_memoket_api_transcript_rejects_incomplete_or_inconsistent_exports(
    work, memoket_api_transcript
):
    _, call = work
    pages, _ = memoket_api_transcript
    wrong_recording = copy.deepcopy(pages)
    wrong_recording[1]["items"][0]["conversation_id"] = "private-recording"
    wrong_fragment = copy.deepcopy(pages)
    wrong_fragment[1]["items"][0]["lines"][0]["id"] = "private-line"
    wrong_offset = copy.deepcopy(pages)
    wrong_offset[0]["items"][0]["next_text_offset"] = 8
    wrong_total = copy.deepcopy(pages)
    for response in wrong_total:
        response["items"][0]["total_line_count"] = 3
    api_error = copy.deepcopy(pages[0])
    api_error["error"] = "private provider error"
    for payload in (
        pages[0],
        api_error,
        {"pages": pages[1:]},
        {"pages": [pages[0], pages[2]]},
        {"pages": pages[:-1]},
        {"pages": wrong_recording},
        {"pages": wrong_fragment},
        {"pages": wrong_offset},
        {"pages": wrong_total},
        {"pages": pages, "recording": {"id": "private-metadata"}},
    ):
        result = call(
            "start",
            "memory-ingestion",
            "--run-id",
            "bad",
            payload={"source": {"format": "memoket", "payload": payload}},
            error="invalid_input",
        )
        assert "private" not in json.dumps(result)
        call("status", "bad", error="not_found")


def test_file_source_accepts_custom_conversations_and_rejects_invalid_inputs(work, tmp_path):
    workspace, call = work
    path = tmp_path / "export"
    conversation = {
        "conversation_id": "record-1",
        "source": "archive",
        "metadata": {"source_reference": "export-123"},
        "segments": [{"segment_id": "line-1", "text": "Decided to ship."}],
    }
    path.write_text(json.dumps(conversation), encoding="utf-8")
    assert call("file-source", str(path), "--format", "conversation") == {
        "source": {"format": "conversation", "payload": conversation},
    }
    call("file-source", str(path), "--format", "text", error="invalid_input")
    call(
        "file-source",
        str(path),
        "--format",
        "conversation",
        "--source-id",
        "override",
        error="invalid_input",
    )
    path.write_bytes(b'{"code":0,"code":1}')
    call("file-source", str(path), "--format", "memoket", error="invalid_input")
    path.write_bytes(b"\xff")
    call(
        "file-source",
        str(path),
        "--format",
        "text",
        "--source-id",
        "malformed",
        error="invalid_input",
    )
    call("file-source", str(tmp_path), "--format", "memoket", error="invalid_input")
    assert not workspace.exists()


def _delete_source(call, run_id, target):
    call("start", "memory-deletion", "--run-id", run_id, payload={"target": target})
    token = call("stage", run_id, "preview")["value"]["approval_token"]
    return call("stage", run_id, "apply", "--approve", token)["value"]


def test_delete_middle_corrections_preserves_surviving_owner_history(work):
    directory, call = work
    previous = None
    for name, source, state in [
        ("initial", "text", "Ada"),
        ("middle-first", "archive", "private-cedar-interim"),
        ("middle-second", "archive", "private-cedar-followup"),
        ("current", "text", "Rae"),
    ]:
        payload = text_input(name, state)
        if source == "archive":
            payload["source"] = {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": source,
                    "metadata": {"source_reference": name},
                    "segments": [{"segment_id": "text-1", "text": state}],
                },
            }
        if previous is not None:
            payload["annotations"]["facts"][0]["supersedes"] = previous
        receipt = ingest(call, name, payload)
        previous = receipt["revision_id"] + "F1"

    query = {"topics": ["onboarding"], "state_key": "owner"}
    before = trace(call, "before-redaction", query, "task-tracing")
    assert before["current_reported_states"]["owner"]["value"] == "Rae"
    assert _delete_source(call, "erase-interim", {"source": "archive"})["deleted_revisions"] == 2

    after = trace(call, "after-redaction", query, "task-tracing")
    by_state = {row["state"]: row for row in after["timeline"]}
    assert set(by_state) == {"Ada", "Rae"}
    assert by_state["Rae"]["supersedes"] == by_state["Ada"]["fact_id"]
    assert by_state["Rae"]["effective"] and not by_state["Ada"]["effective"]
    assert after["current_reported_states"]["owner"]["value"] == "Rae"
    call("artifact", "before-redaction", "report", error="invalidated_run")
    assert b"private-cedar" not in (directory / "kite.sqlite3").read_bytes()

    _delete_source(call, "erase-ancestor", {"source": "text", "conversation_id": "initial"})
    remaining = trace(call, "after-ancestor", query, "task-tracing")
    assert len(remaining["timeline"]) == 1
    assert remaining["timeline"][0]["supersedes"] is None
    assert remaining["current_reported_states"]["owner"]["value"] == "Rae"


def test_deleting_unrelated_source_preserves_a_merge_into_parent_only_topic(work):
    _, call = work
    ingest(call, "call-notes", text_input("call-notes", "Call notes", topic="calls"))
    review = text_input("review", "Review notes", topic="review")
    review["annotations"]["taxonomy"] = [{"name": "review", "parents": ["follow-up"]}]
    ingest(call, "review", review)
    ingest(call, "unrelated", text_input("unrelated", "Unrelated", topic="unrelated"))

    call(
        "start",
        "memory-consolidation",
        "--run-id",
        "merge-follow-up",
        payload={"merge": {"from_topic": "calls", "to_topic": "follow-up"}},
    )
    call("stage", "merge-follow-up", "commit")
    call("stage", "merge-follow-up", "verify")
    before = trace(call, "before-unrelated-deletion", {"topics": ["calls"]})
    assert before["matched_count"] == 2
    _delete_source(call, "erase-unrelated", {"source": "text", "conversation_id": "unrelated"})
    after = trace(call, "after-unrelated-deletion", {"topics": ["calls"]})
    assert after["resolved_topics"] == before["resolved_topics"]
    assert after["timeline"] == before["timeline"]


@pytest.mark.parametrize("target", ["missing", "self"])
def test_integrity_rejects_dangling_or_cyclic_correction_links(work, target):
    directory, call = work
    receipt = ingest(call, "original", text_input("original", "Ada"))
    with sqlite3.connect(directory / "kite.sqlite3") as db:
        (stored,) = db.execute("SELECT payload FROM revisions").fetchone()
        record = json.loads(stored)
        record["facts"][0]["supersedes"] = (
            receipt["revision_id"] + "F1" if target == "self" else "missing-fact"
        )
        db.execute("UPDATE revisions SET payload=?", (json.dumps(record),))
    call("integrity", error="integrity_error")
    call("codebook", error="integrity_error")


def test_scoped_codebook_excludes_unselected_sources_and_cross_scope_metadata(work, tmp_path):
    _, call = work
    private = text_input("shared-id", "private-mint-transcript", topic="private-mint-topic")
    private["source"] = {
        "format": "conversation",
        "payload": {
            "conversation_id": "shared-id",
            "source": "archive",
            "title": "private-mint-title",
            "metadata": {"source_reference": "private-mint-reference"},
            "segments": [
                {
                    "segment_id": "text-1",
                    "text": "private-mint-transcript",
                    "speaker_id": "private-mint-speaker",
                }
            ],
        },
    }
    private["annotations"]["taxonomy"] = [
        {"name": "private-mint-topic", "aliases": ["private-mint-alias"]},
    ]
    private_fact = ingest(call, "private", private)["revision_id"] + "F1"
    public = text_input("shared-id", "Shared Ada", topic="public-release")
    public["annotations"]["facts"][0]["supersedes"] = private_fact
    original = ingest(call, "public-original", public)
    revised = text_input("shared-id", "Shared Rae", topic="public-release")
    revised["source"]["expected_checksum"] = original["checksum"]
    revised["annotations"]["facts"][0]["supersedes"] = original["revision_id"] + "F1"
    current_revision = ingest(call, "public-revision", revised, pipeline="memory-update")
    ingest(
        call, "other", text_input("other", "unselected-text-sibling", topic="unselected-sibling")
    )

    call(
        "start",
        "memory-consolidation",
        "--run-id",
        "merge-to-private",
        payload={"merge": {"from_topic": "public-release", "to_topic": "private-mint-topic"}},
    )
    call("stage", "merge-to-private", "commit")
    call("stage", "merge-to-private", "verify")
    before = call("integrity")
    inventory = call("sources", "--source", "text")
    assert inventory["memory_version"] == before["memory_version"]
    assert [item["conversation_id"] for item in inventory["sources"]] == ["other", "shared-id"]
    assert inventory["sources"][1] == {
        "source": "text",
        "conversation_id": "shared-id",
        "revisions": 2,
        "current_checksum": current_revision["checksum"],
    }
    inventory_text = json.dumps(call("sources"))
    assert "private-mint" not in inventory_text and "Shared Rae" not in inventory_text
    full = call("codebook")
    assert all(
        text in full
        for text in (
            "private-mint-transcript",
            "private_mint_topic",
            "private_mint_alias",
            "unselected-text-sibling",
        )
    )

    text_only = call("codebook", "--source", "text")
    assert "unselected-text-sibling" in text_only and "private-mint-transcript" not in text_only
    scoped = call("codebook", "--source", "text", "--conversation-id", "shared-id")
    assert "Shared Ada" in scoped and "Shared Rae" in scoped
    assert all(secret not in scoped for secret in ("private-mint", "private_mint", "unselected-"))
    assert "public_release" in scoped
    scoped_path = tmp_path / "scoped.xml"
    scoped_path.write_text(scoped, encoding="utf-8")
    assert Memory.load(scoped_path)
    evidence = (
        Codebook.load(scoped_path, strict=True)
        .query(
            SymbolicQuery(topics=("public release",)),
        )
        .evidence
    )
    assert {item.text for item in evidence if item.type == "fact"} == {"Shared Ada", "Shared Rae"}

    current = call(
        "codebook", "--source", "text", "--conversation-id", "shared-id", "--active-only"
    )
    assert "Shared Rae" in current and "Shared Ada" not in current
    assert "private-mint" not in current and "unselected-" not in current
    assert call("integrity") == before
    assert call("codebook") == full


def test_scoped_codebook_rejects_missing_or_ambiguous_selectors(work):
    _, call = work
    ingest(call, "note", text_input("note", "Known note"))
    call("codebook", "--conversation-id", "note", error="invalid_input")
    call("codebook", "--source", "TEXT", error="invalid_input")
    call("codebook", "--source", "archive", error="not_found")
    call("codebook", "--source", "text", "--conversation-id", "missing", error="not_found")
    call("sources", "--source", "TEXT", error="invalid_input")
    _delete_source(call, "erase-note", {"source": "text", "conversation_id": "note"})
    assert call("sources", "--source", "text")["sources"] == []
    call("codebook", "--source", "text", "--conversation-id", "note", error="not_found")


def test_source_inventory_paginates_one_workspace_snapshot(work, tmp_path):
    directory, call = work
    ingest(call, "second", text_input("second", "Synthetic second source."))
    ingest(call, "memoket", fixture("second-meeting"))
    ingest(call, "first", text_input("first", "Synthetic first source."))
    inventory = call("sources")
    assert "next_cursor" not in inventory
    before = (directory / "kite.sqlite3").read_bytes()
    first = call("sources", "--limit", "1")
    second = call("sources", "--limit", "1", "--cursor", first["next_cursor"])
    last = call("sources", "--limit", "1", "--cursor", second["next_cursor"])
    assert [item for page in (first, second, last) for item in page["sources"]] == inventory[
        "sources"
    ]
    assert last["next_cursor"] is None
    assert first["memory_version"] == second["memory_version"] == last["memory_version"]
    assert "Synthetic" not in json.dumps([first, second, last])
    assert (directory / "kite.sqlite3").read_bytes() == before
    call("sources", "--limit", "0", error="invalid_input")
    call("sources", "--limit", "1", "--cursor", "malformed", error="invalid_input")
    call("sources", "--cursor", first["next_cursor"], error="invalid_input")
    call("sources", "--limit", "2", "--cursor", first["next_cursor"], error="invalid_input")
    call(
        "sources",
        "--source",
        "text",
        "--limit",
        "1",
        "--cursor",
        first["next_cursor"],
        error="invalid_input",
    )
    scoped = call("sources", "--source", "text", "--limit", "1")
    scoped_next = call(
        "sources", "--source", "text", "--limit", "1", "--cursor", scoped["next_cursor"]
    )
    assert scoped["sources"] + scoped_next["sources"] == inventory["sources"][1:]
    backup = tmp_path / "copy"
    call("backup", str(backup))
    call(
        "sources",
        "--limit",
        "1",
        "--cursor",
        first["next_cursor"],
        workspace_dir=backup,
        error="invalid_input",
    )
    _delete_source(call, "erase", {"source": "text", "conversation_id": "first"})
    call("sources", "--limit", "1", "--cursor", first["next_cursor"], error="stale_snapshot")


def test_codebook_and_sources_read_committed_snapshot_during_a_write(work, monkeypatch):
    directory, call = work
    ingest(call, "selected", text_input("selected", "The selected transcript."))
    ingest(call, "other", text_input("other", "The unrelated transcript."))
    command = ("codebook", "--source", "text", "--conversation-id", "selected")
    inventory, full, scoped = call("sources"), call("codebook"), call(*command)
    assert "The selected transcript." in scoped and "The unrelated transcript." not in scoped
    connect = sqlite3.connect

    def quick_connect(*args, **kwargs):
        kwargs["timeout"] = 0.1
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", quick_connect)
    with closing(connect(directory / "kite.sqlite3")) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM revisions")
        assert call("sources") == inventory
        assert call("codebook") == full
        assert call(*command) == scoped
        writer.rollback()
        writer.execute("UPDATE memory SET xml=?", (b"corrupt index",))
        writer.commit()
    call("codebook", error="integrity_error")
    assert call(*command) == scoped


@pytest.fixture
def blocked_provider(monkeypatch):
    release = Event()
    requests = Queue()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.put(payload)
            if not release.wait(timeout=20):
                self.send_error(503)
                return
            candidate = {
                "facts": [
                    {
                        "content": "Candidate from the approved transcript",
                        "kind": "event",
                        "topics": ["general"],
                        "src": ["S1"],
                    },
                ]
            }
            data = json.dumps(
                {"choices": [{"message": {"content": json.dumps(candidate)}}]}
            ).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-placeholder")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield release, requests
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _begin_provider(call, repo=ROOT):
    payload = {"source": fixture("first-meeting")["source"], "model": {"name": "test-model"}}
    call("start", "memory-provider-ingestion", "--run-id", "provider", payload=payload, repo=repo)
    call("stage", "provider", "normalize", repo=repo)


def _cli_process(directory, *args, repo=ROOT):
    return [
        sys.executable,
        "-m",
        "memoket_kite.tools.cli",
        "--repo",
        str(repo),
        "--workspace",
        str(directory),
        *args,
    ]


@contextmanager
def _provider_process(directory, repo=ROOT):
    command = _cli_process(directory, "stage", "provider", "extract", "--allow-provider", repo=repo)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        yield process, command
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_provider_request_releases_workspace_and_keeps_other_labels_local(work, blocked_provider):
    directory, call = work
    release, requests = blocked_provider
    secret = text_input("unrelated", "privatequasar transcript", topic="privatequasar")
    secret["annotations"]["facts"][0]["entities"] = ["privatequasar"]
    ingest(call, "unrelated", secret)
    _begin_provider(call)

    with _provider_process(directory) as (process, command):
        request = requests.get(timeout=10)
        prompt = request["messages"][0]["content"]
        assert "We proposed a guided tour." in prompt
        assert "privatequasar" not in prompt

        status = subprocess.run(
            _cli_process(directory, "status", "provider"),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        assert json.loads(status.stdout)["stages"]["extract"]["state"] == "pending"
        duplicate = subprocess.run(command, capture_output=True, text=True, timeout=5)
        assert duplicate.returncode == 2
        assert json.loads(duplicate.stdout)["error"]["code"] == "provider_busy"
        assert requests.empty()
        release.set()
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors or output
        candidate = json.loads(output)
        assert candidate["checkpoint"]["stages"]["extract"]["attempts"] == 1
        assert candidate["value"]["facts"][0]["segment_ids"] == ["text-1"]
    assert call("stage", "provider", "extract")["manifest"] == candidate["manifest"]


@pytest.mark.parametrize("mutation", ["commit", "delete"])
def test_provider_response_cannot_restore_a_stale_or_deleted_run(work, blocked_provider, mutation):
    directory, call = work
    release, requests = blocked_provider
    _begin_provider(call)
    with _provider_process(directory) as (process, _):
        requests.get(timeout=10)
        if mutation == "commit":
            ingest(call, "new-source", text_input("new-source", "New memory"))
        else:
            _delete_source(call, "erase", {"source": "text", "conversation_id": "design-1"})
        release.set()
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 2, errors or output
        outcome = json.loads(output)
        assert outcome["error"]["code"] == (
            "stale_snapshot" if mutation == "commit" else "invalidated_run"
        )
        assert "value" not in outcome and "manifest" not in outcome
    status = call("status", "provider")
    assert status["stages"]["extract"]["attempts"] == 0
    assert not status["snapshot_current"]
    db = (directory / "kite.sqlite3").read_bytes()
    assert b"Candidate from the approved transcript" not in db
    if mutation == "delete":
        assert status["state"] == "invalidated"
        assert b"We proposed a guided tour." not in db


def test_killed_provider_process_does_not_strand_the_stage(work, blocked_provider):
    directory, call = work
    release, requests = blocked_provider
    _begin_provider(call)
    with _provider_process(directory) as (process, _):
        requests.get(timeout=10)
        process.kill()
        process.communicate(timeout=10)
    assert call("status", "provider")["stages"]["extract"]["state"] == "pending"

    with _provider_process(directory) as (retry, _):
        requests.get(timeout=10)
        release.set()
        output, errors = retry.communicate(timeout=10)
        assert retry.returncode == 0, errors or output
        assert json.loads(output)["checkpoint"]["stages"]["extract"]["state"] == "completed"


def test_provider_result_is_discarded_if_the_definition_changes(work, tmp_path, blocked_provider):
    directory, call = work
    release, requests = blocked_provider
    repo = tmp_path / "definitions"
    for folder in ("schemas", "skills", "pipeline_defs"):
        shutil.copytree(ROOT / folder, repo / folder)
    _begin_provider(call, repo)

    with _provider_process(directory, repo) as (process, _):
        requests.get(timeout=10)
        path = repo / "skills" / "memory" / "fact-extraction" / "SKILL.md"
        path.write_text(path.read_text() + "\nUpdated instructions.\n")
        release.set()
        output, errors = process.communicate(timeout=10)
        assert process.returncode == 2, errors or output
        assert json.loads(output)["error"]["code"] == "stale_definition"
    assert call("status", "provider", repo=repo)["stages"]["extract"]["attempts"] == 0
