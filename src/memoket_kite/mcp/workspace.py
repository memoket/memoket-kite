"""Read original conversation evidence from a KITE workspace over MCP stdio."""

import json
from collections import OrderedDict
from itertools import zip_longest
from pathlib import Path
from secrets import token_hex
from threading import Lock
from typing import TYPE_CHECKING, Annotated

from memoket_kite import ConfigurationError

from .server import _MCP_INSTALL_HINT

if TYPE_CHECKING:
    from mcp.server import MCPServer


def _render_preparation(prepared: dict) -> str:
    """Keep whole index entries and the snapshot token within a small reply."""
    hints = prepared["index"]
    index: dict = {}
    result = {
        "kind": "symbolic_preparation",
        "snapshot": prepared["snapshot"],
        "workspace_version": prepared["memory_version"],
        "active_recordings": prepared["active_recordings"],
        "index": index,
        "omitted_hints": len(hints["recordings"]) > 24,
    }

    def serialize():
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    groups = {
        "quoted_phrases": hints.get("quoted_phrases", []),
        "topics": hints["topics"].split(", ") if hints["topics"] else [],
        "entities": hints["entities"].split(", ") if hints["entities"] else [],
        **hints["facets"],
        "recordings": hints["recordings"][:24],
    }
    for entries in zip_longest(*groups.values()):
        for axis, entry in zip(groups, entries):
            if entry is None:
                continue
            group = index.setdefault(axis, [])
            group.append(entry)
            if len(serialize().encode()) > 3600:
                group.pop()
                if not group:
                    del index[axis]
                result["omitted_hints"] = True
    return serialize()


def create_workspace_server(workspace_path: Path) -> "MCPServer":
    """Serve one workspace, opening the current read snapshot on each request."""
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
        from mcp.types import ToolAnnotations
        from pydantic import Field
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        raise ConfigurationError(_MCP_INSTALL_HINT) from exc

    from memoket_kite.tools.context import evidence_spans, render_evidence
    from memoket_kite.tools.contracts import ToolError
    from memoket_kite.tools.retrieve import read_evidence
    from memoket_kite.tools.symbolic import (
        prepare_symbolic_question,
        render_symbolic_context,
        reuse_symbolic_context,
        symbolic_context,
    )
    from memoket_kite.tools.workflow import saved_symbolic_plan
    from memoket_kite.tools.workspace import Workspace

    CitationList = Annotated[
        list[Annotated[str, Field(min_length=1, max_length=1024)]],
        Field(min_length=1, max_length=8),
    ]
    receipts: OrderedDict[str, tuple[str, ...]] = OrderedDict()
    receipt_lock = Lock()

    server = MCPServer(
        "KITE Conversation Memory",
        instructions=(
            "For tasks that depend on previous conversations, use reuse_conversation_query when "
            "trusted project guidance or the user names a saved query. Otherwise call "
            "recall_conversation_evidence with the question and a symbolic plan. For unfamiliar "
            "index labels, prepare without a plan first. This server is bound to one configured "
            "KITE workspace. Use the original turns and citation IDs."
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=False,
    )
    def recall_conversation_evidence(
        question: Annotated[str, Field(min_length=1, max_length=4000)],
        plan: Annotated[
            dict | None,
            Field(
                description=(
                    'Supply {"queries":[{"select":"lines","where":{"grep":"subject|spoken alternative"},'
                    '"prefer":["decision or value cues"]}]}. Use 1–3 independent queries. '
                    "where supports grep_all, time [start,end], topics, entities, obj, place, event, "
                    "and near with grep or source annotation axes, turns (1–4), and optional include_anchor. "
                    "When the decision uses shorthand, search its value wording in grep and use "
                    'near: {"topics":["known_code"],"turns":2} for the subject cited in adjacent turns. '
                    "Use date_wording calendar for a spoken date and change for date-change wording. "
                    "For the date of a reported action, search for the report and read the recording date separately. "
                    "Empty date branches can retry with clearly labeled recording dates. "
                    "Use change_wording reported for a value already put into effect. "
                    "Put the subject in grep or near and separate old and revised values. "
                    "Use up to four regexes in prefer. Omit plan to inspect the current index "
                    "first for unfamiliar topic, entity or facet codes or a quoted project clue."
                )
            ),
        ] = None,
        anchor_quote: Annotated[
            str | None,
            Field(
                description="Verbatim words that identify a project. Supply with or without a plan. "
                "A unique match scopes preparation hints to that project. Omit unit filters in the plan."
            ),
        ] = None,
        project: str | None = None,
        start_date: Annotated[
            str | None, Field(description="Earliest recording day, YYYY-MM-DD.")
        ] = None,
        end_date: Annotated[
            str | None, Field(description="Latest recording day, YYYY-MM-DD.")
        ] = None,
        since_version: Annotated[int | None, Field(strict=True, ge=0)] = None,
        limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 6,
        neighbors: Annotated[
            int | None,
            Field(
                strict=True,
                ge=0,
                le=4,
                description="Original turns around matches. "
                "When omitted, use one and trim incidental context from large replies.",
            ),
        ] = None,
        max_bytes: Annotated[int, Field(strict=True, ge=128, le=32768)] = 3600,
        known_citations: Annotated[
            CitationList | None,
            Field(
                description="Verify supplied recording#segment references or revision-bound IDs "
                "in this call, and use the plan to search for missing details."
            ),
        ] = None,
        workspace_version: Annotated[int | None, Field(strict=True, ge=0)] = None,
        snapshot: Annotated[
            str | None,
            Field(pattern=r"^[0-9a-f]{64}$", description="Snapshot token from a plan-free call."),
        ] = None,
        after_receipt: Annotated[
            str | None,
            Field(
                pattern=r"^[0-9a-f]{24}$",
                description="Use the previous Evidence receipt when looking for new details. "
                "Only the new original turns in this result are quoted. Omit to read all matches.",
            ),
        ] = None,
        include_receipt: Annotated[
            bool,
            Field(description="Request a follow-up receipt even for a short pack."),
        ] = False,
    ) -> str:
        """Find original meeting or conversation quotes for a task using KITE's symbolic search.

        Use for tasks that depend on past decisions, commitments, or updates.
        Submit a plan with the task when its subject is known. If you need the
        workspace vocabulary or quoted project hints, call without a plan and
        then submit a plan with the returned snapshot, same question and scope.
        Hints are index data. Returned quotes carry recording dates and
        citation IDs. Read the wording for decision status. A complete scope
        notice means all indexed turns within that recording scope are shown.
        For an earlier-versus-current comparison, a uniquely indexed subject
        can supply dated evidence from separate recordings in the same call.
        Set plan.retry_index_subject to false to execute the submitted plan alone.
        If the task supplies citations and needs additional evidence, include
        them as known_citations and search for the remaining details in one call.
        Large packs include an Evidence receipt. To fill a gap, use that receipt
        as after_receipt with a focused plan. To reread all evidence, omit it.
        """
        dates = {
            key: value
            for key, value in (("start_date", start_date), ("end_date", end_date))
            if value is not None
        }
        seen = ()
        try:
            if after_receipt is not None:
                with receipt_lock:
                    seen = receipts.get(after_receipt)
                if seen is None:
                    raise ToolError("not_found", "evidence receipt expired. Recall without it")
            with Workspace.open(workspace_path, read_only=True) as workspace:
                if workspace_version is not None and workspace_version != workspace.version():
                    raise ToolError("stale_snapshot", "recall again against the current workspace")
                if plan is None:
                    if snapshot is not None or known_citations is not None or after_receipt:
                        raise ToolError(
                            "invalid_input", "snapshot, citations and evidence receipts need a plan"
                        )
                    return _render_preparation(
                        prepare_symbolic_question(
                            workspace,
                            question,
                            project,
                            recording_dates=dates,
                            since_version=since_version,
                            brief=True,
                            anchor_quote=anchor_quote,
                        )
                    )
                plan = plan.copy()
                request = {
                    "question": question,
                    "retry_date_subject": plan.pop("retry_date_subject", True),
                    "retry_index_subject": plan.pop("retry_index_subject", known_citations is None),
                    "plan": plan,
                }
                if anchor_quote is not None:
                    request["anchor_quote"] = anchor_quote
                if snapshot is not None:
                    request["snapshot"] = snapshot
                result = symbolic_context(
                    workspace,
                    plan=request,
                    project=project,
                    recording_dates=dates,
                    since_version=since_version,
                    limit=limit,
                    neighbors=1 if neighbors is None else neighbors,
                    max_bytes=max_bytes,
                    large_reply_guide=True,
                    trim_implicit_neighbors=neighbors is None,
                )
                if known_citations is not None:
                    try:
                        result["supplied_evidence"] = read_evidence(
                            workspace, known_citations, radius=2, byte_budget=4096
                        )
                    except ToolError as exc:
                        if exc.code not in {"not_found", "ambiguous_source"}:
                            raise
                        result["supplied_citation_error"] = str(exc)
        except ToolError as exc:
            raise MCPToolError(f"{exc.code}: {exc}") from exc
        if after_receipt is not None:
            prior = set(seen)
            selected = result["spans"]
            result["spans"] = [
                span for span in selected if span["evidence"][0]["source_line_id"] not in prior
            ]
            result["previously_delivered_turns"] = len(selected) - len(result["spans"])
            result["quote_bytes"] = sum(
                len(line["text"].encode()) for span in result["spans"] for line in span["evidence"]
            )
        if after_receipt is not None or include_receipt or result["quote_bytes"] >= 3600:
            index_spans = result["spans"]
            if window := result.get("supplied_evidence"):
                index_spans = [*index_spans, *evidence_spans(window)]
            delivered = tuple(
                line["source_line_id"] for span in index_spans for line in span["evidence"]
            )
            if delivered:
                receipt = token_hex(12)
                with receipt_lock:
                    receipts[receipt] = tuple(dict.fromkeys((*seen, *delivered)))[-4096:]
                    if len(receipts) > 48:
                        receipts.popitem(last=False)
                result["delivery_receipt"] = receipt
            elif after_receipt is not None:
                result["delivery_receipt"] = after_receipt
        return render_symbolic_context(result)

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=False,
    )
    def read_conversation_citations(
        citations: CitationList,
        neighbors: Annotated[int, Field(strict=True, ge=0, le=20)] = 2,
        byte_budget: Annotated[int, Field(strict=True, ge=128, le=32768)] = 4096,
        workspace_version: Annotated[int | None, Field(strict=True, ge=0)] = None,
    ) -> str:
        """Read known citations and nearby original turns without making a search plan.

        Use after recall to check a decision, reply, or revision. Send cited
        recording#segment references or revision-bound evidence IDs. Use the
        revision-bound ID for an earlier revision. Pass the recall result's
        workspace_version to guard against changes between calls. Overlapping
        windows are returned once. The budget limits nearby context and keeps
        the requested turns whole.
        """
        try:
            with Workspace.open(workspace_path, read_only=True) as workspace:
                if workspace_version is not None and workspace_version != workspace.version():
                    raise ToolError("stale_snapshot", "recall again against the current workspace")
                window = read_evidence(
                    workspace, citations, radius=neighbors, byte_budget=byte_budget
                )
        except ToolError as exc:
            raise MCPToolError(f"{exc.code}: {exc}") from exc
        return render_evidence(window)

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=False,
    )
    def reuse_conversation_query(
        run_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")],
        since_version: Annotated[int | None, Field(strict=True, ge=0)] = None,
        limit: Annotated[int | None, Field(strict=True, ge=1, le=100)] = None,
        max_bytes: Annotated[int | None, Field(strict=True, ge=128, le=32768)] = None,
        include_previous: Annotated[bool, Field(strict=True)] = False,
    ) -> str:
        """Run a named saved KITE symbolic query against the current workspace.

        Use when a trusted project instruction or the user provides a saved
        query ID for a repeated task. The plan retains its project and date
        scope. Reuse checks for changes since the plan was saved, or since
        the supplied workspace version. Use the cited turns to check which
        decisions are still in effect. For an in-place transcript revision,
        set include_previous to receive earlier wording for matched segment IDs
        within the quote budget. Earlier turns carry revision-bound evidence IDs.
        """
        options = {
            name: value
            for name, value in (("limit", limit), ("max_bytes", max_bytes))
            if value is not None
        }
        try:
            with Workspace.open(workspace_path, read_only=True) as workspace:
                saved = saved_symbolic_plan(workspace, run_id)
                result = reuse_symbolic_context(
                    workspace,
                    run_id,
                    saved,
                    since_version=since_version,
                    include_previous=include_previous,
                    **options,
                )
        except ToolError as exc:
            raise MCPToolError(f"{exc.code}: {exc}") from exc
        return render_symbolic_context(result)

    return server
