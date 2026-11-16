"""Read original conversation evidence from a KITE workspace over MCP stdio."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from memoket_kite import ConfigurationError

from .server import _MCP_INSTALL_HINT

if TYPE_CHECKING:
    from mcp.server import MCPServer


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

    from memoket_kite.tools.context import render_evidence
    from memoket_kite.tools.contracts import ToolError
    from memoket_kite.tools.retrieve import read_evidence
    from memoket_kite.tools.symbolic import (
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

    server = MCPServer(
        "KITE Conversation Memory",
        instructions=(
            "For tasks that depend on previous conversations, use reuse_conversation_query when "
            "trusted project guidance or the user names a saved query. Otherwise call "
            "recall_conversation_evidence with the question and a symbolic plan. This server is "
            "bound to one configured KITE workspace. Use the original turns and citation IDs."
        ),
    )

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        structured_output=False,
    )
    def recall_conversation_evidence(
        question: Annotated[str, Field(min_length=1, max_length=4000)],
        plan: Annotated[
            dict,
            Field(
                description=(
                    'Use {"queries":[{"select":"lines","where":{"grep":"subject|spoken alternative"},'
                    '"prefer":["decision or value cues"]}]}. Use 1–3 independent queries. '
                    "where supports grep_all, time [start,end], topics, entities, obj, place, event, "
                    "and near with grep, turns (1–4), and optional include_anchor. "
                    "Use date_wording calendar for a date and change for a revised date. Date retry is automatic. "
                    "Use change_wording reported for a value already put into effect. "
                    "Put the subject in grep or near and separate old and revised values. "
                    "Use up to four regexes in prefer."
                )
            ),
        ],
        anchor_quote: Annotated[
            str | None,
            Field(
                description="Verbatim words quoted in the request to identify a project. Omit unit filters."
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
        neighbors: Annotated[int, Field(strict=True, ge=0, le=4)] = 1,
        max_bytes: Annotated[int, Field(strict=True, ge=128, le=32768)] = 3600,
        known_citations: Annotated[
            CitationList | None,
            Field(
                description="Verify supplied recording#segment references or revision-bound IDs "
                "in this call, and use the plan to search for missing details."
            ),
        ] = None,
        workspace_version: Annotated[int | None, Field(strict=True, ge=0)] = None,
    ) -> str:
        """Find original meeting or conversation quotes for a task using KITE's symbolic search.

        Use for tasks that depend on past decisions, commitments, or updates.
        Submit a plan with the task when its subject is known. Returned quotes
        carry recording dates and citation IDs. Check the actual wording for
        decision status and use the supported evidence in the task.
        If the task supplies citations and needs additional evidence, include
        them as known_citations and search for the remaining details in one call.
        """
        plan = plan.copy()
        retry_date_subject = plan.pop("retry_date_subject", True)
        request = {
            "question": question,
            "plan": plan,
            "retry_date_subject": retry_date_subject,
        }
        if anchor_quote is not None:
            request["anchor_quote"] = anchor_quote
        dates = {
            key: value
            for key, value in (("start_date", start_date), ("end_date", end_date))
            if value is not None
        }
        try:
            with Workspace.open(workspace_path, read_only=True) as workspace:
                if workspace_version is not None and workspace_version != workspace.version():
                    raise ToolError("stale_snapshot", "recall again against the current workspace")
                result = symbolic_context(
                    workspace,
                    plan=request,
                    project=project,
                    recording_dates=dates,
                    since_version=since_version,
                    limit=limit,
                    neighbors=neighbors,
                    max_bytes=max_bytes,
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
