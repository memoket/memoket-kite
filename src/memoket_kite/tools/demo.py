"""Exercise text import, Memoket import, checkpoint recovery and topic tracing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import ToolError
from .workflow import verify_run_storage
from .workspace import Workspace


def _run_demo(repo: Path, directory: Path, *, metrics: bool) -> dict:
    example = repo / "examples" / "agent-workflows"

    def call(*args: str) -> dict:
        # Each command starts a fresh process and loads the durable checkpoint.
        command = [
            sys.executable,
            "-m",
            "memoket_kite.tools.cli",
            "--repo",
            str(repo),
            "--workspace",
            str(directory),
            *args,
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode:
            raise ToolError("demo_error", f"demo command failed ({args[0]})")
        return json.loads(process.stdout)

    completed = 0
    replays = 0
    for number, name in enumerate(("first-meeting", "second-meeting"), 1):
        run_id = f"meeting-{number}"
        call(
            "start",
            "memory-ingestion",
            "--run-id",
            run_id,
            "--input",
            str(example / f"{name}.json"),
        )
        call("stage", run_id, "normalize", "--summary")
        if call("status", run_id)["stages"]["normalize"]["state"] != "completed":
            raise ToolError("demo_error", "normalized fixture did not resume")
        call("stage", run_id, "annotate", "--summary")
        commit = call("stage", run_id, "commit", "--summary")
        if call("stage", run_id, "commit", "--summary")["manifest"] != commit["manifest"]:
            raise ToolError("demo_error", "fixture commit was not idempotent")
        replays += 1
        completed += call("stage", run_id, "verify", "--summary")["run_state"] == "completed"

    call(
        "start", "topic-tracing", "--run-id", "trace", "--input", str(example / "topic-query.json")
    )
    for stage in ("resolve", "retrieve"):
        call("stage", "trace", stage, "--summary")
    result = call("stage", "trace", "report")
    completed += result["checkpoint"]["state"] == "completed"
    report = result["value"]
    decision_correct = (
        report["current_reported_states"].get("onboarding-design", {}).get("value")
        == "short checklist"
    )
    statuses = [item["status"] for item in report["timeline"]]
    if (
        completed != 3
        or not decision_correct
        or statuses
        != [
            "proposal",
            "rejected",
            "confirmed",
            "confirmed",
        ]
    ):
        raise ToolError("demo_error", "fixture report differed from expected decisions")
    evidence_ids = {
        (line["conversation_id"], line["segment_id"])
        for row in report["timeline"]
        for line in row["evidence"]
    }
    expected_ids = {("design-1", "text-1"), ("184", "line-a"), ("184", "line-b")}
    if evidence_ids != expected_ids or any(not row["evidence"] for row in report["timeline"]):
        raise ToolError("demo_error", "fixture report differed from expected citations")
    if metrics:
        return {
            "mode": "deterministic synthetic fixture",
            "pipelines_completed": completed,
            "pipelines_started": 3,
            "idempotent_write_replays": replays,
            "facts_with_source": sum(bool(row["evidence"]) for row in report["timeline"]),
            "facts_returned": report["matched_count"],
            "expected_citations_found": len(evidence_ids & expected_ids),
            "expected_citations": len(expected_ids),
            "decision_state_correct": decision_correct,
        }
    return report


def run_demo(repo: Path, *, metrics: bool = False, save_workspace: Path | None = None) -> dict:
    with TemporaryDirectory(prefix="kite-demo-") as path:
        directory = Path(path)
        report = _run_demo(repo, directory, metrics=metrics)
        if save_workspace is not None:
            Workspace.backup(
                directory, save_workspace, verify_runs=lambda copy: verify_run_storage(copy, repo)
            )
        return report


def render_demo(report: dict) -> str:
    lines = ["KITE offline demo", "Two synthetic meetings. No model calls.", ""]
    for key, label in (
        ("onboarding-design", "Confirmed onboarding design"),
        ("checklist-owner", "Implementation owner"),
    ):
        state = report["current_reported_states"].get(key)
        if state and state["status"] == "confirmed":
            lines.append(f"{label}  {state['value']}")
    lines.extend(("", "Evidence across meetings"))
    shown = set()
    for row in report["timeline"]:
        day = row["source_day"] or "Date unknown"
        for evidence in row["evidence"]:
            identity = (row["status"], evidence["source_line_id"])
            if identity in shown:
                continue
            shown.add(identity)
            source = f"{evidence['source']}/{evidence['conversation_id']}#{evidence['segment_id']}"
            speaker = f"  {evidence['speaker_id']}" if "speaker_id" in evidence else ""
            lines.append(f"{day}  {row['status'].upper()}  {source}{speaker}")
            lines.extend(f"  > {line}" for line in evidence["text"].splitlines())
    return "\n".join(lines)
