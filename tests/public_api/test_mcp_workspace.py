"""Workspace MCP calls must match local symbolic queries and see later commits."""

import json

import pytest

from memoket_kite.mcp.workspace import create_workspace_server
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
    server = create_workspace_server(workspace)
    async with Client(server, raise_exceptions=False) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"recall_conversation_evidence", "read_conversation_citations"}
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
            tool, {"citations": [old_id, "review#decision"], "byte_budget": 128}
        )
        assert not both.is_error
        text = both.content[0].text
        assert f"Revision: {source['expected_checksum']}" in text
        assert "threshold of five." in text and "threshold of three." in text
        assert "[review#reply]" not in text

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
