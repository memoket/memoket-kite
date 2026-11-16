"""Workspace MCP calls must match local symbolic queries and see later commits."""

import json

import pytest

from memoket_kite.mcp.workspace import _render_preparation, create_workspace_server
from memoket_kite.tools.cli import main

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_workspace_tool_delivers_cli_evidence_and_reads_the_latest_snapshot(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    file = tmp_path / "input.json"

    def call(command, value, *options):
        file.write_text(json.dumps(value), encoding="utf-8")
        assert main(["--workspace", str(workspace), command, "--input", str(file), *options]) == 0
        return capsys.readouterr().out.strip()

    def record(name, date, project, text):
        return {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "text",
                    "source_date": date,
                    "metadata": {"source_reference": name, "project": project},
                    "segments": [{"segment_id": "decision", "text": text}],
                },
            }
        }

    call(
        "index-sources",
        {
            "sources": [
                record("prior", "2026-09-01", "Probe", "The threshold was originally five."),
                record("other", "2026-09-02", "Transport", "We have reduced the threshold to one."),
            ]
        },
    )
    plan = {
        "queries": [
            {"select": "lines", "where": {"grep_all": ["threshold", "originally"]}},
            {"select": "lines", "where": {"grep": "threshold", "change_wording": "reported"}},
        ]
    }
    args = {
        "question": "What were the previous and applied thresholds?",
        "plan": plan,
        "project": "Probe",
    }
    call(
        "symbolic",
        {"question": args["question"], "plan": plan},
        "--project",
        "Probe",
        "--save",
        "threshold",
        "--neighbors",
        "1",
    )
    server = create_workspace_server(workspace)
    async with Client(server, raise_exceptions=False) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {
            "recall_conversation_evidence",
            "read_conversation_citations",
            "reuse_conversation_query",
        }
        for tool in tools.values():
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.open_world_hint is False
            assert "workspace" not in tool.input_schema["properties"]
        recall = "recall_conversation_evidence"
        first = await client.call_tool(recall, args)
        assert not first.is_error and len(first.content) == 1
        assert "prior#decision" in first.content[0].text
        assert "other#decision" not in first.content[0].text
        assert "Query 2 matched no original turns." in first.content[0].text
        reuse = "reuse_conversation_query"
        saved_first = await client.call_tool(reuse, {"run_id": "threshold"})
        assert not saved_first.is_error and "Workspace v1" in saved_first.content[0].text
        assert "Saved plan threshold (compiled at v1)" in saved_first.content[0].text
        assert "[revised#decision]" not in saved_first.content[0].text
        combined = await client.call_tool(
            recall,
            {
                **args,
                "known_citations": ["prior#decision", "other#decision", "other#absent"],
                "workspace_version": 1,
            },
        )
        assert not combined.is_error
        text = combined.content[0].text
        assert "Supplied citation 3:" in text and "Supplied citations and nearby turns" in text
        assert text.count("[prior#decision]") == 1
        assert "[other#decision]" in text and "Additional symbolic matches" not in text
        assert "Query 2 matched no original turns." in text

        call(
            "index-sources",
            {
                "sources": [
                    record(
                        "revised", "2026-09-03", "Probe", "We have reduced the threshold to three."
                    )
                ]
            },
        )
        second = await client.call_tool(recall, args)
        assert not second.is_error and second.structured_content is None
        assert "revised#decision" in second.content[0].text
        saved_second = await client.call_tool(reuse, {"run_id": "threshold"})
        text = saved_second.content[0].text
        assert not saved_second.is_error
        assert "Since v1: 1 changed recordings, 1 cited" in text
        assert "[prior#decision]" in text and "[revised#decision]" in text
        assert "[other#decision]" not in text
        assert (
            main(["--workspace", str(workspace), "symbolic", "--reuse", "threshold", "--text"]) == 0
        )
        assert text.strip() == capsys.readouterr().out.strip()
        stale = await client.call_tool(recall, {**args, "workspace_version": 1})
        assert stale.is_error and "stale_snapshot:" in stale.content[0].text
        combined = await client.call_tool(
            recall, {**args, "known_citations": ["prior#decision"], "workspace_version": 2}
        )
        assert not combined.is_error
        text = combined.content[0].text
        assert text.count("[prior#decision]") == 1
        assert "Additional symbolic matches\nRecording date: 2026-09-03" in text
        assert "[revised#decision]" in text
        assert second.content[0].text.strip() == call(
            "symbolic",
            {"question": args["question"], "plan": plan, "retry_date_subject": True},
            "--text",
            "--project",
            "Probe",
            "--limit",
            "6",
            "--neighbors",
            "1",
            "--max-bytes",
            "3600",
        )
        inline = await client.call_tool(
            recall, {**args, "plan": {**plan, "retry_date_subject": True}}
        )
        assert inline.content == second.content
        invalid = await client.call_tool(
            recall, {**args, "start_date": "2026-09-04", "end_date": "2026-09-01"}
        )
        assert invalid.is_error and "invalid_input:" in invalid.content[0].text


async def test_citations_preserve_revision_and_deduplicate_context(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    file = tmp_path / "input.json"

    def cli(*options, payload=None):
        args = ["--workspace", str(workspace), *options]
        if payload is not None:
            file.write_text(json.dumps(payload), encoding="utf-8")
            args.extend(("--input", str(file)))
        assert main(args) == 0
        return capsys.readouterr().out.strip()

    source = {
        "format": "conversation",
        "payload": {
            "conversation_id": "review",
            "source": "text",
            "source_date": "2026-09-01",
            "metadata": {"source_reference": "review"},
            "segments": [
                {"segment_id": "subject", "text": "Let's review the shipping threshold."},
                {"segment_id": "decision", "text": "Use a threshold of five."},
                {"segment_id": "reply", "text": "Confirmed."},
            ],
        },
    }
    cli("index-sources", payload={"sources": [{"source": source}]})
    cli(
        "symbolic",
        "--save",
        "review-query",
        payload={"queries": [{"select": "lines", "where": {"grep": "Use a threshold"}}]},
    )
    original = json.loads(cli("evidence", "review#decision"))["value"]
    old_id = original["evidence"]["source_line_id"]
    version = original["memory_version"]
    tool = "read_conversation_citations"
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        lookup = {
            "citations": ["[text/review#decision]", "review#reply", "review#missing"],
            "workspace_version": version,
        }
        before = await client.call_tool(tool, lookup)
        assert not before.is_error and before.structured_content is None
        text = before.content[0].text
        assert text.count("[review#reply]") == 1 and "Citation 3:" in text
        assert text == cli(
            "evidence",
            "[text/review#decision]",
            "--also",
            "review#reply",
            "--also",
            "review#missing",
            "--context",
            "2",
            "--byte-budget",
            "4096",
            "--text",
        )
        source["expected_checksum"] = original["evidence"]["revision_checksum"]
        source["payload"]["segments"][1]["text"] = "Use a threshold of three."
        cli("index-sources", payload={"sources": [{"source": source}]})
        stale = await client.call_tool(tool, lookup)
        assert stale.is_error and "stale_snapshot:" in stale.content[0].text
        both = await client.call_tool(
            tool, {"citations": [f"[{old_id}]", "review#decision"], "byte_budget": 128}
        )
        assert not both.is_error
        text = both.content[0].text
        assert f"Revision: {source['expected_checksum']}" in text
        assert "threshold of five." in text and "threshold of three." in text
        assert "[review#reply]" not in text
        request = {
            "question": "What is the shipping threshold?",
            "plan": {"queries": [{"select": "lines", "where": {"grep": "Use a threshold"}}]},
            "known_citations": [old_id],
        }
        combined = await client.call_tool("recall_conversation_evidence", request)
        assert not combined.is_error
        text = combined.content[0].text
        assert f"Revision: {source['expected_checksum']}" in text
        assert "[review#decision] Use a threshold of five." in text
        assert "[review#decision] Use a threshold of three." in text
        reuse = "reuse_conversation_query"
        reused = await client.call_tool(reuse, {"run_id": "review-query"})
        assert not reused.is_error
        assert "threshold of three." in reused.content[0].text
        assert "threshold of five." not in reused.content[0].text
        comparison = {"run_id": "review-query", "include_previous": True}
        compared = await client.call_tool(reuse, comparison)
        assert not compared.is_error
        text = compared.content[0].text
        assert "[review#decision] Use a threshold of three." in text
        assert "Earlier source revisions at workspace v1" in text
        assert f"[{old_id}] Use a threshold of five." in text
        assert text == cli("symbolic", "--reuse", "review-query", "--include-previous", "--text")
        later = await client.call_tool(reuse, {**comparison, "since_version": 2})
        assert not later.is_error and "threshold of five." not in later.content[0].text

        cli(
            "start",
            "memory-deletion",
            "--run-id",
            "test-delete",
            payload={"target": {"source": "text", "conversation_id": "review"}},
        )
        preview = json.loads(cli("stage", "test-delete", "preview"))["value"]
        cli("stage", "test-delete", "apply", "--approve", preview["approval_token"])
        gone = await client.call_tool(tool, {"citations": [old_id, "review#decision"]})
        assert gone.is_error and "not_found:" in gone.content[0].text
        assert "threshold of five." not in gone.content[0].text
        combined = await client.call_tool("recall_conversation_evidence", request)
        assert not combined.is_error
        assert "Supplied citations unavailable:" in combined.content[0].text
        assert "threshold of five." not in combined.content[0].text
        discarded = await client.call_tool(reuse, comparison)
        assert discarded.is_error and "invalidated_run:" in discarded.content[0].text
        assert "threshold of five." not in discarded.content[0].text


async def test_workspace_lookup_does_not_create_a_missing_store(tmp_path):
    missing = tmp_path / "missing"
    async with Client(create_workspace_server(missing), raise_exceptions=False) as client:
        result = await client.call_tool(
            "recall_conversation_evidence",
            {"question": "Find decisions.", "plan": {"queries": []}},
        )
    assert result.is_error
    assert "not_found:" in result.content[0].text
    assert not missing.exists()


async def test_symbolic_recall_cites_a_decision_and_its_nearby_indexed_subject(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "input.json"

    def cli(*args, value):
        payload.write_text(json.dumps(value), encoding="utf-8")
        assert main(["--workspace", str(workspace), *args, "--input", str(payload)]) == 0
        return json.loads(capsys.readouterr().out)

    subject = (
        "Now, the part between the reservoir and the relief leg on the press is next on the agenda."
        " Please leave the other control component presentations until after lunch."
    )
    decision = (
        "We approved four minutes for it. Keep the other outstanding changes in the review list."
    )
    tags = {
        "topics": ["Safeguard checks"],
        "entities": ["Inlet regulator"],
        "facets": {"object": ["Control component"]},
    }

    def recording(name, turns, labels):
        return {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "text",
                    "metadata": {"source_reference": name},
                    "segments": [{"segment_id": k, "text": v} for k, v in turns.items()],
                },
            },
            "annotations": {"index_spans": labels, "facts": []},
        }

    cli(
        "index-sources",
        value={
            "sources": [
                recording(
                    "review",
                    {"subject": subject, "decision": decision},
                    [{"segment_ids": ["subject"], **tags}],
                ),
                recording(
                    "outside",
                    {"decision": "We approved seven minutes for it."},
                    [],
                ),
                recording(
                    "different-spans",
                    {"subject": subject, "decision": "We approved nine minutes for it."},
                    [
                        {"segment_ids": ["subject"], "topics": tags["topics"]},
                        {
                            "segment_ids": ["subject"],
                            "entities": tags["entities"],
                            "facets": tags["facets"],
                        },
                    ],
                ),
                recording(
                    "too-far",
                    {
                        "subject": subject,
                        "pause": "Let's take a break.",
                        "decision": "We approved ten minutes for it.",
                    },
                    [{"segment_ids": ["subject"], **tags}],
                ),
            ]
        },
    )
    where = {
        "grep": "approved.+minutes",
        "near": {
            "turns": 1,
            "topics": ["safeguard_checks"],
            "entities": ["inlet_regulator"],
            "obj": ["control_component"],
        },
    }
    question = "What was the approved time for the inlet regulator safeguard?"
    request = {"question": question, "plan": {"queries": [{"select": "lines", "where": where}]}}
    receipt = cli("symbolic", value=request)
    assert [span["evidence"][0]["segment_id"] for span in receipt["spans"]] == [
        "subject",
        "decision",
    ]
    assert {
        step["atom"]: step["matched_labels"]
        for step in receipt["trace"]
        if step.get("via") == "near"
    } == {
        "topics": ["safeguard_checks"],
        "entities": ["inlet_regulator"],
        "obj": ["control_component"],
    }
    assert next(step for step in receipt["trace"] if step["stage"] == "near")["hits"] == 1
    trimmed = cli("symbolic", "--delivery-bytes", "256", value=request)
    assert "[review#decision]" not in trimmed

    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"
        result = await client.call_tool(tool, request)
        assert not result.is_error
        text = result.content[0].text
        assert f"[review#subject] {subject}" in text
        assert f"[review#decision] {decision}" in text
        assert (
            "[different-spans#" not in text and "[outside#" not in text and "[too-far#" not in text
        )
        limited = await client.call_tool(tool, {**request, "max_bytes": 128, "neighbors": 0})
        assert not limited.is_error and "[review#decision]" not in limited.content[0].text
        where["near"]["grep"] = "unmentioned wording"
        unsupported = await client.call_tool(tool, request)
        assert not unsupported.is_error and "[review#decision]" not in unsupported.content[0].text
        del where["near"]["grep"]
        revised = recording(
            "review",
            {"subject": "Next item on the schedule.", "decision": decision},
            [],
        )
        revised["source"]["expected_checksum"] = receipt["spans"][0]["evidence"][0][
            "revision_checksum"
        ]
        cli(
            "index-sources",
            value={"sources": [revised]},
        )
        after = await client.call_tool(tool, request)
        assert not after.is_error and "[review#decision]" not in after.content[0].text
        old_version = ("symbolic", "--at-version", str(receipt["memory_version"]))
        assert not cli(*old_version, value=request)["spans"]
        original_only = {
            "queries": [
                {
                    "select": "lines",
                    "where": {
                        "units": ["review"],
                        "grep": where["grep"],
                        "near": {"grep": "reservoir and the relief leg", "turns": 1},
                    },
                }
            ]
        }
        retained = cli(*old_version, value=original_only)
        assert [span["evidence"][0]["text"] for span in retained["spans"]] == [subject, decision]


async def test_workspace_preparation_binds_index_hints_to_the_submitted_plan(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    script = tmp_path / "input.json"
    question = 'Use the release agreement from the project that discussed the "folding hinge".'

    def cli(*args, value=None):
        if value is not None:
            script.write_text(json.dumps(value), encoding="utf-8")
            args = (*args, "--input", str(script))
        assert main(["--workspace", str(workspace), *args]) == 0
        return json.loads(capsys.readouterr().out)

    def note(identity, project, day, wording, span=None):
        return {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": identity,
                    "source": "text",
                    "source_date": day,
                    "metadata": {"source_reference": identity, "project": project},
                    "segments": [{"segment_id": "line", "text": wording}],
                },
            },
            **(
                {"annotations": {"facts": [], "index_spans": [{"segment_ids": ["line"], **span}]}}
                if span
                else {}
            ),
        }

    launch = {"topics": ["rollout"], "entities": ["Mina"], "facets": {"event": ["Approval"]}}
    cli(
        "index-sources",
        value={
            "sources": [
                note("anchor", "Heron", "2026-04-06", "Let's try the folding hinge."),
                note(
                    "decision", "Heron", "2026-04-07", "Mina agreed we can ship next week.", launch
                ),
                note("old", "Falcon", "2026-04-05", "Do not ship the folding hinge design."),
                note(
                    "outside",
                    "Falcon",
                    "2026-04-06",
                    "Ravi confirmed the local depot.",
                    {"topics": ["depot"]},
                ),
            ]
        },
    )
    args = {"question": question, "start_date": "2026-04-06"}
    plan = {
        "queries": [{"select": "lines", "where": {"topics": ["rollout"], "event": ["approval"]}}]
    }
    tool = "recall_conversation_evidence"
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        prepared = await client.call_tool(tool, args)
        assert not prepared.is_error
        hints = json.loads(prepared.content[0].text)
        assert hints["kind"] == "symbolic_preparation" and hints["active_recordings"] == 3
        assert hints["index"] == {
            "quoted_phrases": [
                {"grep": r"folding\s+hinge", "matching_recordings": 1, "project_recordings": 2}
            ],
            "topics": ["depot (1)", "rollout (1)"],
            "entities": ["mina (1)"],
            "event": ["approval (1)"],
        }
        raw = cli("symbolic", "--prepare", question, "--brief", "--start-date", args["start_date"])
        assert hints["snapshot"] == raw["snapshot"]
        exact = {
            **args,
            "question": "Implement the release agreement.",
            "anchor_quote": "folding hinge",
        }
        scoped = await client.call_tool(tool, exact)
        assert not scoped.is_error
        exact_hints = json.loads(scoped.content[0].text)
        assert exact_hints["index"]["topics"] == ["rollout (1)"]
        assert exact_hints["index"]["quoted_phrases"][0]["anchor_project"] == "Heron"
        anchored = await client.call_tool(
            tool, {**exact, "snapshot": exact_hints["snapshot"], "plan": plan}
        )
        assert not anchored.is_error and "[decision#line]" in anchored.content[0].text
        unscoped = await client.call_tool(
            tool, {k: v for k, v in exact.items() if k != "start_date"}
        )
        assert not unscoped.is_error
        mixed = json.loads(unscoped.content[0].text)["index"]
        assert "depot (1)" in mixed["topics"] and "anchor_project" not in mixed["quoted_phrases"][0]
        request = {
            **args,
            "snapshot": hints["snapshot"],
            "plan": plan,
            "anchor_quote": "folding hinge",
        }
        before = await client.call_tool(tool, request)
        assert not before.is_error and "[decision#line] Mina agreed" in before.content[0].text
        wrong_scope = await client.call_tool(tool, {**request, "start_date": "2026-04-05"})
        assert wrong_scope.is_error and "stale_snapshot:" in wrong_scope.content[0].text
        cli(
            "index-sources",
            value={"sources": [note("change", "Falcon", "2026-04-08", "The latch was confirmed.")]},
        )
        stale = await client.call_tool(tool, request)
        assert stale.is_error and "stale_snapshot:" in stale.content[0].text


def test_workspace_preparation_keeps_whole_hints_within_the_reply_limit():
    long_label = "中文标签" * 220
    prepared = {
        "snapshot": "0" * 64,
        "memory_version": 5,
        "active_recordings": 30,
        "index": {
            "topics": f"{long_label} (1), short (2)",
            "entities": f"{long_label} (1)",
            "facets": {"event": ["handoff (3)"]},
            "recordings": [
                {"recording": f"recording-{i}", "date": "2026-04-06"} for i in range(30)
            ],
        },
    }
    response = _render_preparation(prepared)
    value = json.loads(response)
    assert len(response.encode()) <= 3600 and value["omitted_hints"]
    assert value["snapshot"] == prepared["snapshot"]
    assert value["index"]["topics"] == [f"{long_label} (1)", "short (2)"]
    assert value["index"]["event"] == ["handoff (3)"]
    assert "entities" not in value["index"]


async def test_previous_turns_share_the_budget_and_keep_revision_specific_positions(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    path = tmp_path / "input.json"

    def call(*options, payload=None):
        if payload is not None:
            path.write_text(json.dumps(payload), encoding="utf-8")
            options = (*options, "--input", str(path))
        assert main(["--workspace", str(workspace), *options]) == 0
        result = json.loads(capsys.readouterr().out)
        return result.get("value", result)

    long_original = "Approved batch size: five. " + "补" * 72
    conversation = {
        "conversation_id": "review",
        "source": "text",
        "source_date": "2026-09-01",
        "metadata": {"source_reference": "review", "project": "Probe"},
        "segments": [
            {"segment_id": "intro", "text": "A note on this session."},
            {"segment_id": "decision", "text": long_original},
        ],
    }

    def source(**options):
        return {
            "sources": [{"source": {"format": "conversation", "payload": conversation, **options}}]
        }

    call("index-sources", payload=source())
    call(
        "symbolic",
        "--save",
        "review",
        "--project",
        "Probe",
        "--start-date",
        "2026-09-01",
        payload={"queries": [{"select": "lines", "where": {"grep": "Approved batch size"}}]},
    )
    original = call("evidence", "review#decision")["evidence"]
    conversation["segments"].pop(0)
    conversation["segments"][0]["text"] = "Approved batch size: three."
    call("index-sources", payload=source(expected_checksum=original["revision_checksum"]))
    reuse = "reuse_conversation_query"
    args = {"run_id": "review", "include_previous": True}
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        narrow = await client.call_tool(reuse, {**args, "max_bytes": 128})
        assert not narrow.is_error
        text = narrow.content[0].text
        assert "Approved batch size: three." in text
        assert long_original not in text
        assert f"omitted 1 earlier turns. Evidence IDs: {original['source_line_id']}" in text
        expanded = await client.call_tool(reuse, {**args, "max_bytes": 512})
        assert not expanded.is_error
        text = expanded.content[0].text
        assert f"[{original['source_line_id']}] {long_original}" in text
        raw = call("symbolic", "--reuse", "review", "--include-previous", "--max-bytes", "512")
        pair = raw["previous_revision_evidence"]["pairs"][0]
        assert pair["current_source_line_id"].endswith("L1")
        assert pair["previous"]["source_line_id"].endswith("L2")
