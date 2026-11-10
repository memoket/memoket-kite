"""Exercise text import, Memoket import, checkpoint recovery and topic tracing."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def run_demo(directory: Path, *, metrics: bool = False) -> dict:
    example = Path(__file__).resolve().parent
    repo = example.parents[1]

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
            raise RuntimeError(process.stdout or process.stderr)
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
        call("stage", run_id, "normalize")
        assert call("status", run_id)["stages"]["normalize"]["state"] == "completed"
        call("stage", run_id, "annotate")
        commit = call("stage", run_id, "commit")
        assert call("stage", run_id, "commit")["manifest"] == commit["manifest"]
        replays += 1
        completed += call("stage", run_id, "verify")["checkpoint"]["state"] == "completed"

    call(
        "start", "topic-tracing", "--run-id", "trace", "--input", str(example / "topic-query.json")
    )
    for stage in ("resolve", "retrieve", "report"):
        result = call("stage", "trace", stage)
    completed += result["checkpoint"]["state"] == "completed"
    report = result["value"]
    assert report["current_reported_states"]["onboarding-design"]["value"] == "short checklist"
    assert [item["status"] for item in report["timeline"]] == [
        "proposal",
        "rejected",
        "confirmed",
        "confirmed",
    ]
    if metrics:
        evidence_ids = {
            (line["conversation_id"], line["segment_id"])
            for row in report["timeline"]
            for line in row["evidence"]
        }
        expected_ids = {("design-1", "text-1"), ("184", "line-a"), ("184", "line-b")}
        return {
            "mode": "deterministic synthetic fixture",
            "pipelines_completed": completed,
            "pipelines_started": 3,
            "idempotent_write_replays": replays,
            "facts_with_source": sum(bool(row["evidence"]) for row in report["timeline"]),
            "facts_returned": report["matched_count"],
            "expected_citations_found": len(evidence_ids & expected_ids),
            "expected_citations": len(expected_ids),
            "decision_state_correct": report["current_reported_states"]["onboarding-design"][
                "value"
            ]
            == "short checklist",
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", action="store_true", help="emit deterministic fixture metrics")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="kite-demo-") as path:
        print(json.dumps(run_demo(Path(path), metrics=args.metrics), indent=2))
