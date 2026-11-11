"""Run offline examples of source-backed conversation memory."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import ToolError
from .workflow import verify_run_storage
from .workspace import Workspace


def _demo_cli(repo: Path, directory: Path):
    def call(*args: str, data: dict | None = None) -> dict:
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
        process = subprocess.run(
            command,
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode:
            raise ToolError("demo_error", f"demo command failed ({args[0]})")
        return json.loads(process.stdout)

    return call


def _run_demo(repo: Path, directory: Path, *, metrics: bool) -> dict:
    example = repo / "examples" / "agent-workflows"
    call = _demo_cli(repo, directory)
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


def _decision_story(repo: Path, directory: Path) -> dict:
    call = _demo_cli(repo, directory)
    fixture = repo / "examples" / "agent-workflows" / "release-decisions.json"
    for meeting in json.loads(fixture.read_text(encoding="utf-8")):
        run_id = meeting["source"]["payload"]["conversation_id"]
        call("ingest", "--run-id", run_id, "--input", "-", data=meeting)

    query = ("query", "--pipeline", "decision-evolution", "--topic", "release plan")
    historical = call(*query, "--end-date", "2026-11-04")["value"]
    latest = call(*query)["value"]
    from_old_owner = call(*query, "--contains", "Ravi", "--follow-states")["value"]
    approval = call(*query, "--event", "customer approval")["value"]
    current_owner = latest["current_reported_states"]["release-owner"]
    fact = next(row for row in latest["timeline"] if row["fact_id"] in current_owner["fact_ids"])
    quote = call("evidence", fact["evidence"][0]["source_line_id"])["value"]["evidence"]
    record = {key: fact[key] for key in ("topics", "source_day", "status", "state_key", "state")}
    record["source"] = f"{quote['source']}/{quote['conversation_id']}#{quote['segment_id']}"
    return {
        "historical": historical,
        "latest": latest,
        "from_old_owner": from_old_owner,
        "approval": approval,
        "record": record,
        "quote": quote["text"],
    }


def run_demo(
    repo: Path,
    *,
    metrics: bool = False,
    decision_story: bool = False,
    save_workspace: Path | None = None,
) -> dict:
    with TemporaryDirectory(prefix="kite-demo-") as path:
        directory = Path(path)
        report = (
            _decision_story(repo, directory)
            if decision_story
            else _run_demo(repo, directory, metrics=metrics)
        )
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


def render_decision_story(story: dict) -> str:
    def answer(report: dict) -> str:
        states = report["current_reported_states"]
        return f"{states['release-owner']['value']} owns the release. Ship {states['ship-day']['value']}."

    latest = story["latest"]
    historical = story["historical"]
    followed = story["from_old_owner"]["current_reported_states"]["release-owner"]
    lines = [
        "Who owns the release, and when are we shipping?",
        "Three sample meetings. Each query runs in a fresh process against local KITE memory.",
        "",
        f"As of {historical['end_date']}  {answer(historical)}",
        f"Latest record     {answer(latest)}",
        f"Starting from Ravi's earlier mention leads to {followed['value']}.",
        "",
        "Decision history",
    ]
    for row in latest["timeline"]:
        evidence = row["evidence"][0]
        source = f"{evidence['conversation_id']}#{evidence['segment_id']}"
        lines.append(f"{row['source_day']}  {row['status']:<9} {row['content']} [{source}]")
    if story["approval"]["matched_count"] == 0:
        lines.extend(("", "Customer approval? No evidence in these meetings."))

    lines.extend(("", "Memory record for the current owner", json.dumps(story["record"], indent=2)))
    lines.extend(("Original quote", f"  > {story['quote']}"))
    return "\n".join(lines)
