"""Workspace MCP calls must match local symbolic queries and see later commits."""

import json
import re
import sqlite3
from contextlib import closing

import pytest

from memoket_kite.mcp.workspace import _render_preparation, create_workspace_server
from memoket_kite.tools.cli import main
from memoket_kite.tools.symbolic import render_symbolic_context
from memoket_kite.tools.workspace import Workspace

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_workspace_tool_delivers_cli_evidence_and_reads_the_latest_snapshot(tmp_path, capsys):
    workspace = tmp_path / "workspace"

    def quotes(text):
        return [line for line in text.splitlines() if line.startswith("[")]

    def evidence_text(result):
        return result.content[0].text.rsplit("\nEvidence receipt ", 1)[0]

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
        submitted = {
            "queries": [
                plan["queries"][0],
                {
                    "select": "lines",
                    "where": {"grep": "threshold"},
                    "change_wording": "reported",
                },
            ]
        }
        normalized = await client.call_tool(recall, {**args, "plan": submitted})
        assert not normalized.is_error
        assert evidence_text(normalized) == evidence_text(second)
        submitted["queries"][1]["where"]["change_wording"] = "reported"
        duplicate = await client.call_tool(recall, {**args, "plan": submitted})
        assert duplicate.is_error and "invalid_plan:" in duplicate.content[0].text
        saved_second = await client.call_tool(reuse, {"run_id": "threshold"})
        text = saved_second.content[0].text
        assert not saved_second.is_error
        assert "Since v1: 1 changed recordings, 1 cited" in text
        assert "[prior#decision]" in text and "[revised#decision]" in text
        assert "[other#decision]" not in text
        assert (
            main(["--workspace", str(workspace), "symbolic", "--reuse", "threshold", "--text"]) == 0
        )
        assert quotes(text) == quotes(capsys.readouterr().out)
        assert 'First quote matched "threshold", "originally"' in text
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
        cli_text = call(
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
        assert quotes(second.content[0].text) == quotes(cli_text)
        inline = await client.call_tool(
            recall, {**args, "plan": {**plan, "retry_date_subject": True}}
        )
        assert evidence_text(inline) == evidence_text(second)
        invalid = await client.call_tool(
            recall, {**args, "start_date": "2026-09-04", "end_date": "2026-09-01"}
        )
        assert invalid.is_error and "invalid_input:" in invalid.content[0].text


async def test_warm_reader_detects_same_version_corruption_and_file_replacement(
    tmp_path, capsys, monkeypatch
):
    path = tmp_path / "workspace"
    replacement = tmp_path / "replacement"
    payload = tmp_path / "input.json"
    for directory, value in [(path, "five"), (replacement, "three")]:
        payload.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "source": {
                                "format": "conversation",
                                "payload": {
                                    "conversation_id": "meeting",
                                    "source": "text",
                                    "metadata": {"source_reference": "meeting"},
                                    "segments": [
                                        {
                                            "segment_id": "decision",
                                            "text": f"Use a threshold of {value}.",
                                        }
                                    ],
                                },
                            }
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert main(["--workspace", str(directory), "index-sources", "--input", str(payload)]) == 0
    capsys.readouterr()

    loads = []
    load = Workspace._load_index

    def counted(workspace, *, verify):
        loads.append(workspace.version())
        return load(workspace, verify=verify)

    monkeypatch.setattr(Workspace, "_load_index", counted)
    args = {
        "question": "What is the threshold?",
        "plan": {"queries": [{"select": "lines", "where": {"grep": "threshold"}}]},
    }
    call = "recall_conversation_evidence"
    database = path / "kite.sqlite3"
    async with Client(create_workspace_server(path), raise_exceptions=False) as client:
        for _ in range(3):
            reply = await client.call_tool(call, args)
            assert not reply.is_error and "threshold of five" in reply.content[0].text
        assert loads == [1]
        with closing(sqlite3.connect(database, timeout=1)) as db, db:
            db.execute("UPDATE memory SET xml=?", (b"<invalid />",))
        corrupted = await client.call_tool(call, args)
        assert corrupted.is_error and "integrity_error:" in corrupted.content[0].text
        assert loads == [1, 1]
        (replacement / "kite.sqlite3").replace(database)
        updated = await client.call_tool(call, args)
        assert not updated.is_error and "threshold of three" in updated.content[0].text
        assert "threshold of five" not in updated.content[0].text
        assert loads == [1, 1, 1]


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
        assert (
            "Requested turns: review#decision, review#reply · Other turns are nearby context."
            in text
        )
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
        assert text.rsplit("\nEvidence receipt ", 1)[0] == cli(
            "symbolic", "--reuse", "review-query", "--include-previous", "--text"
        )
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


async def test_named_project_in_content_only_workspace_uses_grounded_entity_mentions(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    input_file = tmp_path / "input.json"

    def add(*records):
        input_file.write_text(json.dumps({"sources": records}), encoding="utf-8")
        assert (
            main(["--workspace", str(workspace), "index-sources", "--input", str(input_file)]) == 0
        )
        capsys.readouterr()

    def record(name, text, day, *, project=None, grounded=False):
        source = {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "transcript",
                    "metadata": {
                        "source_reference": name,
                        **({"project": project} if project else {}),
                    },
                    "source_date": day,
                    "segments": [{"segment_id": "turn", "text": text}],
                },
            }
        }
        if not project:
            span = {"segment_ids": ["turn"], "entities": ["atlas"]}
            if grounded:
                span["entity_mentions"] = [
                    {"entity": "Atlas", "segment_id": "turn", "text": "Atlas"}
                ]
            source["annotations"] = {"facts": [], "index_spans": [span]}
        return source

    add(
        record("first", "Atlas approved nine units.", "2025-03-01", grounded=True),
        record("second", "Atlas approved five units.", "2025-03-02", grounded=True),
        record("uncited", "A different team approved twelve units.", "2025-03-02"),
    )
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"
        args = {"question": "What was approved for Atlas?", "project": "Atlas"}
        plan = {"queries": [{"select": "lines", "where": {"grep": "approved"}}]}
        prepared = await client.call_tool(tool, args)
        assert not prepared.is_error
        info = json.loads(prepared.content[0].text)
        assert info["active_recordings"] == 2 and info["mentioned_project_entity"] == "atlas"
        result = await client.call_tool(tool, {**args, "plan": plan, "snapshot": info["snapshot"]})
        assert not result.is_error
        text = result.content[0].text
        assert "Indexed mentions of atlas scope this search to 2 recordings." in text
        assert "[first#turn] Atlas approved nine units." in text
        assert "[second#turn] Atlas approved five units." in text
        assert "[uncited#turn]" not in text
        dated = await client.call_tool(tool, {**args, "plan": plan, "start_date": "2025-03-02"})
        assert not dated.is_error and "[first#turn]" not in dated.content[0].text
        empty = await client.call_tool(tool, {**args, "plan": plan, "start_date": "2025-03-03"})
        assert not empty.is_error and "0 recordings" in empty.content[0].text

        add(record("official", "The rulebook was approved.", "2025-03-03", project="Other"))
        rejected = await client.call_tool(tool, {**args, "plan": plan})
        assert (
            rejected.is_error
            and "no active recordings in the selected project" in rejected.content[0].text
        )
        recorded = await client.call_tool(tool, {**args, "project": "Other", "plan": plan})
        assert not recorded.is_error and "[official#turn]" in recorded.content[0].text


async def test_followup_receipt_omits_seen_turns_and_returns_revised_evidence(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "input.json"

    def cli(*args, value):
        payload.write_text(json.dumps(value), encoding="utf-8")
        assert main(["--workspace", str(workspace), *args, "--input", str(payload)]) == 0
        return json.loads(capsys.readouterr().out)

    def source(name, text):
        return {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "text",
                    "metadata": {"source_reference": name},
                    "segments": [{"segment_id": "decision", "text": text}],
                },
            }
        }

    quota = "The quota is seven units for each member."
    window = "The pickup window ends at eight."
    original = source("review", quota)
    cli("index-sources", value={"sources": [original, source("session", window)]})
    request = {
        "question": "Find the quota.",
        "plan": {"queries": [{"where": {"grep": "quota"}, "select": "lines"}]},
    }
    cli("symbolic", "--save", "quota", value=request)
    run = cli("symbolic", value=request["plan"])
    checksum = run["spans"][0]["evidence"][0]["revision_checksum"]
    receipt_line = re.compile(r"Evidence receipt ([a-f0-9]{24})\.")

    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"

        async def recall(value, **options):
            answer = await client.call_tool(tool, {**value, **options})
            assert not answer.is_error
            return answer.content[0].text

        saved = await client.call_tool(
            "reuse_conversation_query", {"run_id": "quota", "include_receipt": True}
        )
        assert not saved.is_error
        first = saved.content[0].text
        assert "Saved plan quota" in first
        first_receipt = receipt_line.search(first)[1]
        assert f"[review#decision] {quota}" in first
        repeat = await recall(request, after_receipt=first_receipt)
        assert "No new original turns in the selected results." in repeat
        assert "Earlier direct matches still in this search: [review#decision]." in repeat
        assert "No evidence matched" not in repeat and quota not in repeat
        assert receipt_line.search(repeat)[1] == first_receipt
        independent = await recall(request)
        assert quota in independent and receipt_line.search(independent)[1] != first_receipt

        plan = {
            "question": "Find the quota and pickup window.",
            "plan": {"queries": [{"select": "lines", "where": {"grep": "quota|pickup"}}]},
        }
        more = await recall(plan, after_receipt=first_receipt)
        assert "omitted 1 original turn" in more
        assert f"[session#decision] {window}" in more and quota not in more
        second_receipt = receipt_line.search(more)[1]
        assert second_receipt != first_receipt
        both_seen = await recall(plan, after_receipt=second_receipt)
        assert "omitted 2 original turns" in both_seen
        assert quota not in both_seen and window not in both_seen

        explicit = await recall(
            plan, after_receipt=second_receipt, known_citations=["review#decision"]
        )
        assert f"[review#decision] {quota}" in explicit
        revised_text = "The quota is now five units for each member."
        revised = source("review", revised_text)
        revised["source"]["expected_checksum"] = checksum
        cli("index-sources", value={"sources": [revised]})
        change = await recall(plan, after_receipt=second_receipt)
        assert "Workspace v2" in change and f"[review#decision] {revised_text}" in change
        assert "Earlier direct matches still in this search: [session#decision]." in change
        assert quota not in change and window not in change

        stale = await client.call_tool(tool, {**request, "after_receipt": "0" * 24})
        assert stale.is_error and "evidence receipt expired" in stale.content[0].text
        long_turn = "Spotcheck. " + "Check the archive record. " * 145
        cli("index-sources", value={"sources": [source("archive", long_turn)]})
        bulk = await recall(
            {
                "question": "Find the spotcheck.",
                "plan": {
                    "queries": [{"select": "lines", "where": {"grep": "spotcheck"}}],
                },
            },
            max_bytes=4800,
        )
        assert "[archive#decision]" in bulk and receipt_line.search(bulk)
        cli(
            "symbolic",
            "--save",
            "spotcheck",
            "--max-bytes",
            "4800",
            value={"queries": [{"select": "lines", "where": {"grep": "spotcheck"}}]},
        )
        saved_bulk = await client.call_tool("reuse_conversation_query", {"run_id": "spotcheck"})
        assert not saved_bulk.is_error and receipt_line.search(saved_bulk.content[0].text)


async def test_followup_keeps_a_direct_citation_handle_and_omits_incidental_ones(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    source = {
        "format": "conversation",
        "payload": {
            "conversation_id": "design",
            "source": "test",
            "metadata": {"source_reference": "design"},
            "segments": [
                {"segment_id": "approved", "text": "The rollout limit is 30."},
                {"segment_id": "discussion", "text": "The prototype weight is 30 grams."},
                {"segment_id": "fixture", "text": "The fixture weighs 30 grams."},
            ],
        },
    }
    data = tmp_path / "input.json"
    data.write_text(json.dumps({"sources": [{"source": source}]}), encoding="utf-8")
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(data)]) == 0
    capsys.readouterr()

    def request(pattern, **options):
        return {
            "question": "Find the rollout limit.",
            "plan": {
                "queries": [{"select": "lines", "where": {"grep": pattern}, "prefer": ["rollout"]}]
            },
            "limit": 1,
            **options,
        }

    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        recall = "recall_conversation_evidence"
        first = await client.call_tool(recall, request("rollout", include_receipt=True))
        assert not first.is_error
        initial = first.content[0].text
        token = re.search(r"Evidence receipt ([a-f0-9]{24})\.", initial)[1]
        assert "[design#discussion] The prototype weight" in initial
        more = await client.call_tool(recall, request("30", after_receipt=token, neighbors=1))
        assert not more.is_error
        text = more.content[0].text
        assert "Earlier direct matches still in this search: [design#approved]." in text
        assert 'Required wording in earlier searches · [design#approved] matched "rollout"' in text
        assert "[design#fixture] The fixture weighs 30 grams." in text
        assert "The rollout limit is 30." not in text
        assert "[design#discussion]" not in text
        outside = await client.call_tool(recall, request("grams", after_receipt=token, neighbors=1))
        assert not outside.is_error
        assert "[design#approved]" not in outside.content[0].text


async def test_planless_receipt_expands_original_context_once(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    input_path = tmp_path / "input.json"
    texts = [
        "Agenda.",
        "The rollout decision is approved.",
        "Mina will send the notice.",
        "The notice covers two regions.",
        "Meeting adjourned.",
    ]
    source = {
        "format": "conversation",
        "payload": {
            "source": "text",
            "conversation_id": "review",
            "metadata": {"source_reference": "review"},
            "segments": [{"segment_id": str(i), "text": text} for i, text in enumerate(texts)],
        },
    }
    other = {
        "format": "conversation",
        "payload": {
            "source": "text",
            "conversation_id": "other",
            "metadata": {"source_reference": "other"},
            "segments": [{"segment_id": "0", "text": "Separate planning meeting."}],
        },
    }
    input_path.write_text(
        json.dumps({"sources": [{"source": item} for item in (source, other)]}), encoding="utf-8"
    )
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(input_path)]) == 0
    capsys.readouterr()
    receipt = re.compile(r"Evidence receipt ([a-f0-9]{24})\.")
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"
        query = {
            "question": "What was approved?",
            "plan": {"queries": [{"select": "lines", "where": {"grep": "approved"}}]},
        }
        first = await client.call_tool(tool, query)
        text = first.content[0].text
        assert not first.is_error and "[review#1]" in text and "[review#3]" not in text
        token = receipt.search(text)[1]
        payload = {"after_receipt": token, "neighbors": 2}
        expanded = await client.call_tool(tool, payload)
        text = expanded.content[0].text
        assert not expanded.is_error and "Original citation windows" in text
        assert "Nearby original turns only · Requested turns already delivered." in text
        assert "[review#3] The notice covers two regions." in text
        assert "[review#1]" not in text and "[review#4]" not in text
        payload["after_receipt"] = receipt.search(text)[1]
        repeat = await client.call_tool(tool, payload)
        assert not repeat.is_error and "No new original turns" in repeat.content[0].text
        direct = await client.call_tool(
            tool, {"known_citations": ["review#3"], "neighbors": 0, "project": "Any"}
        )
        text = direct.content[0].text
        assert not direct.is_error and "[review#3] The notice covers two regions." in text
        from_direct = await client.call_tool(
            tool, {"after_receipt": receipt.search(text)[1], "neighbors": 1}
        )
        assert (
            not from_direct.is_error
            and "[review#4] Meeting adjourned." in from_direct.content[0].text
        )
        assert "[review#3]" not in from_direct.content[0].text
        wide = await client.call_tool(tool, {"known_citations": ["review#1"], "neighbors": 8})
        assert not wide.is_error and "[review#4] Meeting adjourned." in wide.content[0].text
        text = wide.content[0].text
        assert "Requested turns: review#1 · Other turns are nearby context." in text
        assert re.findall(r"^\[(review#\d+)\]", text, re.M) == [f"review#{i}" for i in range(5)]
        one_new = await client.call_tool(
            tool,
            {
                "after_receipt": receipt.search(text)[1],
                "known_citations": ["review#1", "other#0"],
                "neighbors": 0,
            },
        )
        assert (
            not one_new.is_error
            and "[other#0] Separate planning meeting." in one_new.content[0].text
        )
        assert "[review#1]" not in one_new.content[0].text
        wide_search = await client.call_tool(tool, {**query, "neighbors": 8})
        assert wide_search.is_error and "0–4 neighbors" in wide_search.content[0].text


async def test_followup_recovers_an_already_quoted_nonleading_citation(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    originals = {
        "review-a": "The access review used a square violet marker.",
        "review-b": "The approved trial temperature is 24 degrees.",
        "review-c": "The trial limit is 16 units.",
    }
    sources = [
        {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "text",
                    "metadata": {"source_reference": name, "project": "Cobalt"},
                    "segments": [{"segment_id": "turn", "text": text}],
                },
            }
        }
        for name, text in originals.items()
    ]
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(path)]) == 0
    capsys.readouterr()

    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"
        initial = await client.call_tool(
            tool,
            {
                "question": "Find the policy for the project with the square violet marker.",
                "anchor_quote": "square violet marker",
                "plan": {
                    "queries": [
                        {
                            "select": "lines",
                            "where": {"grep": "marker|trial"},
                            "prefer": ["marker|approved"],
                        }
                    ]
                },
            },
        )
        text = initial.content[0].text
        assert not initial.is_error and "Complete indexed recording scope for Cobalt" in text
        assert "[review-c#turn] The trial limit is 16 units." in text
        token = re.search(r"Evidence receipt ([a-f0-9]{24})\.", text)[1]
        plan = {"queries": [{"select": "lines", "where": {"grep": "limit"}}]}
        next_result = await client.call_tool(tool, {"after_receipt": token, "plan": plan})
        assert not next_result.is_error, next_result.content
        text = next_result.content[0].text
        assert "Earlier direct matches still in this search: [review-c#turn]." in text
        assert "3 already quoted with the receipt" in text
        assert "No new original turns" in text
        assert "16 units" not in text and "[review-a#turn]" not in text
        missing = await client.call_tool(tool, {"plan": plan})
        assert missing.is_error and "submit a question" in missing.content[0].text

        # Coverage must be checked against current indexed turns, including ones
        # the follow-up plan and its older receipt do not match or contain.
        extra = {
            "format": "conversation",
            "payload": {
                "source": "text",
                "conversation_id": "review-d",
                "metadata": {"source_reference": "review-d", "project": "Cobalt"},
                "segments": [{"segment_id": "turn", "text": "A new maintenance note."}],
            },
        }
        path.write_text(json.dumps({"sources": [{"source": extra}]}), encoding="utf-8")
        assert main(["--workspace", str(workspace), "index-sources", "--input", str(path)]) == 0
        capsys.readouterr()
        changed = await client.call_tool(tool, {"after_receipt": token, "plan": plan})
        assert not changed.is_error
        assert "Workspace v2" in changed.content[0].text
        assert "Complete indexed recording scope" not in changed.content[0].text


@pytest.mark.parametrize(
    ("term", "label", "quote"),
    [
        ("rollout", "rollout_cap", "The rollout cap is fixed."),
        ("上线", "上线限额", "上线限额已确定。"),
    ],
)
async def test_citation_followup_filters_unrelated_values_in_the_same_recording(
    tmp_path, capsys, term, label, quote
):
    workspace = tmp_path / "workspace"
    distractions = [
        "Floor lamp test.",
        "Tripod adjustments.",
        "Recheck the scale.",
        "It weighs 30 grams.",
    ]
    sources = []
    for name, turns in (
        ("decision", [quote, "The approved value is 30.", *distractions]),
        ("prototype", ["The prototype weighs 30 grams."]),
    ):
        record = {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "test",
                    "metadata": {"source_reference": name},
                    "segments": [
                        {"segment_id": f"line-{n}", "text": text} for n, text in enumerate(turns, 1)
                    ],
                },
            }
        }
        if name == "decision":
            record["annotations"] = {
                "facts": [],
                "index_spans": [{"segment_ids": ["line-1"], "facets": {"object": [label]}}],
            }
        sources.append(record)
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"sources": sources}, ensure_ascii=False), encoding="utf-8")
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(path)]) == 0
    capsys.readouterr()
    base = {"question": f"Find the approved {term} value.", "neighbors": 0}
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"
        first = await client.call_tool(
            tool,
            {
                **base,
                "plan": {"queries": [{"select": "lines", "where": {"grep": term}}]},
            },
        )
        assert not first.is_error, first.content
        text = first.content[0].text
        token = re.search(r"Evidence receipt ([a-f0-9]{24})\.", text)[1]
        hint = next(line for line in text.splitlines() if line.startswith("Follow-up near for"))
        near = json.loads(hint.split(" · ", 1)[1])
        assert near == {"obj": [label], "turns": 2, "include_anchor": True}
        assert "follow_up {citation, grep}" in text
        broad = await client.call_tool(
            tool,
            {
                **base,
                "after_receipt": token,
                "plan": {"queries": [{"select": "lines", "where": {"grep": f"{term}|30"}}]},
            },
        )
        assert not broad.is_error, broad.content
        text = broad.content[0].text
        assert "within the subject window: [decision#line-1], [decision#line-2]." in text
        assert "outside that window:" in text
        assert "[prototype#line-1] The prototype weighs 30 grams." in text
        copied = await client.call_tool(
            tool,
            {
                **base,
                "after_receipt": token,
                "plan": {"queries": [{"select": "lines", "where": {"grep": "30", "near": near}}]},
            },
        )
        focus = {"citation": "decision#line-1", "grep": "30"}
        continued = {"after_receipt": token, "neighbors": 0, "follow_up": focus}
        followed = await client.call_tool(tool, continued)
        for response in (copied, followed):
            assert not response.is_error, response.content
            text = response.content[0].text
            assert "[decision#line-2] The approved value is 30." in text
            assert "[decision#line-1]" in text
            assert "[prototype#line-1]" not in text
            assert "weighs 30 grams" not in text

        wrong = await client.call_tool(
            tool, {**continued, "follow_up": {**focus, "citation": "decision#line-6"}}
        )
        assert wrong.is_error and "citation offered for follow-up" in wrong.content[0].text
        without_receipt = await client.call_tool(tool, {"follow_up": focus})
        assert without_receipt.is_error and "needs after_receipt" in without_receipt.content[0].text

        # An append changes the snapshot even though the anchors in this recording remain.
        added = sources[1]["source"]
        added["payload"]["conversation_id"] = "another"
        added["payload"]["metadata"]["source_reference"] = "another"
        path.write_text(json.dumps({"sources": [{"source": added}]}), encoding="utf-8")
        assert main(["--workspace", str(workspace), "index-sources", "--input", str(path)]) == 0
        capsys.readouterr()
        stale = await client.call_tool(tool, continued)
        assert stale.is_error and "stale_snapshot" in stale.content[0].text


async def test_followup_preserves_original_stage_binding_when_head_already_delivered(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "input.json"
    sources = []
    for name, anchor, detail in (
        ("first", "Formal approval for the launch.", "Execution detail is signed."),
        ("second", "Provisional approval for the test.", "Execution detail is pending."),
    ):
        sources.append(
            {
                "source": {
                    "format": "conversation",
                    "payload": {
                        "conversation_id": name,
                        "source": "test",
                        "metadata": {"source_reference": name},
                        "segments": [
                            {"segment_id": "approval", "text": anchor},
                            {"segment_id": "execution", "text": detail},
                        ],
                    },
                }
            }
        )
    source_path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(source_path)]) == 0
    capsys.readouterr()
    first = {"select": "lines", "where": {"grep": "approval"}, "prefer": ["formal"]}
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"
        options = {"question": "Find the launch evidence.", "neighbors": 0, "limit": 1}
        initial = await client.call_tool(
            tool, {**options, "plan": {"queries": [first]}, "include_receipt": True}
        )
        assert not initial.is_error
        text = initial.content[0].text
        assert "[first#approval] Formal approval" in text and "[second#approval]" not in text
        token = re.search(r"Evidence receipt ([a-f0-9]{24})\.", text)[1]
        stages = [
            {"name": "approval", **first},
            {
                "name": "execution",
                "select": "lines",
                "where": {"units": "$approval.unit", "grep": "execution"},
            },
        ]
        follow = await client.call_tool(
            tool, {**options, "plan": {"stages": stages}, "after_receipt": token}
        )
        assert not follow.is_error
        text = follow.content[0].text
        assert "[first#execution] Execution detail is signed." in text
        assert "[second#execution]" not in text and "Formal approval" not in text
        assert "Stage approval matched 2 original turns without a quote." not in text


async def test_large_recall_guides_branches_and_trims_only_implicit_context(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "input.json"
    sources = [
        {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "text",
                    "metadata": {"source_reference": name},
                    "segments": [
                        {"segment_id": "before", "text": "Planning discussion. " * 126},
                        {"segment_id": "decision", "text": text},
                        {
                            "segment_id": "after",
                            "text": "Administrative notes. " * 126 + tail,
                        },
                    ],
                },
            }
        }
        for name, text, tail in [
            ("shift-log", "Approve the cutoff at eight.", "This starts tomorrow."),
            ("allocation-notes", "Set the quota to seven units.", ""),
        ]
    ]
    payload.write_text(json.dumps({"sources": sources}), encoding="utf-8")
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(payload)]) == 0
    capsys.readouterr()
    args = {
        "question": "When do the cutoff and quota take effect?",
        "plan": {
            "queries": [{"select": "lines", "where": {"grep": cue}} for cue in ("cutoff", "quota")]
        },
        "max_bytes": 18000,
    }
    payload.write_text(
        json.dumps({"question": args["question"], "plan": args["plan"]}), encoding="utf-8"
    )
    assert (
        main(
            [
                "--workspace",
                str(workspace),
                "symbolic",
                "--input",
                str(payload),
                "--save",
                "both",
                "--neighbors",
                "1",
                "--max-bytes",
                "18000",
            ]
        )
        == 0
    )
    capsys.readouterr()
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        result = await client.call_tool("recall_conversation_evidence", args)
        assert not result.is_error
        focused = result.content[0].text
        assert "Omitted" in focused and "incidental adjacent" in focused
        assert "- Query 1 · [shift-log#decision]" in focused
        assert "- Query 2 · [allocation-notes#decision]" in focused
        assert "[shift-log#decision] Approve the cutoff at eight." in focused
        assert "[allocation-notes#decision] Set the quota to seven units." in focused
        assert "[shift-log#after]" in focused and "This starts tomorrow." in focused
        assert "[allocation-notes#after]" not in focused
        explicit = await client.call_tool("recall_conversation_evidence", {**args, "neighbors": 1})
        assert not explicit.is_error
        context = explicit.content[0].text
        saved = await client.call_tool("reuse_conversation_query", {"run_id": "both"})
        assert not saved.is_error
        assert "Direct matches by query branch" in saved.content[0].text
        assert "Evidence receipt " in saved.content[0].text
        assert "incidental adjacent" not in context
        for name in ("shift-log", "allocation-notes"):
            assert all(f"[{name}#{segment}]" in context for segment in ("before", "after"))
            assert all(
                f"[{name}#{segment}]" in saved.content[0].text for segment in ("before", "after")
            )
        assert len(focused.encode()) < len(context.encode())


async def test_symbolic_recall_labels_recording_dates_separately_from_spoken_dates(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "input.json"
    records = [
        ("report-before", "2026-01-12", "I cancelled our CedarCart subscription."),
        ("report-after", "2026-01-20", "I ordered lunch from PaperRoute."),
        ("similar-name", "2026-01-16", "I ordered a subscription to PaperRound on January 16."),
        ("undated", None, "I ordered extra supplies from PaperRoute."),
        ("excluded-by-scope", "2026-02-01", "I ordered more from PaperRoute."),
    ]
    batch = {
        "sources": [
            {
                "source": {
                    "format": "conversation",
                    "payload": {
                        "conversation_id": name,
                        "source": "text",
                        "metadata": {"source_reference": name},
                        **({"source_date": day} if day else {}),
                        "segments": [{"segment_id": "report", "text": quote}],
                    },
                },
            }
            for name, day, quote in records
        ]
    }
    payload.write_text(json.dumps(batch), encoding="utf-8")
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(payload)]) == 0
    capsys.readouterr()
    plan = {
        "queries": [
            {
                "select": "lines",
                "where": {"grep": subject, "date_wording": "calendar"},
                "prefer": [event],
            }
            for subject, event in [
                ("CedarCart|Cedar Cart", "cancelled"),
                ("PaperRoute|Paper Route", "ordered"),
            ]
        ]
    }
    args = {
        "question": "What dates were the CedarCart cancellation and PaperRoute order reported?",
        "plan": plan,
        "end_date": "2026-01-31",
        "neighbors": 0,
    }
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        result = await client.call_tool("recall_conversation_evidence", args)
        assert not result.is_error
        text = result.content[0].text
        assert "Queries 1, 2 found subject turns with recording dates" in text
        assert "Recording date: 2026-01-12\n[report-before#report]" in text
        assert "Recording date: 2026-01-20\n[report-after#report]" in text
        assert all(f"[{name}#report]" not in text for name, _, _ in records[2:])
        strict = await client.call_tool(
            "recall_conversation_evidence", {**args, "plan": {**plan, "retry_date_subject": False}}
        )
        assert not strict.is_error
        assert "[report-before#report]" not in strict.content[0].text
        assert "[report-after#report]" not in strict.content[0].text
        unrelated = await client.call_tool(
            "recall_conversation_evidence",
            {
                **args,
                "plan": {
                    "queries": [
                        {
                            "select": "lines",
                            "where": {"grep": "Paper Rook", "date_wording": "calendar"},
                        }
                    ]
                },
            },
        )
        assert not unrelated.is_error
        assert "Query 1 matched no original turns" in unrelated.content[0].text
        assert "[similar-name#report]" not in unrelated.content[0].text


async def test_project_bound_implementation_cites_reported_outcome_within_quote_limit(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "input.json"
    source_rows = [
        ("anchor", "Boiler", "The workshop uses an amber yard tag."),
        ("debate", "Boiler", "The standard build might include relay blocking."),
        ("proposal", "Boiler", "If we kept the relay blocking, do we need another test?"),
        ("delivery", "Boiler", "We left the relay blocking out."),
        ("other", "Vent", "We left the relay blocking out."),
    ]
    payload.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source": {
                            "format": "conversation",
                            "payload": {
                                "conversation_id": name,
                                "source": "text",
                                "metadata": {"source_reference": name, "project": project},
                                "segments": [{"segment_id": "one", "text": text}],
                            },
                        }
                    }
                    for name, project, text in source_rows
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(payload)]) == 0
    capsys.readouterr()
    args = {
        "question": "Implement the policy for the project with the amber yard tag.",
        "anchor_quote": "amber yard tag",
        "plan": {
            "queries": [
                {
                    "select": "lines",
                    "where": {"grep": "relay blocking"},
                    "prefer": ["standard|include"],
                }
            ]
        },
        "limit": 3,
        "neighbors": 0,
    }
    tool = "recall_conversation_evidence"
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        result = await client.call_tool(tool, args)
        assert not result.is_error
        text = result.content[0].text
        assert "Original wording reporting what was included or left out: [delivery#one]" in text
        assert "[delivery#one] We left the relay blocking out." in text
        assert "[debate#one] The standard build might include relay blocking." in text
        assert "[proposal#one]" not in text and "[other#one]" not in text
        short = await client.call_tool(tool, {**args, "limit": 2})
        assert not short.is_error
        assert "Original wording reporting" not in short.content[0].text


async def test_project_branches_keep_a_match_from_an_unquoted_recording(tmp_path, capsys):
    workspace = tmp_path / "workspace"
    path = tmp_path / "input.json"
    sources = {
        "anchor": ["The stencil has an amber yard tag."],
        "primary": ["Budget primary discussion.", "Budget repeat discussion."],
        "estimate": ["The budget is twenty.", "The schedule is July."],
        "deadline": ["The schedule is June."],
        "other": ["The budget is forty."],
    }
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source": {
                            "format": "conversation",
                            "payload": {
                                "conversation_id": name,
                                "source": "text",
                                "metadata": {
                                    "source_reference": name,
                                    "project": "Vent" if name == "other" else "Boiler",
                                },
                                "segments": [
                                    {"segment_id": str(i), "text": line}
                                    for i, line in enumerate(lines, 1)
                                ],
                            },
                        }
                    }
                    for name, lines in sources.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["--workspace", str(workspace), "index-sources", "--input", str(path)]) == 0
    capsys.readouterr()
    args = {
        "question": "Find the budget and schedule for the amber yard tag project.",
        "anchor_quote": "amber yard tag",
        "limit": 4,
        "neighbors": 0,
    }
    tool = "recall_conversation_evidence"
    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:

        def plan(month):
            return {
                "queries": [
                    {"select": "lines", "where": {"grep": "budget"}, "prefer": ["primary|repeat"]},
                    {"select": "lines", "where": {"grep": f"schedule is {month}"}},
                ]
            }

        reply = await client.call_tool(tool, {**args, "plan": plan("June")})
        assert not reply.is_error
        text = reply.content[0].text
        assert set(re.findall(r"^\[([^]]+)\]", text, re.M)) == {
            "anchor#1",
            "primary#1",
            "estimate#1",
            "deadline#1",
        }
        another = await client.call_tool(tool, {**args, "plan": plan("July")})
        assert not another.is_error
        assert set(re.findall(r"^\[([^]]+)\]", another.content[0].text, re.M)) == {
            "anchor#1",
            "primary#1",
            "primary#2",
            "estimate#2",
        }
        shared = plan("June")
        shared["queries"][1].update(
            {"where": {"grep": "schedule is June|Budget repeat"}, "prefer": ["schedule is June"]}
        )
        joint = await client.call_tool(tool, {**args, "plan": shared})
        assert not joint.is_error
        assert set(re.findall(r"^\[([^]]+)\]", joint.content[0].text, re.M)) == {
            "anchor#1",
            "primary#1",
            "primary#2",
            "deadline#1",
        }


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


async def test_recall_automatically_uses_one_exact_index_for_a_confirmed_comparison(
    tmp_path, capsys
):
    workspace = tmp_path / "workspace"
    payload = tmp_path / "input.json"

    def cli(*args, value=None):
        if value is not None:
            payload.write_text(json.dumps(value), encoding="utf-8")
            args = (*args, "--input", str(payload))
        assert main(["--workspace", str(workspace), *args]) == 0
        return json.loads(capsys.readouterr().out)

    def note(name, project, date, subject, decision):
        turns = [
            {"segment_id": "subject", "text": subject},
            {"segment_id": "decision", "text": decision},
            {
                "segment_id": "rejected",
                "text": "The current proposal would set the approved cooldown to 3 minutes.",
            },
        ]
        return {
            "source": {
                "format": "conversation",
                "payload": {
                    "conversation_id": name,
                    "source": "text",
                    "source_date": date,
                    "metadata": {"source_reference": name, "project": project},
                    "segments": turns,
                },
            },
            "annotations": {
                "facts": [],
                "index_spans": [{"segment_ids": ["subject"], "entities": ["Inlet regulator"]}],
            },
        }

    earlier = note(
        "initial",
        "Foundry",
        "2026-04-05",
        "This is the part between the reservoir and the relief leg on the press.",
        "The original approved cooldown for that fixture is 25 minutes.",
    )
    current = note(
        "review",
        "Foundry",
        "2026-04-09",
        "The control valve coming from the reservoir into the press is up next.",
        "The revised approved cooldown for that fixture is 12 minutes.",
    )
    cli("index-sources", value={"sources": [earlier, current]})
    request = {
        "question": "Use the original and current approved cooldown for the inlet regulator.",
        "plan": {"queries": [{"select": "lines", "where": {"grep": "inlet regulator"}}]},
    }
    receipt = cli(
        "symbolic",
        "--save",
        "indexed-comparison",
        "--limit",
        "2",
        value={**request, "retry_index_subject": True},
    )
    assert receipt["compiled_plan"] == request["plan"]
    assert receipt["trace"][0]["stage"] == "indexed_subject_comparison"
    assert receipt["trace"][0]["label"] == "inlet_regulator"
    citations = {
        line["source_line_id"]: line["segment_id"]
        for span in receipt["spans"]
        for line in span["evidence"]
    }
    assert [citations[line] for line in receipt["trace"][0]["subject_line_ids"]] == [
        "subject",
        "subject",
    ]
    assert [step["hits"] for step in receipt["trace"] if step.get("via") == "near"] == [1, 1]
    refs = {
        f"[{source['source']['payload']['conversation_id']}#{segment}]"
        for source in (earlier, current)
        for segment in ("subject", "decision")
    }
    saved = cli("symbolic", "--reuse", "indexed-comparison")
    assert saved["plan"] == receipt["plan"]
    bare = render_symbolic_context(receipt, show_indexed_subject=False)
    limit = len(json.dumps(bare.strip(), ensure_ascii=False).encode())
    trimmed = cli(
        "symbolic",
        "--delivery-bytes",
        str(limit),
        value={**request, "retry_index_subject": True},
    )
    assert all(ref in trimmed for ref in refs) and "Indexed subject" not in trimmed

    async with Client(create_workspace_server(workspace), raise_exceptions=False) as client:
        tool = "recall_conversation_evidence"

        async def call(**overrides):
            answer = await client.call_tool(tool, {**request, "neighbors": 0, **overrides})
            assert not answer.is_error
            return answer.content[0].text

        text = await call()
        assert (
            "Indexed subject inlet regulator (entity) links [initial#subject] "
            "to the earlier decision [initial#decision] and [review#subject] "
            "to the later decision [review#decision]." in text
        )
        assert all(ref in text for ref in refs)
        assert "#rejected]" not in text
        assert "Indexed subject" not in await call(max_bytes=128)
        assert "Indexed subject" not in await call(start_date="2026-04-07")
        assert "Indexed subject" not in await call(
            plan={**request["plan"], "retry_index_subject": False}
        )
        assert "Indexed subject" not in await call(
            plan={
                "queries": [
                    {"select": "lines", "where": {"grep": "inlet regulator", "who": ["sam"]}}
                ]
            }
        )
        dimensions = note(
            "dimensions",
            "Foundry",
            "2026-04-10",
            "The part at the relief leg and the control valve coming from the reservoir are the same size.",
            "Keep the dimensions in the recording.",
        )
        cli("index-sources", value={"sources": [dimensions]})
        assert "Original wording linking the two subject descriptions" not in await call()
        names = note(
            "names",
            "Foundry",
            "2026-04-10",
            "The part at the relief leg and the control valve coming from the reservoir are the same device.",
            "Keep both descriptions in the recording.",
        )
        cli("index-sources", value={"sources": [names]})
        text = await call()
        assert (
            "Original wording linking the two subject descriptions is at [names#subject]." in text
        )
        assert "[names#subject] The part at the relief leg" in text
        without_room = await call(max_bytes=receipt["quote_bytes"])
        assert all(ref in without_room for ref in refs) and "[names#subject]" not in without_room
        assert "[names#subject]" not in await call(limit=2)
        contradictory = note(
            "correction",
            "Foundry",
            "2026-04-11",
            "The part at the relief leg and the control valve coming from the reservoir aren't the same device.",
            "Keep each item's history separate.",
        )
        cli("index-sources", value={"sources": [contradictory]})
        text = await call()
        assert all(ref in text for ref in refs)
        assert "Original wording linking the two subject descriptions" not in text
        assert "[names#subject]" not in text
        assert (
            "Original wording questioning or distinguishing the two subject descriptions "
            "is at [correction#subject]." in text
        )
        other = note(
            "other",
            "Finishing",
            "2026-04-10",
            "This is the circulation valve in the second machine.",
            "The revised approved cooldown for that fixture is 6 minutes.",
        )
        cli("index-sources", value={"sources": [other]})
        assert "Indexed subject" not in await call()
        text = await call(project="Foundry")
        assert all(ref in text for ref in refs) and "[other#decision]" not in text
        old_revision = next(
            line["revision_checksum"]
            for span in receipt["spans"]
            for line in span["evidence"]
            if line["conversation_id"] == "review"
        )
        corrected = note(
            "review",
            "Foundry",
            "2026-04-09",
            "The control valve coming from the reservoir into the press is up next.",
            "The revised approved cooldown for that fixture is 9 minutes.",
        )
        corrected["source"]["expected_checksum"] = old_revision
        corrected["annotations"]["index_spans"][0]["segment_ids"] = ["decision"]
        cli("index-sources", value={"sources": [corrected]})
        text = await call(project="Foundry")
        assert "[review#decision]" in text and "[review#subject]" not in text
        assert "9 minutes." in text and "12 minutes." not in text
        assert "and [review#decision] to the later decision [review#decision]" in text


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
        missed = {
            **exact,
            "plan": {"queries": [{"select": "lines", "where": {"grep": "turnkey shipment"}}]},
        }
        empty = await client.call_tool(tool, missed)
        text = empty.content[0].text
        assert not empty.is_error and "The anchor identifies Heron (2 recordings)." in text
        assert '"topics":["rollout (1)"]' in text and '"event":["approval (1)"]' in text
        assert '"entities":["mina (1)"]' in text and "depot" not in text
        assert "[decision#line]" not in text
        recovered = await client.call_tool(
            tool,
            {
                "plan": plan,
                "after_receipt": re.search(r"Evidence receipt ([a-f0-9]{24})\.", text)[1],
            },
        )
        assert not recovered.is_error and "[decision#line] Mina agreed" in recovered.content[0].text
        assert "Index codes in this scope" not in recovered.content[0].text
        ambiguous = await client.call_tool(
            tool, {k: v for k, v in missed.items() if k != "start_date"}
        )
        assert not ambiguous.is_error and "matches multiple projects" in ambiguous.content[0].text
        assert "Index codes in this scope" not in ambiguous.content[0].text
        request = {
            **args,
            "snapshot": hints["snapshot"],
            "plan": plan,
            "anchor_quote": "folding hinge",
        }
        before = await client.call_tool(tool, request)
        assert not before.is_error and "[decision#line] Mina agreed" in before.content[0].text
        receipt = re.search(r"Evidence receipt ([a-f0-9]{24})\.", before.content[0].text)[1]
        follow = {
            "question": "Find another matching turn.",
            "after_receipt": receipt,
            "plan": {"queries": [{"select": "lines", "where": {"grep": "ship|local depot"}}]},
        }
        continued = await client.call_tool(tool, follow)
        assert not continued.is_error
        assert "Recording dates: 2026-04-06 to any" in continued.content[0].text
        assert "[decision#line]" in continued.content[0].text
        assert "[old#line]" not in continued.content[0].text
        assert "[outside#line]" not in continued.content[0].text
        assert "Evidence receipt " in continued.content[0].text

        broader = await client.call_tool(tool, {**follow, "project": "Falcon"})
        assert not broader.is_error and "[old#line]" in broader.content[0].text
        assert "[outside#line]" in broader.content[0].text
        direct_plan = {
            "queries": [{"select": "lines", "where": {"grep": "depot", "unit": "outside"}}]
        }
        named = await client.call_tool(tool, {**follow, "plan": direct_plan})
        assert not named.is_error and "[outside#line]" in named.content[0].text
        staged = {
            "stages": [{"name": "detail", "select": "lines", "where": {"grep": "local depot"}}]
        }
        external = await client.call_tool(tool, {**follow, "plan": staged})
        assert not external.is_error and "[outside#line]" in external.content[0].text

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
