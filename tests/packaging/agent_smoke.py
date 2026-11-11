"""Verify the agent lifecycle from a clean package installation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from tempfile import TemporaryDirectory

import memoket_kite


def main() -> None:
    name = "kite-tool.exe" if os.name == "nt" else "kite-tool"
    command = Path(sysconfig.get_path("scripts")) / name
    with TemporaryDirectory(prefix="kite-installed-") as temporary:
        directory = Path(temporary)
        original = directory / "original"
        recovered = directory / "recovered"

        def call(*args: str, workspace: Path = original, payload=None, error=None) -> dict:
            argv = [str(command), "--workspace", str(workspace), *args]
            if payload is not None:
                argv.extend(("--input", "-"))
            process = subprocess.run(
                argv,
                input=json.dumps(payload) if payload is not None else None,
                capture_output=True,
                text=True,
                timeout=45,
                cwd=directory,
                check=False,
            )
            assert process.returncode == (2 if error else 0), (
                process.args,
                process.stdout,
                process.stderr,
            )
            result = json.loads(process.stdout)
            if error:
                assert result["error"]["code"] == error, result
            return result

        # Packaged resources take precedence over unrelated files in the user's directory.
        (directory / "pipeline_defs").mkdir()
        (directory / "pipeline_defs" / "memory-ingestion.yaml").write_text("invalid: true\n")
        assets = Path(call("resources")["root"])
        assert assets == Path(memoket_kite.__file__).resolve().parent / "_agent_assets"
        for path in (
            "AGENTS.md",
            "AGENT_GUIDE.md",
            "CLAUDE.md",
            "CONTRIBUTING.md",
            "examples/agent-workflows/extensions.md",
        ):
            assert (assets / path).is_file(), path
        extension = directory / "incident project"
        shutil.copytree(assets / "examples" / "agent-workflows" / "incident-log", extension)
        incident_workspace = directory / "incident workspace"
        ext = ("--extensions", str(extension))
        name = "example-incident-review"
        catalog = call(*ext, "pipelines", workspace=incident_workspace)["pipelines"]
        assert name in {pipeline["id"] for pipeline in catalog}
        assert call(*ext, "validate-pipeline", name, workspace=incident_workspace)[
            "skill_root"
        ] == str(extension.resolve())
        converted = directory / "incident conversation.json"
        converted.write_bytes(
            subprocess.check_output(
                [sys.executable, str(extension / "connector.py"), str(extension / "export.json")],
                cwd=directory,
                timeout=15,
            )
        )
        source = call(
            "file-source", str(converted), "--format", "conversation", workspace=incident_workspace
        )
        imported = call(
            "ingest",
            "--annotations",
            str(extension / "annotations.json"),
            workspace=incident_workspace,
            payload=source,
        )
        assert imported["receipt"]["changed"] and imported["integrity"]["facts"] == 1
        run = call(
            *ext,
            "start",
            name,
            workspace=incident_workspace,
            payload={"query": {"topics": ["alerts"], "source": "sample-incident-log"}},
        )["run_id"]
        for stage in ("resolve", "retrieve"):
            call(*ext, "stage", run, stage, "--summary", workspace=incident_workspace)
        report = call(*ext, "stage", run, "report", workspace=incident_workspace)["value"]
        (incident,) = report["timeline"]
        (line,) = incident["evidence"]
        assert (line["source"], line["conversation_id"], line["segment_id"]) == (
            "sample-incident-log",
            "incident-42",
            "update-1",
        )
        assert line["text"] == "We moved the alert review to Tuesday."
        assert (line["speaker_id"], line["start_ms"]) == ("oncall", 1250)
        assert incident["source_time"] == "2026-09-08T10:00:00Z"

        metrics = call("demo", "--metrics")["value"]
        assert metrics["pipelines_completed"] == 3 and metrics["decision_state_correct"]
        assert metrics["expected_citations_found"] == metrics["expected_citations"] == 3
        demo_workspace = directory / "demo workspace"
        demo = subprocess.check_output(
            [
                str(command),
                "--workspace",
                str(original),
                "demo",
                "--text",
                "--save-workspace",
                str(demo_workspace),
            ],
            text=True,
            cwd=directory,
            timeout=45,
        )
        assert "Confirmed onboarding design  short checklist" in demo
        assert "Implementation owner  Ravi" in demo
        assert demo.index("text/design-1#text-1") < demo.index("memoket/184#line-a")
        assert demo.index("memoket/184#line-a") < demo.index("memoket/184#line-b")
        assert demo.count("memoket/184#line-b") == 1
        assert "We decided to ship a short checklist. Ravi will implement it." in demo
        saved = call("integrity", "--include-workflows", workspace=demo_workspace)
        assert saved["revisions"] == 2 and saved["verified_runs"] == 3
        topics = call("topics", "--contains", "first run", workspace=demo_workspace)
        (onboarding,) = topics["topics"]
        assert onboarding["code"] == "onboarding" and onboarding["direct_fact_count"] == 4
        report = call("query", "--topic", "first run", workspace=demo_workspace)["value"]
        assert report == call("artifact", "trace", "report", workspace=demo_workspace)["value"]
        readable = subprocess.check_output(
            [
                str(command),
                "--workspace",
                str(demo_workspace),
                "query",
                "--pipeline",
                "decision-evolution",
                "--contains",
                "guided tour",
                "--follow-states",
                "--text",
            ],
            cwd=directory,
            text=True,
            timeout=45,
        )
        assert "onboarding-design  Confirmed  short checklist  [3]" in readable
        assert "Followed states  onboarding-design" in readable
        assert readable.count("memoket/184#line-b") == 1
        assert report["timeline"][2]["evidence"][0]["source_line_id"] in readable
        call("demo", "--save-workspace", str(demo_workspace), error="conflict")
        assert call("integrity", "--include-workflows", workspace=demo_workspace) == saved
        call("source-history", "text", "note", error="not_found")
        assert not original.exists()

        (directory / "captions.vtt").write_text(
            "WEBVTT\n\ncue-1\n00:01.250 --> 00:03.500\n<v Ada>Let's ship it.</v>\n",
            encoding="utf-8",
        )
        captions = call("file-source", "captions.vtt", "--format", "vtt", "--source-id", "captions")
        (cue,) = captions["source"]["payload"]["segments"]
        assert (cue["source_sentence_id"], cue["speaker_id"], cue["start_ms"], cue["end_ms"]) == (
            "cue-1",
            "Ada",
            1250,
            3500,
        )

        quote = "The launch review is Tuesday."
        (directory / "notes.txt").write_text(f"Launch notes\n\n{quote}\n", encoding="utf-8")
        source = call(
            "file-source",
            "notes.txt",
            "--format",
            "text",
            "--source-id",
            "note",
            "--split-lines",
            "--project",
            "Launch pilot",
        )
        run_id = call("start", "memory-ingestion", payload=source)["run_id"]
        call("stage", run_id, "normalize", "--summary")
        call("stage", run_id, "annotate", error="missing_input")
        assert call("runs")["runs"][0]["run_id"] == run_id
        (resumable,) = call("runs", "--resumable")["runs"]
        assert resumable["run_id"] == run_id
        assert resumable["resume"]["stages"][0]["missing_inputs"] == {"annotations": "annotations"}
        backup = call("backup", str(recovered))
        assert (backup["revisions"], backup["verified_runs"], backup["verified_artifacts"]) == (
            0,
            1,
            1,
        )
        verified = call("integrity", "--include-workflows", workspace=recovered)
        assert (
            verified["revisions"],
            verified["verified_runs"],
            verified["verified_artifacts"],
        ) == (
            0,
            1,
            1,
        )
        status = call("status", run_id, workspace=recovered)
        assert status["resume"]["stages"][0]["missing_inputs"] == {"annotations": "annotations"}
        page = call(
            "segments", run_id, "normalize", "--offset", "1", "--limit", "1", workspace=recovered
        )
        assert page["next_offset"] is None and page["segments"][0]["text"] == quote

        annotations = {
            "facts": [
                {"content": quote, "segment_ids": ["text-line-3"], "topics": ["launch review"]}
            ]
        }
        result = call(
            "stage",
            run_id,
            "annotate",
            "--summary",
            payload={"annotations": annotations},
            workspace=recovered,
        )
        assert result["retry_count"] == 1
        for stage in ("commit", "verify"):
            call("stage", run_id, stage, "--summary", workspace=recovered)
        query = {"query": {"topics": ["launch review"], "projects": ["Launch pilot"]}}
        trace = call("start", "topic-tracing", payload=query, workspace=recovered)["run_id"]
        for stage in ("resolve", "retrieve"):
            call("stage", trace, stage, "--summary", workspace=recovered)
        report = call("stage", trace, "report", workspace=recovered)["value"]
        assert report["matched_count"] == 1 and report["next_cursor"] is None
        assert call("query", payload=query, workspace=recovered)["value"] == report
        limited = {"query": {**query["query"], "evidence_byte_budget": 128}}
        budgeted = call("query", payload=limited, workspace=recovered)["value"]
        assert (
            budgeted["evidence_budget"]["over_budget"]
            and budgeted["timeline"] == report["timeline"]
        )
        evidence = report["timeline"][0]["evidence"][0]
        detail = call("evidence", evidence["source_line_id"], workspace=recovered)["value"]
        assert detail["evidence"] == evidence and not detail["before"] and not detail["after"]
        limited = call(
            "evidence",
            evidence["source_line_id"],
            "--context",
            "1",
            "--byte-budget",
            "128",
            workspace=recovered,
        )["value"]
        assert limited["evidence"] == evidence and limited["context_budget"]["over_budget"]
        assert limited["context_budget"]["omitted_before"] == 1 and not limited["before"]
        assert (evidence["conversation_id"], evidence["segment_id"], evidence["text"]) == (
            "note",
            "text-line-3",
            quote,
        )
        history = call("source-history", "text", "note", workspace=recovered)
        (revision,) = history["revisions"]
        assert revision["active"] and revision["source_day"] is None
        assert revision["segments"] == 2 and revision["facts"] == 1
        assert history["current_checksum"] == evidence["revision_checksum"]

        target = {"target": {"source": "text", "conversation_ids": ["note"]}}
        removal = call("start", "memory-deletion", payload=target, workspace=recovered)["run_id"]
        plan = call("stage", removal, "preview", workspace=recovered)["value"]
        assert plan["revisions"] == plan["facts"] == 1
        impact = call("deletion-impact", removal, workspace=recovered)["value"]
        assert impact["committed_conversation_ids"] == ["note"]
        assert impact["committed_conversations"] == 1 and impact["source_lines"] == 2
        assert plan["approval_token"] not in json.dumps(impact) and quote not in json.dumps(impact)
        call("stage", removal, "apply", workspace=recovered, error="awaiting_approval")
        receipt = call(
            "stage", removal, "apply", "--approve", plan["approval_token"], workspace=recovered
        )
        assert receipt["value"]["deleted_revisions"] == 1
        call(
            "start", "memory-ingestion", payload=source, workspace=recovered, error="deleted_source"
        )
        assert call("integrity", workspace=recovered)["revisions"] == 0
        rejected = directory / "rejected"
        call(
            "backup",
            str(rejected),
            "--require-deletions-from",
            str(recovered),
            error="stale_backup",
        )
        assert not rejected.exists()
        assert call("integrity")["revisions"] == 0
        assert call("status", run_id)["state"] == "failed"
        scripted = directory / "scripted"
        reviewed = directory / "reviewed annotations.json"
        reviewed.write_text(json.dumps({"annotations": annotations}), encoding="utf-8")
        imported = call(
            "ingest",
            "--run-id",
            "scripted",
            "--annotations",
            str(reviewed),
            workspace=scripted,
            payload=source,
        )
        assert imported["receipt"]["changed"] and imported["integrity"]["facts"] == 1
        assert call("status", "scripted", workspace=scripted)["state"] == "completed"
        assert call("ingest", "--resume", "scripted", workspace=scripted) == imported
        amended = call(
            "ingest",
            "--update",
            "--run-id",
            "amended",
            workspace=scripted,
            payload={
                "source": {
                    **source["source"],
                    "expected_checksum": imported["receipt"]["checksum"],
                },
                "annotations": {
                    "facts": [
                        {
                            "content": "Tuesday is the review day.",
                            "segment_ids": ["text-line-3"],
                            "topics": ["launch review"],
                        }
                    ]
                },
            },
        )
        assert amended["receipt"]["changed"]
        assert amended["receipt"]["revision_id"] == imported["receipt"]["revision_id"]
        assert amended["integrity"]["revisions"] == 1 and amended["integrity"]["facts"] == 2
        assert call("ingest", "--resume", "amended", workspace=scripted) == amended
        print(
            "Installed agent smoke passed: packaged workflows, backup recovery, citations, deletion."
        )


if __name__ == "__main__":
    main()
