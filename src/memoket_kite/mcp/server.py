"""Expose one writable KITE memory through MCP stdio.

The SDK is imported only when constructing a server, so the base library and
module imports remain usable without the optional ``mcp`` extra.
"""

import argparse
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Sequence

from memoket_kite import ConfigurationError, Fact, KiteError, Memory

if TYPE_CHECKING:
    from mcp.server import MCPServer


def _serialize_fact(fact: Fact) -> dict[str, object]:
    return {
        "id": fact.id,
        "content": fact.content,
        "kind": fact.kind,
        "subject": fact.subject,
        "topics": list(fact.topics),
        "entities": list(fact.entities),
        "event_time": fact.event_time,
        "source_time": fact.source_time,
        "confidence": fact.confidence,
        "sources": [dict(source) for source in fact.sources],
    }


def _load_memory(memory_path: str | Path) -> Memory:
    try:
        path = Path(memory_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ConfigurationError(f"memory must be an existing XML file: {path}")
        if not os.access(path, os.R_OK | os.W_OK):
            raise ConfigurationError(f"memory file must be readable and writable: {path}")
        # Memory.remember() atomically replaces the XML in its parent directory.
        if not os.access(path.parent, os.W_OK | os.X_OK):
            raise ConfigurationError(f"memory directory must be writable: {path.parent}")
        return Memory.load(path)
    except ConfigurationError:
        raise
    except (OSError, RuntimeError, KiteError) as exc:
        raise ConfigurationError(f"could not configure memory {memory_path!s}: {exc}") from exc


def create_server(memory_path: str | Path) -> "MCPServer":
    """Load one artifact once and register the three public Memory operations."""
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
        from pydantic import BaseModel, ConfigDict, Field, StringConstraints
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        raise ConfigurationError(
            'MCP support is not installed; run: python -m pip install "memoket-kite[mcp]"'
        ) from exc

    memory = _load_memory(memory_path)
    server = MCPServer("KITE Memory")
    # SDK sync handlers run in worker threads. Serialize this instance's calls;
    # separate processes/writers against the same artifact are still unsupported.
    lock = Lock()

    @contextmanager
    def memory_call():
        with lock:
            try:
                yield
            except KiteError as exc:
                raise ToolError(f"{type(exc).__name__}: {exc}") from exc

    nonempty_text = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r"\S")]
    positive_limit = Annotated[int, Field(strict=True, ge=1)]

    class Message(BaseModel):
        model_config = ConfigDict(extra="forbid")

        role: nonempty_text
        content: nonempty_text

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))
    def kite_remember(
        messages: Annotated[list[Message], Field(min_length=1)],
        session_id: Annotated[
            str,
            StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"),
        ]
        | None = None,
        date: Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")]
        | None = None,
        title: Annotated[str, Field(strict=True)] | None = None,
    ) -> dict[str, object]:
        """Store a conversation in the configured XML memory using KITE's provider.

        Roles may be speaker names. Date is YYYY-MM-DD; omitted dates use KITE's
        current date. Supply a unique session_id to identify the conversation.
        Returned facts include original source IDs, dates, roles, and content.
        """
        with memory_call():
            facts = memory.remember(
                [message.model_dump() for message in messages],
                session_id=session_id,
                date=date,
                title=title,
            )
            return {
                "status": "remembered",
                "session_id": session_id,
                "facts": [_serialize_fact(fact) for fact in facts],
            }

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    def kite_recall(query: nonempty_text, limit: positive_limit = 10) -> dict[str, object]:
        """Recall KITE facts with source IDs, dates, roles, and original content.

        Uses KITE's existing query planner and provider. No matches returns an
        empty facts list. Limit must be a positive integer.
        """
        with memory_call():
            facts = memory.recall(query, limit=limit)
            return {"query": query, "facts": [_serialize_fact(fact) for fact in facts]}

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
    def kite_answer(question: nonempty_text, limit: positive_limit = 10) -> dict[str, object]:
        """Answer through KITE's provider and return its citations and evidence.

        Evidence facts retain source IDs, dates, roles, and original content.
        Citations may also name raw lines or instances absent from Fact evidence.
        Evidence records what KITE's reader saw; it is not a proof of the answer.
        """
        with memory_call():
            result = memory.answer_with_evidence(question, limit=limit)
            return {
                "answer": result.text,
                "citations": list(result.citations),
                "evidence": [_serialize_fact(fact) for fact in result.evidence],
            }

    return server


def main(argv: Sequence[str] | None = None) -> None:
    """Start the local stdio server with an explicitly configured XML path."""
    parser = argparse.ArgumentParser(description="Serve one local KITE XML memory over MCP stdio.")
    parser.add_argument(
        "--memory", type=Path, required=True, help="Existing writable XML memory file"
    )
    args = parser.parse_args(argv)
    try:
        server = create_server(args.memory)
    except ConfigurationError as exc:
        parser.error(str(exc))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
