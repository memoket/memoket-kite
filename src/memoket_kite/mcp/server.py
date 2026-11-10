"""Expose one KITE memory through MCP stdio, optionally read-only.

The SDK is imported only when constructing a server, so the base library and
module imports remain usable without the optional ``mcp`` extra.
"""

import argparse
import os
from contextlib import contextmanager
from importlib.util import find_spec
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Sequence

from memoket_kite import ConfigurationError, Fact, KiteError, Memory

if TYPE_CHECKING:
    from mcp.server import MCPServer

_MCP_INSTALL_HINT = (
    'MCP support is not installed. Run python -m pip install ".[mcp]" from the KITE checkout '
    'or install the same KITE wheel with the "[mcp]" extra.'
)


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


def _validate_memory_path(memory_path: str | Path, *, read_only: bool = False) -> Path:
    try:
        path = Path(memory_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ConfigurationError(f"memory must be an existing XML file: {path}")
        access_mode = os.R_OK if read_only else os.R_OK | os.W_OK
        if not os.access(path, access_mode):
            required = "readable" if read_only else "readable and writable"
            raise ConfigurationError(f"memory file must be {required}: {path}")
        # Memory.remember() atomically replaces the XML in its parent directory.
        if not read_only and not os.access(path.parent, os.W_OK | os.X_OK):
            raise ConfigurationError(f"memory directory must be writable: {path.parent}")
        return path
    except ConfigurationError:
        raise
    except (OSError, RuntimeError, KiteError) as exc:
        raise ConfigurationError(f"could not configure memory {memory_path!s}: {exc}") from exc


def _load_memory(memory_path: str | Path, *, read_only: bool = False, **models: str) -> Memory:
    try:
        return Memory.load(_validate_memory_path(memory_path, read_only=read_only), **models)
    except ConfigurationError:
        raise
    except (OSError, RuntimeError, KiteError) as exc:
        raise ConfigurationError(f"could not configure memory {memory_path!s}: {exc}") from exc


def _check_configuration(
    memory_path: str | Path,
    *,
    model: str | None = None,
    answer_model: str | None = None,
    read_only: bool = False,
) -> None:
    """Inspect local setup without loading memory, providers, or the MCP SDK."""
    print("KITE MCP configuration\n")
    print(f"Memory: {memory_path}")
    print(f"Mode: {'read-only' if read_only else 'read-write'}")
    errors = []
    try:
        _validate_memory_path(memory_path, read_only=read_only)
    except ConfigurationError as exc:
        print("Memory status: unusable")
        errors.append(str(exc))
    else:
        status = "exists, readable" if read_only else "exists, readable, writable (parent writable)"
        print(f"Memory status: {status}")
    print(f"Model: {model if model is not None else 'default'}")
    print(f"Answer model: {answer_model if answer_model is not None else 'default'}")
    for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY"):
        print(f"{name}: {'set' if os.environ.get(name, '').strip() else 'not set'}")
    if find_spec("mcp") is None:
        print("MCP SDK: not installed")
        errors.append(_MCP_INSTALL_HINT)
    else:
        print("MCP SDK: installed")
    if errors:
        raise ConfigurationError("; ".join(errors))
    print("\nConfiguration looks valid.")


def create_server(
    memory_path: str | Path,
    *,
    model: str | None = None,
    answer_model: str | None = None,
    read_only: bool = False,
) -> "MCPServer":
    """Load one artifact once and register the selected public Memory operations."""
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
        from pydantic import BaseModel, ConfigDict, Field, StringConstraints
    except ModuleNotFoundError as exc:
        if exc.name != "mcp":
            raise
        raise ConfigurationError(_MCP_INSTALL_HINT) from exc

    models = {
        key: value
        for key, value in (("model", model), ("answer_model", answer_model))
        if value is not None
    }
    memory = _load_memory(memory_path, read_only=read_only, **models)
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

    if not read_only:
        server.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))(
            kite_remember
        )

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


def _model_name(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("model cannot be empty")
    return value


def main(argv: Sequence[str] | None = None) -> None:
    """Check configuration or start the local stdio server."""
    parser = argparse.ArgumentParser(description="Serve one local KITE XML memory over MCP stdio.")
    parser.add_argument(
        "--memory", type=Path, required=True, help="Existing XML memory file (writable by default)"
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Expose recall and answer only; require read access",
    )
    parser.add_argument("--model", type=_model_name, help="KITE model (default: KITE's default)")
    parser.add_argument(
        "--answer-model", type=_model_name, help="Answer model (default: selected KITE model)"
    )
    parser.add_argument("--check", action="store_true", help="Check local configuration and exit")
    args = parser.parse_args(argv)
    models = {
        key: value
        for key, value in vars(args).items()
        if key in ("model", "answer_model") and value is not None
    }
    try:
        if args.check:
            _check_configuration(args.memory, read_only=args.read_only, **models)
            return
        server = create_server(args.memory, read_only=args.read_only, **models)
    except ConfigurationError as exc:
        parser.error(str(exc))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
