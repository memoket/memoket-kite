"""Verify the agent lifecycle from a clean package installation."""

from __future__ import annotations

import json
import os
import subprocess
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
        metrics = call("demo", "--metrics")["value"]
        assert metrics["pipelines_completed"] == 3 and metrics["decision_state_correct"]
        assert metrics["expected_citations_found"] == metrics["expected_citations"] == 3

        quote = "The launch review is Tuesday."
        (directory / "notes.txt").write_text(f"Launch notes\n\n{quote}\n", encoding="utf-8")
        source = call(
            "file-source", "notes.txt", "--format", "text", "--source-id", "note", "--split-lines"
        )
        run_id = call("start", "memory-ingestion", payload=source)["run_id"]
        call("stage", run_id, "normalize", "--summary")
        call("stage", run_id, "annotate", error="missing_input")
        assert call("runs")["runs"][0]["run_id"] == run_id
        backup = call("backup", str(recovered))
        assert (backup["revisions"], backup["verified_runs"], backup["verified_artifacts"]) == (
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
        query = {"query": {"topics": ["launch review"]}}
        trace = call("start", "topic-tracing", payload=query, workspace=recovered)["run_id"]
        for stage in ("resolve", "retrieve"):
            call("stage", trace, stage, "--summary", workspace=recovered)
        report = call("stage", trace, "report", workspace=recovered)["value"]
        assert report["matched_count"] == 1 and report["next_cursor"] is None
        assert call("query", payload=query, workspace=recovered)["value"] == report
        evidence = report["timeline"][0]["evidence"][0]
        assert (evidence["conversation_id"], evidence["segment_id"], evidence["text"]) == (
            "note",
            "text-line-3",
            quote,
        )

        target = {"target": {"source": "text", "conversation_ids": ["note"]}}
        removal = call("start", "memory-deletion", payload=target, workspace=recovered)["run_id"]
        plan = call("stage", removal, "preview", workspace=recovered)["value"]
        assert plan["revisions"] == plan["facts"] == 1
        call("stage", removal, "apply", workspace=recovered, error="awaiting_approval")
        receipt = call(
            "stage", removal, "apply", "--approve", plan["approval_token"], workspace=recovered
        )
        assert receipt["value"]["deleted_revisions"] == 1
        call(
            "start", "memory-ingestion", payload=source, workspace=recovered, error="deleted_source"
        )
        assert call("integrity", workspace=recovered)["revisions"] == 0
        assert call("integrity")["revisions"] == 0
        assert call("status", run_id)["state"] == "failed"
        print(
            "Installed agent smoke passed: packaged workflows, backup recovery, citations, deletion."
        )


if __name__ == "__main__":
    main()
