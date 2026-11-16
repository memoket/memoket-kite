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
        tools = (await client.list_tools()).tools
        assert [tool.name for tool in tools] == ["recall_conversation_evidence"]
        assert tools[0].annotations.read_only_hint is True
        assert tools[0].annotations.open_world_hint is False
        assert "workspace" not in tools[0].input_schema["properties"]
        first = await client.call_tool(tools[0].name, args)
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
        second = await client.call_tool(tools[0].name, args)
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
            tools[0].name, {**args, "plan": {**plan, "retry_date_subject": True}}
        )
        assert inline.content == second.content
        invalid = await client.call_tool(
            tools[0].name, {**args, "start_date": "2026-09-04", "end_date": "2026-09-01"}
        )
        assert invalid.is_error and "invalid_input:" in invalid.content[0].text


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
