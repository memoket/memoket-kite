import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.agent import revision_eval, symbolic_fixture
from benchmarks.agent.context_eval import read_corpus
from memoket_kite.tools.workspace import Workspace


@pytest.mark.parametrize("arm", ["files", "kite"])
def test_revision_benchmark_changes_sources_and_resumes_with_edited_project(
    tmp_path, monkeypatch, arm
):
    fixture = tmp_path / "fixture"
    symbolic_fixture.prepare(fixture)
    states = json.loads((fixture / "cases.json").read_text())
    batch = fixture / "revision.json"
    records = [
        item.record
        for item in read_corpus(fixture / "after/sources", "text")
        if item.name != "initial.txt"
    ]
    batch.write_text(json.dumps({"sources": records}), encoding="utf-8")
    case = {
        "id": "orchard",
        "seed": "seed",
        "source_format": "text",
        "states": states,
        "batch": batch,
    }
    revision_eval._prepare_case(case, fixture)
    session = "resumable"
    viewed = []

    def run_agent(_bin, _model, project, _prompt, **options):
        assert options["persist_session"]
        assert options["codex_options"][:2] == ("--disable", "apps")
        update = bool(viewed)
        assert options["resume_session"] == (session if update else None)
        if arm == "files":
            visible = {file.stem for file in (project / "sources").glob("*.txt")}
            assert (project / "sources" / "RECORDINGS.json").is_file()
        else:
            assert not (project / "sources").exists()
            listener = next(
                arg
                for arg in options["codex_options"]
                if arg.startswith("mcp_servers.kite_context.args=")
            )
            db = Path(json.loads(listener.partition("=")[2])[-1])
            with Workspace.open(db, read_only=True) as workspace:
                assert workspace.version() == (2 if update else 1)
                visible = {row["conversation"]["conversation_id"] for row in workspace.records()}
        assert visible == ({"initial", "proposal", "revision"} if update else {"initial"})
        policy = project / "policy.py"
        if update:
            assert "'max_signups': 15" in policy.read_text()
        data = (
            {"regions": ["North", "East"], "max_signups": 9}
            if update
            else {"regions": ["North", "West"], "max_signups": 15}
        )
        policy.write_text(f"def policy():\n    return {data!r}\n", encoding="utf-8")
        viewed.append(visible)
        recording = "revision" if update else "initial"
        return {
            "session_id": session,
            "answer": f"{recording}#text-line-2 {recording}#text-line-3",
            "error": None,
            "commands": [{"exit_code": 0, "output": "Ran 1 test in 0.001s\n\nOK"}],
        }

    monkeypatch.setattr(revision_eval, "codex_trial", run_agent)
    args = SimpleNamespace(codex="codex", model="offline", kite_python=sys.executable)
    trial = revision_eval._trial(args, case, arm, 1)
    assert trial["passed"] and len(trial["turns"]) == 2
    assert len(viewed) == 2 and trial["update_seconds"] >= 0
