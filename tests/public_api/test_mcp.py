"""Offline MCP contracts, including real XML persistence and temporal retrieval."""

import builtins
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from memoket_kite import (
    Answer,
    ConfigurationError,
    Fact,
    KiteError,
    Memory,
    ProviderError,
    QueryError,
    StorageError,
)
from memoket_kite.mcp.server import create_server, main

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "examples" / "data" / "mcp_memory.xml"
QUESTION = "Where does the user live now?"
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def memory_path(tmp_path):
    path = tmp_path / "memory.xml"
    path.write_bytes(SEED.read_bytes())
    return path


@pytest.fixture
def fact():
    return Fact(
        id="move-2F1",
        content="The user moved from Tokyo to Berlin.",
        topics=("places",),
        event_time="2026-10-20",
        source_time="2026-10-20",
        sources=(
            {
                "id": "move-2L1",
                "date": "2026-10-20",
                "role": "user",
                "content": "I moved from Tokyo to Berlin.",
            },
        ),
    )


@pytest.fixture
def configured(monkeypatch, memory_path, fact):
    memory = Mock(spec=Memory)
    memory.remember.return_value = [fact]
    memory.recall.return_value = [fact]
    memory.answer_with_evidence.return_value = Answer(
        question=QUESTION,
        text="Berlin",
        evidence=(fact,),
        # Raw-line/instance IDs need not have a matching Fact in evidence.
        citations=("move-2L1", "instance-1"),
    )
    load = Mock(return_value=memory)
    monkeypatch.setattr(Memory, "load", load)
    server = create_server(memory_path)
    load.assert_called_once_with(memory_path.resolve())
    return server, memory


async def test_registered_tools_and_schemas(configured):
    server, _ = configured
    async with Client(server, raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"kite_remember", "kite_recall", "kite_answer"}
    assert tools["kite_remember"].input_schema["required"] == ["messages"]
    assert tools["kite_remember"].input_schema["properties"]["messages"]["minItems"] == 1
    for name, field in (("kite_recall", "query"), ("kite_answer", "question")):
        schema = tools[name].input_schema
        assert schema["required"] == [field]
        assert schema["properties"]["limit"]["default"] == 10
        assert schema["properties"]["limit"]["minimum"] == 1
        assert tools[name].annotations.read_only_hint is True
    assert tools["kite_remember"].annotations.read_only_hint is False


async def test_remember_forwards_public_arguments_and_serializes(configured, fact):
    server, memory = configured
    arguments = {
        "messages": [{"role": "user", "content": "I moved from Tokyo to Berlin."}],
        "session_id": "move-2",
        "date": "2026-10-20",
        "title": "A move",
    }
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("kite_remember", arguments)
    assert not result.is_error
    memory.remember.assert_called_once_with(
        arguments["messages"], session_id="move-2", date="2026-10-20", title="A move"
    )
    data = result.structured_content
    assert data["status"] == "remembered"
    assert data["session_id"] == "move-2"
    assert data["facts"][0]["content"] == fact.content
    assert data["facts"][0]["sources"] == [dict(fact.sources[0])]
    assert data["facts"][0]["topics"] == ["places"]
    assert "metadata" not in data["facts"][0]
    assert json.loads(result.content[0].text) == data


async def test_remember_optional_values_use_memory_defaults(configured):
    server, memory = configured
    memory.remember.return_value = []
    messages = [{"role": "alice", "content": "Hello"}]
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("kite_remember", {"messages": messages})
    memory.remember.assert_called_once_with(messages, session_id=None, date=None, title=None)
    assert result.structured_content == {"status": "remembered", "session_id": None, "facts": []}


async def test_recall_preserves_public_fact_and_sources(configured, fact):
    server, memory = configured
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("kite_recall", {"query": QUESTION, "limit": 3})
    memory.recall.assert_called_once_with(QUESTION, limit=3)
    assert not result.is_error
    assert result.structured_content == {
        "query": QUESTION,
        "facts": [
            {
                "id": fact.id,
                "content": fact.content,
                "kind": None,
                "subject": None,
                "topics": ["places"],
                "entities": [],
                "event_time": "2026-10-20",
                "source_time": "2026-10-20",
                "confidence": None,
                "sources": [dict(fact.sources[0])],
            }
        ],
    }


async def test_empty_recall_is_an_empty_list(configured):
    server, memory = configured
    memory.recall.return_value = []
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("kite_recall", {"query": "Unknown detail?"})
    memory.recall.assert_called_once_with("Unknown detail?", limit=10)
    assert not result.is_error
    assert result.structured_content == {"query": "Unknown detail?", "facts": []}


async def test_answer_preserves_text_citations_and_evidence(configured, fact):
    server, memory = configured
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("kite_answer", {"question": QUESTION, "limit": 2})
    memory.answer_with_evidence.assert_called_once_with(QUESTION, limit=2)
    assert not result.is_error
    data = result.structured_content
    assert data["answer"] == "Berlin"
    assert data["citations"] == ["move-2L1", "instance-1"]
    assert data["evidence"][0]["id"] == fact.id
    assert data["evidence"][0]["sources"] == [dict(fact.sources[0])]


async def test_missing_source_dates_stay_null_and_empty_answers_are_unchanged(configured):
    server, memory = configured
    memory.recall.return_value = [
        Fact(id="f1", content="Undated", sources=({"id": "l1", "date": None},))
    ]
    memory.answer_with_evidence.return_value = Answer(question=QUESTION, text="No information")
    async with Client(server, raise_exceptions=True) as client:
        recalled = await client.call_tool("kite_recall", {"query": QUESTION})
        answered = await client.call_tool("kite_answer", {"question": QUESTION})
    assert recalled.structured_content["facts"][0]["sources"] == [{"id": "l1", "date": None}]
    assert answered.structured_content == {
        "answer": "No information",
        "citations": [],
        "evidence": [],
    }
    memory.answer_with_evidence.assert_called_once_with(QUESTION, limit=10)


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"messages": []},
        {"messages": "bad"},
        {"messages": ["bad"]},
        {"messages": [{"role": "user"}]},
        {"messages": [{"role": "user", "content": ""}]},
        {"messages": [{"role": " ", "content": "hello"}]},
        {"messages": [{"role": "user", "content": 123}]},
        {"messages": [{"role": False, "content": "hello"}]},
        {"messages": [{"role": "user", "content": "hello", "extra": "bad"}]},
        *[
            {"messages": [{"role": "user", "content": "hello"}], key: value}
            for key, value in (
                ("session_id", ""),
                ("session_id", "has spaces"),
                ("session_id", 12),
                ("date", "tomorrow"),
                ("date", 20261020),
                ("title", ["bad"]),
            )
        ],
    ],
)
async def test_invalid_remember_arguments_never_reach_memory(configured, arguments):
    server, memory = configured
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool("kite_remember", arguments)
    assert result.is_error
    assert result.content[0].text
    memory.remember.assert_not_called()


@pytest.mark.parametrize("tool,field", [("kite_recall", "query"), ("kite_answer", "question")])
@pytest.mark.parametrize("value", ["", " \n", None, 123])
async def test_invalid_questions(configured, tool, field, value):
    server, memory = configured
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(tool, {field: value})
    assert result.is_error
    memory.recall.assert_not_called()
    memory.answer_with_evidence.assert_not_called()


@pytest.mark.parametrize("tool,field", [("kite_recall", "query"), ("kite_answer", "question")])
@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10", None])
async def test_invalid_limits(configured, tool, field, limit):
    server, memory = configured
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(tool, {field: QUESTION, "limit": limit})
    assert result.is_error
    memory.recall.assert_not_called()
    memory.answer_with_evidence.assert_not_called()


@pytest.mark.parametrize(
    "error", [KiteError, ConfigurationError, StorageError, ProviderError, QueryError]
)
@pytest.mark.parametrize(
    "tool,method,arguments",
    [
        ("kite_remember", "remember", {"messages": [{"role": "user", "content": "hello"}]}),
        ("kite_recall", "recall", {"query": QUESTION}),
        ("kite_answer", "answer_with_evidence", {"question": QUESTION}),
    ],
)
async def test_public_errors_become_mcp_tool_errors(configured, error, tool, method, arguments):
    server, memory = configured
    getattr(memory, method).side_effect = error("original failure detail")
    async with Client(server, raise_exceptions=True) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error
    assert f"{error.__name__}: original failure detail" in result.content[0].text


@pytest.mark.parametrize("kind", ["missing", "directory", "malformed", "unreadable", "parent"])
def test_invalid_memory_configuration(memory_path, monkeypatch, kind):
    if kind == "missing":
        memory_path.unlink()
    elif kind == "directory":
        memory_path = memory_path.parent
    elif kind == "malformed":
        memory_path.write_text("<broken", encoding="utf-8")
    else:
        real_access = os.access
        refused = memory_path if kind == "unreadable" else memory_path.parent
        monkeypatch.setattr(
            "memoket_kite.mcp.server.os.access",
            lambda path, mode: path != refused and real_access(path, mode),
        )
    with pytest.raises(ConfigurationError, match="memory"):
        create_server(memory_path)


def test_cli_configuration_errors_go_only_to_stderr(tmp_path, capsys):
    with pytest.raises(SystemExit) as info:
        main(["--memory", str(tmp_path / "missing.xml")])
    assert info.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "could not configure memory" in output.err


def test_cli_runs_only_stdio(memory_path, monkeypatch):
    server = Mock()
    factory = Mock(return_value=server)
    monkeypatch.setattr("memoket_kite.mcp.server.create_server", factory)
    main(["--memory", str(memory_path)])
    factory.assert_called_once_with(memory_path)
    server.run.assert_called_once_with(transport="stdio")


def test_missing_optional_sdk_has_an_actionable_error(memory_path, monkeypatch):
    real_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.startswith("mcp"):
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_sdk)
    with pytest.raises(ConfigurationError, match=r"pip install.*memoket-kite\[mcp\]"):
        create_server(memory_path)


def test_module_import_without_sdk_has_no_side_effects():
    script = """
import builtins, contextlib, io, os, socket
real_import = builtins.__import__
def without_sdk(name, *args, **kwargs):
    if name.split('.')[0] in ('mcp', 'pydantic'):
        raise AssertionError('optional SDK imported at module import')
    return real_import(name, *args, **kwargs)
builtins.__import__ = without_sdk
def no_network(*args, **kwargs):
    raise AssertionError('network at module import')
socket.socket.connect = no_network
before = dict(os.environ)
output = io.StringIO()
with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
    import memoket_kite.mcp.server
assert not output.getvalue(), output.getvalue()
assert dict(os.environ) == before
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=10)


async def test_stdio_client_connects_and_lists_tools_without_provider(memory_path, monkeypatch):
    # This subprocess never performs an LLM operation; the actual pipe transport
    # and CLI must initialize successfully with no provider configuration.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "memoket_kite.mcp.server", "--memory", str(memory_path)],
    )
    async with Client(params, mode="legacy") as client:
        tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == {"kite_remember", "kite_recall", "kite_answer"}


async def test_temporal_update_persists_and_newer_evidence_wins(memory_path, monkeypatch):
    """Only model responses are scripted; KITE extracts, saves, sorts and packs.

    A one-fact limit makes a missing/reversed temporal sort observable: the
    reader asserts that it actually saw the selected source, before replying.
    """
    from memoket_kite.pipeline import answer as reader
    from memoket_kite.pipeline import compile_plan as compiler
    from memoket_kite.pipeline import extract as extraction

    monkeypatch.setattr("memoket_kite.memory.ensure_configured", lambda model: None)
    responses = iter(
        [
            ("move-1", "2026-07-26", "The user moved to Tokyo.", "Tokyo"),
            ("move-2", "2026-10-20", "The user moved from Tokyo to Berlin.", "Berlin"),
        ]
    )

    def scripted_extract(prompt, **kwargs):
        session, date, content, place = next(responses)
        assert f"{session}L1" in prompt
        return {
            "facts": [
                {
                    "content": content,
                    "kind": "event",
                    "who": "user",
                    "t": date,
                    "topics": ["places"],
                    "src": [f"{session}L1"],
                    "facets": {"event": ["move"], "place": [place]},
                }
            ],
            "proposals": [],
        }

    monkeypatch.setattr(extraction, "llm_json", scripted_extract)
    monkeypatch.setattr(
        compiler,
        "llm_json",
        lambda *args, **kwargs: {
            "queries": [
                {
                    "select": "facts",
                    "where": {"topics": [{"code": "places"}], "who": ["user"], "event": ["move"]},
                    "pipe": [{"op": "sort", "key": "t", "desc": True}, {"op": "head", "n": 1}],
                }
            ],
            "intent": "factual",
        },
    )
    reader_calls = []

    def scripted_reader(prompt, **kwargs):
        # Read the evidence section, not a fixed response detached from retrieval.
        evidence = prompt.split("EVIDENCE:\n", 1)[1]
        assert "most recent wins" in prompt
        if "move-2L1" in evidence:
            assert "2026-10-20" in evidence
            assert "move-1L1" not in evidence
            reader_calls.append("Berlin")
            return {"answer": "Berlin", "evidence": ["move-2L1"]}
        assert "move-1L1" in evidence and "2026-07-26" in evidence
        reader_calls.append("Tokyo")
        return {"answer": "Tokyo", "evidence": ["move-1L1"]}

    monkeypatch.setattr(reader, "llm_json", scripted_reader)
    server = create_server(memory_path)
    async with Client(server, raise_exceptions=True) as client:
        for session, date, message, city in (
            ("move-1", "2026-07-26", "I moved to Tokyo.", "Tokyo"),
            ("move-2", "2026-10-20", "I moved from Tokyo to Berlin.", "Berlin"),
        ):
            remembered = await client.call_tool(
                "kite_remember",
                {
                    "messages": [{"role": "user", "content": message}],
                    "session_id": session,
                    "date": date,
                },
            )
            assert not remembered.is_error, remembered.content
            recalled = await client.call_tool("kite_recall", {"query": QUESTION, "limit": 1})
            answered = await client.call_tool("kite_answer", {"question": QUESTION, "limit": 1})
            assert not recalled.is_error, recalled.content
            assert not answered.is_error, answered.content
            data = answered.structured_content
            assert data["answer"] == city
            assert data["citations"] == [f"{session}L1"]
            for facts in (recalled.structured_content["facts"], data["evidence"]):
                assert len(facts) == 1
                assert facts[0]["id"] == f"{session}F1"
                assert facts[0]["sources"] == [
                    {
                        "id": f"{session}L1",
                        "date": date,
                        "role": "user",
                        "content": message,
                    }
                ]
            print(json.dumps({"after": session, **data}, sort_keys=True))

        duplicate = await client.call_tool(
            "kite_remember",
            {
                "messages": [{"role": "user", "content": "Duplicate"}],
                "session_id": "move-2",
            },
        )
        assert duplicate.is_error and "already exists" in duplicate.content[0].text
        invalid_date = await client.call_tool(
            "kite_remember",
            {
                "messages": [{"role": "user", "content": "Bad date"}],
                "date": "2026-02-31",
            },
        )
        assert invalid_date.is_error and "valid ISO date" in invalid_date.content[0].text

    assert reader_calls == ["Tokyo", "Berlin"]
    xml = memory_path.read_text(encoding="utf-8")
    assert "move-1F1" in xml and "move-2F1" in xml
    # A fresh server sees the same persisted artifact, with no second ingestion.
    async with Client(create_server(memory_path), raise_exceptions=True) as client:
        result = await client.call_tool("kite_recall", {"query": QUESTION, "limit": 1})
    assert result.structured_content["facts"][0]["sources"][0]["id"] == "move-2L1"
