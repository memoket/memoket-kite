"""Exercise the desktop companion's local CLI against the real workspace."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from memoket_kite.tools.cli import main
from memoket_kite.tools.workspace import Workspace


@pytest.fixture
def twin(tmp_path, capsys):
    workspace = tmp_path / "memory"

    def call(action, value=None, status=0):
        args = ["--workspace", str(workspace), "twin", action]
        if value is not None:
            request = tmp_path / "input.json"
            request.write_text(json.dumps(value), encoding="utf-8")
            args += ["--input", str(request)]
        assert main(args) == status
        return json.loads(capsys.readouterr().out)

    return workspace, call


def memory(path, text, day, *, kind="document"):
    path.write_text(text, encoding="utf-8")
    return {
        "file": str(path),
        "kind": kind,
        "title": path.name,
        "date": day,
        "segments": [{"text": text, "segment_id": "line-1"}],
    }


def test_capture_originals_reminders_and_deletion(twin, tmp_path):
    workspace, call = twin
    first_file = tmp_path / "original.txt"
    first = memory(first_file, "We confirmed that field testing starts on Monday.", "2026-09-01")
    old = call("capture", first)
    duplicate = call("capture", first)
    assert old["version"] == duplicate["version"] == 1 and not duplicate["created"]
    changed_file = tmp_path / "updated.txt"
    changed = memory(changed_file, "We rescheduled field testing to Friday.", "2026-09-02")
    current = call("capture", changed)
    assert (current["version"], current["source_count"]) == (2, 2)
    assert call("status")["items"][0]["original"]

    result = call("reminders", {"subject": "field testing", "since": 1})
    assert result["engine"] == "memoket_kite.core.algebra.execute_plan"
    assert result["trace"] and result["plan"]
    assert [(q["date"], q["text"]) for q in result["reminders"]] == [
        ("2026-09-02", changed["segments"][0]["text"])
    ]
    assert not call("reminders", {"since": 2})["reminders"]
    output = tmp_path / "originals"
    output.mkdir()
    request = {"id": current["source_id"], "directory": str(output)}
    exported = Path(call("original", request)["path"])
    assert exported.read_bytes() == changed_file.read_bytes()

    with Workspace.open(workspace) as store:
        assert all(not row["facts"] for row in store.records())
        plan = store.deletion_plan(
            {"source": "kite-twin", "conversation_ids": [current["source_id"]]}, run_id="twin-test"
        )[0]
        store.delete(plan, plan["approval_token"], run_id="twin-test")
        assert store.db.execute("SELECT COUNT(*) FROM twin_assets").fetchone()[0] == 1
    assert call("original", request, status=2)["error"]["code"] == "not_found"
    assert call("status")["source_count"] == 1


def test_prepared_recording_must_match_original_bytes(twin, tmp_path):
    _, call = twin
    original = tmp_path / "recorded.m4a"
    value = memory(original, "Recorded test fixture.", "2026-11-01", kind="recording")
    value["segments"][0].update(start_ms=40, end_ms=900)
    value["prepared_sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
    original.write_bytes(b"Changed input")
    assert call("capture", value, status=2)["error"]["code"] == "stale_source"
    original.write_text("Recorded test fixture.", encoding="utf-8")
    assert call("capture", value)["version"] == 1
    quote = call("reminders", {"subject": "test fixture"})["reminders"][0]
    assert quote["start_ms"] == 40


def test_memoket_import_preserves_recording_identity_and_updates_transcript(twin, tmp_path):
    workspace, call = twin
    original = tmp_path / "Memoket Transcript.txt"

    def save(text):
        value = memory(original, text, "2026-11-01", kind="memoket")
        value["memoket_id"] = "sample-recording"
        value["segments"][0].update(speaker_id="Test Speaker", start_ms=1000, end_ms=4000)
        return call("capture", value)

    first = save("We discussed the release.")
    assert not save("We discussed the release.")["created"]
    latest_text = "We confirmed the release."
    latest = save(latest_text)
    assert (latest["version"], latest["source_count"]) == (2, 1)
    assert latest["source_id"] == first["source_id"]
    with Workspace.open(workspace) as store:
        conversation = store.source_revision("kite-twin", latest["source_id"], None)["conversation"]
        assert conversation["metadata"]["source_reference"] == "memoket:sample-recording"
        turn = conversation["segments"][0]
        assert (turn["text"], turn["speaker_id"], turn["start_ms"]) == (
            latest_text,
            "Test Speaker",
            1000,
        )


def test_batch_ingestion_verifies_valid_recordings_when_one_annotation_fails(
    twin, tmp_path, monkeypatch, capsys
):
    from memoket_kite.tools import twin_batch
    from memoket_kite.tools.contracts import ToolError

    workspace, _ = twin
    batch = []
    for number in range(3):
        request = memory(tmp_path / f"original-{number}.txt", f"Source {number}", "2026-11-01")
        path = Path(request["file"])
        batch.append(
            {
                "source": {
                    "format": "conversation",
                    "payload": {
                        "source": "kite-twin",
                        "conversation_id": f"recording-{number}",
                        "segments": request["segments"],
                        "metadata": {
                            "source_reference": f"memoket:recording-{number}",
                            "twin_kind": "memoket",
                            "twin_asset": hashlib.sha256(path.read_bytes()).hexdigest() + ".txt",
                        },
                    },
                },
                "options": {
                    "host": "codex",
                    "file": str(path),
                    "self_speakers": [],
                    "note_is_self": False,
                },
            }
        )

    def annotate(*, normalized, options, vocab, page_executor):
        if normalized["conversation"]["conversation_id"] == "recording-1":
            raise ToolError("invalid_annotations", "test response could not be parsed")
        return {
            "annotations": {
                "facts": [],
                "index_spans": [
                    {
                        "segment_ids": ["line-1"],
                        "topics": ["Test topic"],
                        "facets": {"duration": "PT1W"},
                    }
                ],
            },
            "profile": [],
            "host": "codex",
            "model": "test",
        }

    monkeypatch.setattr(twin_batch, "propose_twin_index", annotate)
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"batch": batch}))

    def cli(*args):
        assert main(["--workspace", str(workspace), *args]) == 0
        return json.loads(capsys.readouterr().out)

    run = cli("start", "twin-batch-ingestion", "--input", str(path))["run_id"]
    for stage in ("normalize", "annotate", "commit", "verify"):
        response = cli("stage", run, stage, *(["--allow-provider"] if stage == "annotate" else []))
        if stage == "annotate":
            assert [row["position"] for row in response["value"]["failed"]] == [1]
    assert response["checkpoint"]["state"] == "completed"
    assert response["value"]["sources"] == 2
    assert response["value"]["memory_version"] == 1
    with Workspace.inspect(workspace) as store:
        records = store.records()
        assert {row["conversation"]["conversation_id"] for row in records} == {
            "recording-0",
            "recording-2",
        }
        assert {row["index_spans"][0]["facets"]["duration"] for row in records} == {"P1W"}
    with Workspace.open(workspace) as store:
        deletion = store.deletion_plan(
            {"source": "kite-twin", "conversation_id": "recording-0"}, run_id="batch-test-deletion"
        )[0]
        store.delete(deletion, deletion["approval_token"], run_id="batch-test-deletion")
        assert (
            store.db.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (run,)).fetchone()[0]
            == 0
        )
        assert store.db.execute("SELECT inputs FROM runs WHERE id=?", (run,)).fetchone()[0] == "{}"


def test_redacted_memoket_recording_keeps_originals_without_guessing_topics(
    twin, tmp_path, monkeypatch
):
    from memoket_kite.tools import twin_ingestion

    workspace, call = twin
    request = memory(tmp_path / "redacted.txt", "[ADDRESS]。", "2026-11-10", kind="memoket")
    request.update(host="codex", allow_provider=True, memoket_id="redacted-recording")

    def unexpected(*args, **kwargs):
        pytest.fail("No provider is needed for a redacted placeholder")

    monkeypatch.setattr(twin_ingestion, "_request_host", unexpected)
    result = call("ingest", request)
    assert result["integrity"]["source_lines"] == 1
    with Workspace.inspect(workspace) as store:
        (record,) = store.records()
        assert record["conversation"]["segments"][0]["text"] == "[ADDRESS]。"
        assert not record.get("index_spans")
        assert result["profile"] == []


def test_bad_tool_entity_mention_does_not_discard_valid_mentions():
    from memoket_kite.tools.twin_ingestion import _filter_entity_mentions

    valid = {"entity": "Cedar", "segment_id": "T1", "text": "Cedar"}
    candidate = {
        "spans": [
            {
                "topics": ["Launch"],
                "entity_mentions": [None, {"entity": "bad"}, {**valid, "segment_id": "T2"}, valid],
            }
        ]
    }
    _filter_entity_mentions(candidate, ["T1"])
    assert candidate["spans"] == [{"topics": ["Launch"], "entity_mentions": [valid]}]


@pytest.mark.parametrize("turn_texts", [("음~~",), ("啊。", "你好。", "喂。")])
def test_brief_memoket_recording_without_topics_keeps_original_turns(
    twin, tmp_path, monkeypatch, turn_texts
):
    from memoket_kite.tools import twin_ingestion

    workspace, call = twin
    request = memory(
        tmp_path / "utterance.txt", "\n".join(turn_texts), "2026-11-10", kind="memoket"
    )
    request.update(host="codex", allow_provider=True, memoket_id="brief-utterance")
    request["segments"] = [
        {"segment_id": f"line-{number}", "text": line} for number, line in enumerate(turn_texts, 1)
    ]
    monkeypatch.setattr(
        twin_ingestion,
        "_request_host",
        lambda *args, **kwargs: {
            "annotations": {"index_spans": [], "facts": [], "taxonomy": []},
            "profile": [],
        },
    )
    result = call("ingest", request)
    assert result["integrity"]["source_lines"] == len(turn_texts)
    with Workspace.inspect(workspace) as store:
        (record,) = store.records()
        assert tuple(line["text"] for line in record["conversation"]["segments"]) == turn_texts
        assert not record.get("index_spans")


def test_skills_are_project_local_and_require_explicit_save(twin, tmp_path):
    _, call = twin
    project = tmp_path / "project"
    project.mkdir()
    value = {
        "project": str(project),
        "host": "codex",
        "name": "release-followup",
        "description": "Apply a confirmed release change and run the release check.",
        "steps": "## Workflow\nCheck the current decision, edit the config, and run the release check.",
    }
    assert not call("skills", value)["skills"]
    saved = Path(call("teach", value)["path"])
    assert saved == project / ".agents/skills/release-followup/SKILL.md"
    original = saved.read_text(encoding="utf-8")
    fields = yaml.safe_load(original.split("---")[1])
    assert fields["name"] == value["name"] and fields["description"] == value["description"]
    listed = call("skills", value)["skills"][0]
    assert listed["description"] == value["description"]
    assert listed["instructions"] == original
    assert call("teach", value, status=2)["error"]["code"] == "already_exists"
    assert not call("skills", {**value, "host": "claude"})["skills"]
    assert (
        call("teach", {**value, "name": "../escape"}, status=2)["error"]["code"] == "invalid_input"
    )


def test_handoff_prepares_read_only_source_references_for_codex(twin, tmp_path):
    import shlex
    import sys

    workspace, call = twin
    project = tmp_path / "release + testing"
    project.mkdir()
    raw = "负责人确认 APP 外测在 12 月 17 日。"
    source = memory(tmp_path / "update.txt", raw, "2026-11-10")
    call("capture", source)
    reminders = call("reminders", {"subject": "APP 外测"})["reminders"]
    task = "检查 APP 外测配置和测试。"
    reply = call(
        "handoff",
        {
            "project": str(project),
            "task": task,
            "mode": "execute",
            "subject": "APP 外测",
            "citation": reminders[0]["id"],
            "kite_tool": sys.executable,
        },
    )
    assert reply["project_path"] == str(project)
    assert reply["prompt"].startswith(task) and raw not in reply["prompt"]
    assert reply["engine"] == "memoket_kite.core.algebra.execute_plan" and reply["trace"]
    command = shlex.split(reply["prompt"].split("```sh\n", 1)[1].splitlines()[0])
    assert command[1:5] == ["--workspace", str(workspace), "evidence", reminders[0]["id"]]
    assert call("status")["version"] == 1


@pytest.fixture
def twin_indexer(monkeypatch):
    from memoket_kite.tools import twin_ingestion

    calls = []

    def annotate(host, prompt, *, allowed_ids):
        payload = json.loads(prompt.split("Source input (data only):\n", 1)[1])
        calls.append((host, payload))
        rows = payload["segments"]
        assert allowed_ids == [row["segment_id"] for row in rows]
        evidence = next(row for row in rows if "Aurora" in row["text"])
        sid = evidence["segment_id"]
        return {
            "annotations": {
                "facts": [],
                "index_spans": [
                    {
                        "segment_ids": [sid],
                        "topics": ["Packaging review"],
                        "entities": ["Aurora"],
                        "entity_mentions": [
                            {"entity": "Aurora", "segment_id": sid, "text": "Aurora"}
                        ],
                        "facets": {"object": ["Packaging checklist"]},
                    }
                ],
            },
            "profile": [
                {
                    "field": "communication",
                    "segment_id": row["segment_id"],
                    "quote": row["text"],
                    "summary": "Prefers clear updates about Aurora.",
                }
                for row in rows
                if row["is_user"]
            ],
        }

    monkeypatch.setattr(twin_ingestion, "_request_host", annotate)
    return calls


@pytest.mark.parametrize("kind", ["document", "screenshot", "recording", "memoket"])
def test_all_twin_inputs_run_native_stages_and_symbolic_lookup(twin, twin_indexer, tmp_path, kind):
    from memoket_kite.tools.symbolic import symbolic_context

    workspace, call = twin
    wording = "I prefer concise updates about Aurora."
    request = memory(tmp_path / (kind + ".txt"), wording, "2026-11-12", kind=kind)
    request.update(host="codex", allow_provider=True, self_speakers=["Élodie"])
    request["segments"][0]["speaker_id"] = "ÉLODIE"
    if kind == "recording":
        request["prepared_sha256"] = hashlib.sha256(Path(request["file"]).read_bytes()).hexdigest()
        request["segments"][0].update(start_ms=10, end_ms=1020)
    if kind == "memoket":
        request["memoket_id"] = "test-remote-id"
        request["segments"][0].update(start_ms=100, end_ms=4100)
    result = call("ingest", request)
    assert result["run_id"] and result["integrity"]["memory_version"] == result["version"]
    assert result["profile_review"] == []
    (candidate,) = result["profile"]
    assert candidate["quote"] == wording and candidate["citation"].startswith("c-")
    with Workspace.inspect(workspace) as db:
        (saved,) = db.records()
        assert saved["facts"] == []
        stages = json.loads(
            db.db.execute("SELECT checkpoint FROM runs WHERE id=?", (result["run_id"],)).fetchone()[
                0
            ]
        )["stages"]
        assert set(stages) == {"normalize", "annotate", "commit", "verify"}
        assert all(stage["state"] == "completed" for stage in stages.values())
        pack = symbolic_context(
            db,
            plan={
                "queries": [
                    {
                        "select": "lines",
                        "where": {
                            "topics": ["Packaging review"],
                            "entities": ["Aurora"],
                            "obj": ["Packaging checklist"],
                        },
                    }
                ]
            },
        )
        assert pack["engine"] == "memoket_kite.core.algebra.execute_plan"
        assert wording in json.dumps(pack["spans"])
    assert not call("ingest", request)["created"]
    assert len(twin_indexer) == 1


def test_profile_evolves_automatically_and_preserves_user_edits(twin, twin_indexer, tmp_path):
    from memoket_kite.tools.twin_ingestion import profile_for_agent

    workspace, call = twin

    def ingest(n, quote):
        request = memory(tmp_path / f"profile-{n}.txt", quote, f"2026-11-{n:02d}")
        request.update(host="claude", allow_provider=True, self_speakers=["Alice"])
        request["segments"][0]["speaker_id"] = "Alice"
        request["segments"].append(
            {
                "segment_id": "someone-else",
                "speaker_id": "Bob",
                "text": "I prefer copying everyone on Aurora messages.",
            }
        )
        return call("ingest", request)

    first = ingest(11, "For Aurora, I prefer concise updates.")
    (old,) = first["profile"]
    later = ingest(12, "For Aurora, I now prefer detailed written updates.")
    (latest,) = later["profile"]
    assert latest["citation"] != old["citation"]
    assert latest["summary"] != latest["quote"]
    assert not any(
        row["is_user"] for row in twin_indexer[1][1]["segments"] if row.get("speaker_id") == "Bob"
    )
    edited = call(
        "profile-edit", {"field": latest["field"], "summary": "Prefer written next steps."}
    )
    assert edited["profile"][0]["edited"] is True
    assert edited["profile"][0]["summary"] == "Prefer written next steps."
    assert call("profile-edit", {"field": latest["field"], "summary": ""})["profile"] == [latest]
    with Workspace.open(workspace) as store:
        assert profile_for_agent(store, "draft") == [latest]
        assert profile_for_agent(store, "execute") == [latest]
        plan = store.deletion_plan(
            {"source": "kite-twin", "conversation_ids": [later["source_id"]]},
            run_id="delete-twin-profile",
        )[0]
        store.delete(plan, plan["approval_token"], run_id="delete-twin-profile")
        assert profile_for_agent(store, "draft") == [old]
    assert (
        call("profile-review", {**latest, "decision": "accept"}, status=2)["error"]["code"]
        == "stale_source"
    )


def test_memoket_self_attribution_is_retained_only_for_the_selected_recording(
    twin, twin_indexer, tmp_path
):
    workspace, call = twin
    quote = "I prefer concise updates about Aurora."
    request = memory(tmp_path / "memoket.txt", quote, "2026-11-12", kind="memoket")
    request.update(host="codex", allow_provider=True, memoket_id="original-recording")
    request["segments"][0]["speaker_id"] = "A"
    request["segments"].append(
        {"segment_id": "other", "speaker_id": "B", "text": "I like lengthy reports."}
    )
    initial = call("ingest", request)
    assert initial["profile"] == []

    attributed = call("ingest", {**request, "memoket_self_speaker": "A", "self_speakers": ["B"]})
    assert attributed["source_id"] == initial["source_id"]
    assert attributed["source_count"] == 1
    assert attributed["profile"][0]["quote"] == quote
    assert [row["is_user"] for row in twin_indexer[-1][1]["segments"]] == [True, False]

    repeated = call("ingest", request)
    assert not repeated["created"]
    assert repeated["profile"] == attributed["profile"]
    replayed = call("ingest", {**request, "reindex": True})
    assert replayed["profile"] == attributed["profile"]
    assert [row["is_user"] for row in twin_indexer[-1][1]["segments"]] == [True, False]

    other = call("ingest", {**request, "memoket_id": "different-recording"})
    assert other["source_count"] == 2
    assert not any(row["is_user"] for row in twin_indexer[-1][1]["segments"])
    with Workspace.inspect(workspace) as store:
        original = store.source_revision("kite-twin", attributed["source_id"], None)
        assert original["conversation"]["metadata"]["twin_self_speaker"] == "A"
        second = store.source_revision("kite-twin", other["source_id"], None)
        assert "twin_self_speaker" not in second["conversation"]["metadata"]


def test_twin_ingest_commits_english_display_with_original_citation(
    twin, twin_indexer, tmp_path, monkeypatch
):
    from memoket_kite.tools import twin_ingestion, viewer

    workspace, call = twin
    original = "我偏好在变更 Aurora 发布前先核对验收标准。"
    english = "I prefer checking the acceptance criteria before changing the Aurora release."
    request = memory(tmp_path / "发布.txt", original, "2026-11-18")
    request.update(host="codex", allow_provider=True, translate_english=True)
    attempts = []

    def translate(_host, prompt, _shape):
        attempts.append(prompt)
        rows, _ = json.JSONDecoder().raw_decode(prompt[prompt.index("\n[") + 1 :])
        return {
            "entries": [
                {
                    "id": item["id"],
                    "english": {
                        "发布.txt": "Release.txt",
                        original: english if len(attempts) > 1 else original,
                    }.get(item["text"], item["text"]),
                }
                for item in rows
            ]
        }

    monkeypatch.setattr(twin_ingestion, "_request_json", translate)
    result = call("ingest", request)
    assert result["source_count"] == 1 and result["integrity"]["source_lines"] == 1
    assert len(attempts) == 2
    snapshot = viewer._snapshot(workspace, [])
    (line_id,) = snapshot["evidence"]
    assert snapshot["evidence"][line_id]["text"] == original
    assert snapshot["display_languages"]["en"][f"line:{line_id}"] == english
    (revision,) = snapshot["revisions"]
    assert snapshot["display_languages"]["en"][f"source:{revision['checksum']}"] == "Release.txt"


def test_twin_ingest_requires_consent_and_rejects_uncited_profile(twin, tmp_path, monkeypatch):
    from memoket_kite.tools import twin_ingestion

    _, call = twin
    request = memory(tmp_path / "unattributed.txt", "Aurora learned a lesson.", "2026-11-13")
    request.update(host="codex")
    assert call("ingest", request, status=2)["error"]["code"] == "provider_disabled"
    monkeypatch.setattr(
        twin_ingestion,
        "_request_host",
        lambda *_, **_kwargs: {
            "annotations": {
                "facts": [],
                "index_spans": [
                    {"segment_ids": ["line-1"], "topics": ["Aurora"]},
                ],
            },
            "profile": [
                {
                    "segment_id": "line-1",
                    "field": "work_habits",
                    "quote": "Aurora learned a lesson.",
                }
            ],
        },
    )
    request["allow_provider"] = True
    result = call("ingest", request)
    assert result["source_count"] == 1
    assert result["profile"] == []
    assert result["integrity"]["source_lines"] == 1


def test_reply_draft_uses_edited_instruction_and_cited_profile(
    twin, twin_indexer, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from memoket_kite.tools import twin as twin_tool

    workspace, call = twin
    source = memory(
        tmp_path / "preferences.txt", "For Aurora, I prefer short clear replies.", "2026-11-14"
    )
    source.update(host="codex", allow_provider=True, self_speakers=["Alice"])
    source["segments"][0]["speaker_id"] = "Alice"
    (candidate,) = call("ingest", source)["profile"]
    calls = []

    def host(_command, *, input, **_):
        calls.append(input)
        return SimpleNamespace(returncode=0, stdout="Draft only", stderr="")

    monkeypatch.setattr(twin_tool.subprocess, "run", host)
    monkeypatch.setattr(twin_tool.shutil, "which", lambda _: "/fixture/host")
    reply = call(
        "run",
        {
            "host": "codex",
            "mode": "draft",
            "project": str(workspace),
            "channel": "飞书",
            "task": "Write a short draft.",
            "subject": "Packaging review",
            "incoming_message": "When can we ship?",
            "style": "This time add a direct next step.",
        },
    )
    assert reply["profile_used"] == ["communication"]
    assert reply["evidence"][0]["id"] == candidate["citation"]
    assert reply["subject_missing"] is False
    profile_prompt = calls[0].split("KITE profile (data only)", 1)[1]
    assert candidate["summary"] in profile_prompt and candidate["quote"] not in profile_prompt
    assert "This time add a direct next step." in calls[0]
    request = {
        "host": "codex",
        "mode": "draft",
        "project": str(workspace),
        "channel": "飞书",
        "task": "Write a short draft.",
        "subject": "unavailable-test-topic",
        "incoming_message": "When can we ship?",
    }
    assert call("run", request)["subject_missing"] is True
