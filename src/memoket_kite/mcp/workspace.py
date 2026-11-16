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

    from memoket_kite.tools.contracts import ToolError
    from memoket_kite.tools.symbolic import render_symbolic_context, symbolic_context
    from memoket_kite.tools.workspace import Workspace

    server = MCPServer(
        "KITE Conversation Memory",
        instructions=(
            "For tasks that depend on previous conversations, call recall_conversation_evidence "
            "directly with the task question and a symbolic plan. This server is bound to one "
            "configured KITE workspace. Use the original turns and citation IDs in its result."
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
    ) -> str:
        """Find original meeting or conversation quotes for a task using KITE's symbolic search.

        Use for tasks that depend on past decisions, commitments, or updates.
        Submit a plan with the task when its subject is known. Returned quotes
        carry recording dates and citation IDs. Check the actual wording for
        decision status and use the supported evidence in the task.
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
        except ToolError as exc:
            raise MCPToolError(f"{exc.code}: {exc}") from exc
        return render_symbolic_context(result)

    return server
