"""JSON command line for the agent orchestration contract."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from .audio import audio_source
from .contracts import ToolError, decode_object, encode, read_utf8, validate
from .demo import run_demo
from .ingest import file_source
from .operations import SCHEMAS, SOURCE_SCOPE, TOOLS, Context
from .resources import resource_root
from .workflow import advance, artifact, load_pipeline, recent_runs, start, status
from .workspace import Workspace


def _object(path: str | None) -> dict:
    if path is None:
        return {}

    try:
        raw = sys.stdin.read() if path == "-" else read_utf8(Path(path))
    except UnicodeError as exc:
        raise ToolError("invalid_input", "input must be valid UTF-8") from exc
    return decode_object(raw)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kite-tool", description=__doc__)
    resources = parser.add_mutually_exclusive_group()
    resources.add_argument(
        "--repo", type=Path, help="override packaged resources with a trusted KITE checkout"
    )
    resources.add_argument(
        "--extensions", type=Path, help="add pipelines and skills from a trusted local directory"
    )
    default_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")) / "kite"
    parser.add_argument(
        "--workspace", type=Path, default=Path(os.environ.get("KITE_DATA_DIR") or default_dir)
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pipelines", help="list validated pipelines")
    commands.add_parser("resources", help="show paths to agent instructions and workflows")
    demo = commands.add_parser("demo", help="run the offline text and Memoket quickstart")
    demo.add_argument("--metrics", action="store_true", help="emit deterministic fixture metrics")
    validate = commands.add_parser(
        "validate-pipeline", help="validate and show a pipeline definition"
    )
    validate.add_argument("pipeline")
    describe = commands.add_parser(
        "describe", help="show registered tools and their JSON contracts"
    )
    describe.add_argument("tool", nargs="?", choices=sorted(TOOLS))
    source = commands.add_parser(
        "file-source", help="prepare ingestion input from a local text or conversation export"
    )
    source.add_argument("path", type=Path)
    source.add_argument("--format", choices=("text", "memoket", "conversation"), required=True)
    source.add_argument("--source-id", help="stable identity for a text file")
    date = source.add_mutually_exclusive_group()
    date.add_argument("--source-date", help="known conversation day in YYYY-MM-DD format")
    date.add_argument("--started-at", help="known conversation timestamp with UTC offset")
    source.add_argument("--expected-checksum", help="current source checksum for a revision")
    audio = commands.add_parser(
        "audio-source", help="transcribe a local recording into a memory-ingestion input"
    )
    audio.add_argument("recording", type=Path)
    audio.add_argument("--source-id", required=True, help="stable recording identifier")
    audio.add_argument("--model", type=Path, required=True, help="local whisper.cpp model")
    audio.add_argument("--language", default="auto", help="Whisper language code or auto")
    audio.add_argument("--whisper-cli", default="whisper-cli", help="trusted executable")
    audio.add_argument("--ffmpeg", default="ffmpeg", help="trusted executable")
    audio.add_argument("--timeout", type=int, default=1800, help="seconds allowed per process")
    begin = commands.add_parser("start", help="create a checkpoint for a pipeline run")
    begin.add_argument("pipeline")
    begin.add_argument("--run-id")
    begin.add_argument("--input", metavar="FILE", help="JSON input file or - for stdin")
    stage = commands.add_parser("stage", help="advance or retry a single stage")
    stage.add_argument("run_id")
    stage.add_argument("stage_id")
    stage.add_argument("--input", metavar="FILE", help="additional stage input file or - for stdin")
    stage.add_argument(
        "--summary", action="store_true", help="return stage status and manifest without its value"
    )
    stage.add_argument("--approve", metavar="TOKEN", help="user-approved deletion preview token")
    stage.add_argument(
        "--allow-provider", action="store_true", help="allow the extractor to send source data"
    )
    runs = commands.add_parser("runs", help="list recent workflow runs without source content")
    runs.add_argument("--limit", type=int, default=20, help="number of runs to show (1–100)")
    for name in ("status", "artifact"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        if name == "artifact":
            command.add_argument("stage_id")
    commands.add_parser("integrity", help="verify the current Codebook against its source records")
    backup = commands.add_parser("backup", help="write a verified private copy to a new directory")
    backup.add_argument("destination", type=Path)
    commands.add_parser("deletion-audit", help="show recent deletion receipts without source text")
    sources = commands.add_parser(
        "sources", help="list conversation identities and current checksums"
    )
    sources.add_argument("--source", help="select a source namespace")
    codebook = commands.add_parser(
        "codebook", help="write all or selected Codebook revisions as XML"
    )
    codebook.add_argument("--source", help="select a source namespace")
    codebook.add_argument("--conversation-id", help="select a conversation within --source")
    codebook.add_argument(
        "--active-only", action="store_true", help="include only current source revisions"
    )
    return parser


def _describe(name: str | None) -> dict:
    return {
        key: {
            "effect": tool.effect,
            "inputs": {
                arg: {"kind": kind, "schema": SCHEMAS[kind]} for arg, kind in tool.inputs.items()
            },
            "output": {"kind": tool.output, "schema": SCHEMAS[tool.output]},
        }
        for key, tool in TOOLS.items()
        if name is None or key == name
    }


def _stage_summary(result: dict, stage_id: str) -> dict:
    checkpoint = result["checkpoint"]
    state = checkpoint["stages"][stage_id]
    summary = {
        "run_id": checkpoint["run_id"],
        "stage_id": stage_id,
        "stage_state": state["state"],
        "run_state": checkpoint["state"],
        "retry_count": state["retry_count"],
    }
    if "manifest" in result:
        summary["manifest"] = result["manifest"]
    return summary


def _dispatch(args) -> tuple[dict, ToolError | None]:
    if args.command == "describe":
        return _describe(args.tool), None
    root = resource_root(args.repo)
    extension_root = None
    if args.extensions is not None:
        extension_root = args.extensions.expanduser().resolve()
        if not (extension_root / "pipeline_defs").is_dir():
            raise ToolError("invalid_input", "extension directory must contain pipeline_defs/")
    if args.command == "resources":
        result = {
            "root": str(root),
            "agent_instructions": str(root / "AGENTS.md"),
            "agent_guide": str(root / "AGENT_GUIDE.md"),
            "pipeline_definitions": str(root / "pipeline_defs"),
            "skills": str(root / "skills"),
            "examples": str(root / "examples" / "agent-workflows"),
        }
        if extension_root is not None:
            result["extension_root"] = str(extension_root)
        return result, None
    if args.command == "demo":
        return {"value": run_demo(root, metrics=args.metrics)}, None
    if args.command == "validate-pipeline":
        definition, checksum, skill_root = load_pipeline(
            root, args.pipeline, extension_root=extension_root
        )
        return {"definition": definition, "digest": checksum, "skill_root": str(skill_root)}, None
    if args.command == "pipelines":
        names = {path.stem for path in (root / "pipeline_defs").glob("*.yaml")}
        if extension_root is not None:
            names.update(path.stem for path in (extension_root / "pipeline_defs").glob("*.yaml"))
        if not names:
            raise ToolError(
                "invalid_pipeline", "no definitions found; use --repo with a KITE checkout"
            )
        return {
            "pipelines": [
                {key: definition[key] for key in ("id", "version", "description")}
                for name in sorted(names)
                for definition, _, _ in [load_pipeline(root, name, extension_root=extension_root)]
            ]
        }, None

    if args.command == "file-source":
        return file_source(
            root,
            args.path,
            args.format,
            source_id=args.source_id,
            source_date=args.source_date,
            started_at=args.started_at,
            expected_checksum=args.expected_checksum,
        ), None

    if args.command == "audio-source":
        return audio_source(
            root,
            args.recording,
            args.model,
            source_id=args.source_id,
            language=args.language,
            ffmpeg=args.ffmpeg,
            whisper=args.whisper_cli,
            timeout=args.timeout,
        ), None

    if args.command == "backup":
        return Workspace.backup(args.workspace, args.destination), None

    if args.command in {"sources", "codebook"}:
        scope = {"source": args.source} if args.source is not None else {}
        conversation_id = getattr(args, "conversation_id", None)
        if conversation_id is not None:
            scope["conversation_id"] = conversation_id
        if scope:
            validate(SOURCE_SCOPE, scope, "source scope")
        with Workspace.open(args.workspace) as workspace:
            if args.command == "sources":
                return {
                    "sources": workspace.list_sources(args.source),
                    "memory_version": workspace.version(),
                }, None
            return {"xml": workspace.export_codebook(**scope, active_only=args.active_only)}, None

    supplied = _object(getattr(args, "input", None))
    with Workspace.open(args.workspace) as workspace:
        context = Context(
            root,
            workspace,
            getattr(args, "pipeline", ""),
            getattr(args, "approve", None),
            getattr(args, "allow_provider", False),
            extension_root=extension_root,
        )
        if args.command == "start":
            return start(context, supplied, args.run_id), None
        if args.command == "stage":
            result, error = advance(context, args.run_id, args.stage_id, supplied)
            if args.summary:
                result = _stage_summary(result, args.stage_id)
            return result, error
        if args.command == "runs":
            return recent_runs(workspace, args.limit), None
        if args.command == "status":
            return status(context, args.run_id), None
        if args.command == "artifact":
            return artifact(context, args.run_id, args.stage_id), None
        if args.command == "deletion-audit":
            rows = workspace.db.execute(
                "SELECT * FROM deletion_audit ORDER BY version DESC LIMIT 100"
            )
            return {"deletions": [dict(row) for row in rows]}, None
        return TOOLS["validate"].function(context), None


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        import jsonschema  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        print(
            encode(
                {
                    "ok": False,
                    "error": {
                        "code": "missing_dependency",
                        "message": "install memoket-kite[agent]",
                    },
                }
            )
        )
        return 2
    try:
        result, error = _dispatch(args)
    except ToolError as exc:
        result, error = {}, exc
    except sqlite3.Error as exc:
        code = (
            "workspace_busy"
            if getattr(exc, "sqlite_errorname", "") in ("SQLITE_BUSY", "SQLITE_LOCKED")
            else "storage_error"
        )
        result, error = {}, ToolError(code, "workspace transaction did not complete")
    except OSError:
        result, error = (
            {},
            ToolError("io_error", "cannot read input, workflow resources or workspace"),
        )
    if error:
        print(encode({"ok": False, **result, "error": {"code": error.code, "message": str(error)}}))
        return 2
    if args.command == "codebook":
        sys.stdout.buffer.write(result["xml"] + b"\n")
    elif args.command in {"audio-source", "file-source"}:
        print(encode(result))
    else:
        print(encode({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
