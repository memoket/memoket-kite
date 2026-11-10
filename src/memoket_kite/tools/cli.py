"""JSON command line for the agent orchestration contract."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .contracts import ToolError, encode
from .operations import SCHEMAS, TOOLS, Context
from .workflow import advance, artifact, load_pipeline, start, status
from .workspace import Workspace


def _object(path: str | None) -> dict:
    if path is None:
        return {}

    def pairs(items):
        result = dict(items)
        if len(items) != len(result):
            raise ToolError("invalid_input", "duplicate JSON input keys")
        return result

    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    try:
        result = json.loads(raw, object_pairs_hook=pairs)
        encode(result)
    except (ValueError, RecursionError) as exc:
        raise ToolError("invalid_input", "input must be finite, valid JSON") from exc
    if not isinstance(result, dict):
        raise ToolError("invalid_input", "input must be a JSON object")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kite-tool", description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="trusted KITE workflow checkout"
    )
    default_dir = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")) / "kite"
    parser.add_argument(
        "--workspace", type=Path, default=Path(os.environ.get("KITE_DATA_DIR") or default_dir)
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pipelines", help="list validated pipelines")
    validate = commands.add_parser(
        "validate-pipeline", help="validate and show a pipeline definition"
    )
    validate.add_argument("pipeline")
    describe = commands.add_parser(
        "describe", help="show registered tools and their JSON contracts"
    )
    describe.add_argument("tool", nargs="?", choices=sorted(TOOLS))
    begin = commands.add_parser("start", help="create a checkpoint for a pipeline run")
    begin.add_argument("pipeline")
    begin.add_argument("--run-id")
    begin.add_argument("--input", metavar="FILE", help="JSON input file or - for stdin")
    stage = commands.add_parser("stage", help="advance or retry a single stage")
    stage.add_argument("run_id")
    stage.add_argument("stage_id")
    stage.add_argument("--input", metavar="FILE", help="additional stage input file or - for stdin")
    stage.add_argument("--approve", metavar="TOKEN", help="user-approved deletion preview token")
    stage.add_argument(
        "--allow-provider", action="store_true", help="allow the extractor to send source data"
    )
    for name in ("status", "artifact"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        if name == "artifact":
            command.add_argument("stage_id")
    commands.add_parser("integrity", help="verify the current Codebook against its source records")
    commands.add_parser("deletion-audit", help="show recent deletion receipts without source text")
    commands.add_parser("codebook", help="write the current XML snapshot to stdout")
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


def _dispatch(args) -> tuple[dict, ToolError | None]:
    root = args.repo.resolve()
    if args.command == "describe":
        return _describe(args.tool), None
    if args.command == "validate-pipeline":
        definition, checksum = load_pipeline(root, args.pipeline)
        return {"definition": definition, "digest": checksum}, None
    if args.command == "pipelines":
        paths = sorted((root / "pipeline_defs").glob("*.yaml"))
        if not paths:
            raise ToolError(
                "invalid_pipeline", "no definitions found; use --repo with a KITE checkout"
            )
        return {
            "pipelines": [
                {key: definition[key] for key in ("id", "version", "description")}
                for path in paths
                for definition, _ in [load_pipeline(root, path.stem)]
            ]
        }, None

    supplied = _object(getattr(args, "input", None))
    with Workspace.open(args.workspace) as workspace:
        context = Context(
            root,
            workspace,
            getattr(args, "pipeline", ""),
            getattr(args, "approve", None),
            getattr(args, "allow_provider", False),
        )
        if args.command == "start":
            return start(context, supplied, args.run_id), None
        if args.command == "stage":
            return advance(context, args.run_id, args.stage_id, supplied)
        if args.command == "status":
            return status(workspace, args.run_id), None
        if args.command == "artifact":
            return artifact(context, args.run_id, args.stage_id), None
        if args.command == "deletion-audit":
            rows = workspace.db.execute(
                "SELECT * FROM deletion_audit ORDER BY version DESC LIMIT 100"
            )
            return {"deletions": [dict(row) for row in rows]}, None
        if args.command == "codebook":
            return {"xml": workspace.index().xml}, None
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
    else:
        print(encode({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
