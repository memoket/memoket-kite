"""Install KITE via Codex app-server and run its packaged launcher in a fresh project."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def install_plugin(codex: str, marketplace: dict, project: Path, env: dict) -> Path:
    with subprocess.Popen(
        [codex, "app-server"],
        cwd=project,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as server:
        request_id = 0

        def request(method: str, **params):
            nonlocal request_id
            request_id += 1
            server.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}))
            server.stdin.write("\n")
            server.stdin.flush()
            for line in server.stdout:
                response = json.loads(line)
                if response.get("id") == request_id:
                    assert "error" not in response, (method, response)
                    return response["result"]
            raise RuntimeError(f"Codex app-server closed while handling {method}")

        try:
            request("initialize", clientInfo={"name": "kite-smoke", "version": "0.1.0"})
            server.stdin.write('{"method":"initialized"}\n')
            server.stdin.flush()
            added = request("marketplace/add", **marketplace)
            assert added["marketplaceName"] == "memoket", added

            def find_marketplace():
                listing = request("plugin/list", cwds=[str(project)], marketplaceKinds=["local"])
                return next(
                    entry for entry in listing["marketplaces"] if entry["name"] == "memoket"
                )

            entry = find_marketplace()
            plugin = next(item for item in entry["plugins"] if item["name"] == "kite")
            assert entry["interface"]["displayName"] == "Memoket", entry
            assert plugin["interface"]["displayName"] == "KITE", plugin
            assert not plugin["installed"] and plugin["installPolicy"] == "AVAILABLE", plugin
            installed = request("plugin/install", marketplacePath=entry["path"], pluginName="kite")
            assert installed["appsNeedingAuth"] == [], installed
            plugin = next(item for item in find_marketplace()["plugins"] if item["name"] == "kite")
            assert plugin["installed"] and plugin["enabled"], plugin

            catalog = request("skills/list", cwds=[str(project)], forceReload=True)["data"][0]
            skill = next(item for item in catalog["skills"] if item["name"] == "kite:kite")
            path = Path(skill["path"])
            assert skill["enabled"] and Path(env["CODEX_HOME"]) in path.parents, skill
            launcher = path.parent / "scripts" / "kite.sh"
            assert launcher.is_file(), launcher
            for name in ("import.md", "query.md"):
                assert (path.parent / "references" / name).is_file(), name
            return launcher
        finally:
            server.terminate()
            server.wait(timeout=15)


def verify_workflows(launcher: Path, project: Path, scratch: Path, env: dict) -> None:
    def run(*args: str | Path, cwd: Path = project, data: dict | None = None, check: bool = True):
        return subprocess.run(
            ["bash", str(launcher), *map(str, args)],
            cwd=cwd,
            env=env,
            input=json.dumps(data) if data is not None else None,
            text=True,
            stdout=subprocess.PIPE,
            check=check,
            timeout=300,
        )

    def submit(prompt: str, cwd: Path = project) -> str:
        return subprocess.run(
            [sys.executable, "-I", str(launcher.with_name("codex_context.py"))],
            input=json.dumps(
                {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(cwd)}
            ),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            timeout=10,
            check=True,
        ).stdout

    assert not submit("KITE: What did we decide?")
    assert not (Path(env["XDG_CACHE_HOME"]) / "kite" / "codex").exists()
    demo = run("demo", "--decision-story").stdout
    assert "Maya owns the release. Ship Tuesday." in demo, demo
    assert "Starting from Ravi's earlier mention leads to Maya." in demo, demo
    assert '"source": "text/release-handoff#text-1"' in demo, demo
    env["UV_OFFLINE"] = "1"
    unavailable_uv_cache = scratch / "unavailable uv cache"
    unavailable_uv_cache.touch()
    env["UV_CACHE_DIR"] = str(unavailable_uv_cache)

    notes = scratch / "Launch review.md"
    quote = "We decided to ship a short checklist."
    notes.write_text(f"Launch review\n\n{quote}\n", encoding="utf-8")
    source = json.loads(
        run(
            "file-source", notes, "--format", "text", "--source-id", "launch", "--split-lines"
        ).stdout
    )
    assert json.loads(run("source-check", "--input", "-", data=source).stdout)["state"] == "new"
    pipeline = json.loads(run("validate-pipeline", "memory-ingestion").stdout)["definition"]
    run("start", pipeline["id"], "--run-id", "launch", "--input", "-", data=source)
    normalized = json.loads(run("stage", "launch", "normalize").stdout)["value"]["conversation"]
    segment = next(item for item in normalized["segments"] if item["text"] == quote)
    annotations = {
        "annotations": {
            "taxonomy": [{"name": "onboarding"}],
            "facts": [
                {
                    "content": quote,
                    "segment_ids": [segment["segment_id"]],
                    "topics": ["onboarding"],
                    "status": "confirmed",
                    "state_key": "onboarding-design",
                    "state": "short checklist",
                }
            ],
        }
    }
    run("stage", "launch", "annotate", "--input", "-", data=annotations)
    run("stage", "launch", "commit")
    verified = json.loads(run("stage", "launch", "verify").stdout)
    assert verified["checkpoint"]["state"] == "completed", verified
    replay = json.loads(run("source-check", "--input", "-", data=source).stdout)
    assert replay["state"] == "unchanged", replay

    subdirectory = project / "subdirectory"
    subdirectory.mkdir()
    report = json.loads(run("query", "--topic", "onboarding", cwd=subdirectory).stdout)["value"]
    assert report["current_reported_states"]["onboarding-design"]["value"] == "short checklist"
    citation = report["timeline"][0]["evidence"][0]
    assert (citation["conversation_id"], citation["segment_id"], citation["text"]) == (
        "launch",
        "text-line-3",
        quote,
    ), citation
    transcripts = scratch / "Transcripts"
    transcripts.mkdir()
    handoff = transcripts / "handoff_transcript.txt"
    handoff.write_text("00:01\nSpeaker A\n\nRollout depends on the checklist.\n", encoding="utf-8")
    (transcripts / "handoff.json").write_text(
        '{"conversation_id":"handoff","started_at":"2026-01-14 08:00:00"}', encoding="utf-8"
    )
    indexed = json.loads(
        run(
            "index-sources",
            "--transcripts",
            transcripts,
            "--recording-metadata",
            transcripts,
            "--save",
            "installed-source-index",
        ).stdout
    )
    assert indexed["sources_changed"] == 1, indexed
    assert indexed["verification"]["sources"] == 2 and indexed["verification"]["search_spans"] >= 2
    assert json.loads(run("status", "installed-source-index").stdout)["state"] == "completed"
    context = run(
        "context",
        "--question",
        "What does rollout depend on?",
        "--start-date",
        "2026-01-14",
        "--text",
        cwd=subdirectory,
    ).stdout
    assert "[handoff#L4] Speaker A: Rollout depends on the checklist." in context, context
    assert "Recording started at: 2026-01-14 08:00:00 (timezone unknown)" in context, context
    hook = json.loads(
        submit("KITE：What does rollout depend on?\nUpdate the checklist.", subdirectory)
    )
    extra = hook["hookSpecificOutput"]["additionalContext"]
    assert "[handoff#L4] Speaker A: Rollout depends on the checklist." in extra, extra
    assert "untrusted original quotations" in extra
    assert "Update the checklist" not in extra
    assert not submit("What does rollout depend on?", subdirectory)
    exact = run("context", "--term", "rollout depends", "--matching-lines", cwd=subdirectory).stdout
    exact_pack = json.loads(exact)
    assert [line["segment_id"] for span in exact_pack["spans"] for line in span["evidence"]] == [
        "L4"
    ], exact
    saved_terms = json.loads(
        run(
            "context",
            "--term",
            "rollout depends",
            "--term",
            "no matching words",
            "--matching-lines",
            "--explain",
            "--save",
            "installed-terms",
        ).stdout
    )
    assert saved_terms["saved_run"] == "installed-terms"
    assert [row["quoted_lines"] for row in saved_terms["term_coverage"]] == [1, 0]
    assert saved_terms["packing_trace"]["candidates"][0]["reason"] == "quoted"
    comparison = run(
        "context",
        "--question",
        'What did we decide to "ship a short checklist" for?',
        "--compare-with",
        'What does "rollout depends" refer to?',
        "--text",
        cwd=subdirectory,
    ).stdout
    first, second = comparison.split("Question 2", 1)
    assert "Question 1" in first and "[launch#text-line-3]" in first, comparison
    assert "[handoff#L4]" in second, comparison
    saved = run(
        "context",
        "--question",
        'What does "rollout depends" refer to?',
        "--save",
        "installed-search",
        "--text",
        cwd=subdirectory,
    ).stdout
    assert "Saved search: installed-search" in saved and "[handoff#L4]" in saved, saved
    assert json.loads(run("status", "installed-search").stdout)["state"] == "completed"
    assert (
        json.loads(run("validate-pipeline", "context-search").stdout)["definition"]["id"]
        == "context-search"
    )
    baseline = json.loads(run("source-history", "transcript", "handoff").stdout)["current_checksum"]
    handoff.write_text("00:01\nSpeaker A\n\nRollout depends on QA signoff.\n", encoding="utf-8")
    correction = json.loads(
        run(
            "file-source",
            handoff,
            "--format",
            "transcript-text",
            "--source-date",
            "2026-01-14",
            "--expected-checksum",
            baseline,
        ).stdout
    )
    run("index-sources", "--input", "-", data={"sources": [correction]})
    difference = run("source-compare", baseline, "--text").stdout
    assert "− [handoff#L4] Rollout depends on the checklist." in difference, difference
    assert "+ [handoff#L4] Rollout depends on QA signoff." in difference, difference
    historical = run(
        "context",
        "--question",
        f'What does "rollout" depend on in workspace v{indexed["memory_version"]}?',
        "--text",
    ).stdout
    assert "[handoff#L4] Speaker A: Rollout depends on the checklist." in historical
    assert "QA signoff" not in historical
    viewer = run("view", "--help").stdout
    assert all(option in viewer for option in ("--compare", "--term", "--run")), viewer
    other_project = scratch / "Other project"
    other_project.mkdir()
    assert not submit("KITE: What does rollout depend on?", other_project)
    empty = run("query", "--topic", "onboarding", cwd=other_project, check=False)
    assert empty.returncode == 2 and json.loads(empty.stdout)["error"]["code"] == "not_found"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex", help="Codex CLI or desktop-bundled executable")
    parser.add_argument("--marketplace", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--ref", help="Git branch for a remote marketplace")
    parser.add_argument("--sparse", action="append", help="Sparse Git marketplace path, repeatable")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="kite-codex-smoke-") as directory:
        scratch = Path(directory).resolve()
        project = scratch / "Sample project"
        project.mkdir()
        codex_home = scratch / "codex"
        codex_home.mkdir()
        env = {
            **os.environ,
            "CODEX_HOME": str(codex_home),
            "XDG_CACHE_HOME": str(scratch / "cache"),
            "XDG_DATA_HOME": str(scratch / "data"),
        }
        env.pop("KITE_DATA_DIR", None)
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        launcher = install_plugin(
            args.codex,
            {"source": args.marketplace, "refName": args.ref, "sparsePaths": args.sparse},
            project,
            env,
        )
        print("KITE installed and discoverable in Codex", flush=True)
        verify_workflows(launcher, project, scratch, env)
        print("KITE demo, transcript import, cited query, and viewer entry point passed")


if __name__ == "__main__":
    main()
