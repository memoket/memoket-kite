"""JSON command line for the agent orchestration contract."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from .audio import audio_source
from .captions import CAPTION_FORMATS
from .contracts import ToolError, decode_object, encode, read_utf8, validate
from .demo import render_demo, run_demo
from .ingest import file_source
from .operations import SCHEMAS, SOURCE_SCOPE, TOOLS, Context
from .resources import resource_root
from .retrieve import evidence_context
from .trace_text import render_trace
from .workflow import (
    SCRIPTED_IMPORT_STAGES,
    TRACE_PIPELINES,
    advance,
    artifact,
    discover_pipelines,
    load_pipeline,
    query_report,
    recent_runs,
    resume_ingestion,
    source_lines,
    start,
    status,
    transcript_page,
    verify_run_storage,
)
from .workspace import Workspace


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's diagnostic can quote raw arguments, including paths and tokens.
        usage = self.format_usage().strip()
        raise ToolError("invalid_arguments", f"invalid command arguments\n{usage}")


def _object(path: str | None) -> dict:
    if path is None:
        return {}

    try:
        raw = sys.stdin.read() if path == "-" else read_utf8(Path(path))
    except UnicodeError as exc:
        raise ToolError("invalid_input", "input must be valid UTF-8") from exc
    return decode_object(raw)


def _query_object(args: argparse.Namespace) -> dict:
    filters = {
        field: value
        for field in SCHEMAS["query"]["properties"]
        if (value := getattr(args, field, None)) is not None
    }
    if args.input is not None:
        if filters:
            raise ToolError(
                "invalid_arguments", f"use either --input or query filter flags\n{args.query_usage}"
            )
        return _object(args.input)
    if not filters:
        raise ToolError(
            "invalid_arguments", f"provide --input or query filter flags\n{args.query_usage}"
        )
    return {"query": filters}


def _ingest_input(args: argparse.Namespace) -> dict:
    if args.annotations is None:
        return _object(args.input)
    if args.input is None:
        raise ToolError("invalid_arguments", "--annotations requires --input")
    source = _object(args.input)
    if source.keys() != {"source"}:
        raise ToolError(
            "invalid_input", "with --annotations, --input must contain only a source field"
        )
    annotations = decode_object(read_utf8(args.annotations))
    if annotations.keys() != {"annotations"}:
        raise ToolError(
            "invalid_input", "--annotations expects an object with only an annotations field"
        )
    return {**source, **annotations}


def _source_date_arguments(parser: argparse.ArgumentParser) -> None:
    date = parser.add_mutually_exclusive_group()
    date.add_argument("--source-date", help="known conversation day in YYYY-MM-DD format")
    date.add_argument("--started-at", help="known conversation timestamp with UTC offset")


def _segment_page_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--offset", type=int, default=0, help="zero-based segment offset")
    parser.add_argument("--limit", type=int, default=50, help="segments per page (1–200)")
    parser.add_argument(
        "--byte-budget",
        type=int,
        default=32768,
        help="target JSON segment bytes (128–1048576); the first segment is kept whole",
    )


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="kite-tool", description=__doc__)
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
    demo_output = demo.add_mutually_exclusive_group()
    demo_output.add_argument(
        "--metrics", action="store_true", help="emit deterministic fixture metrics"
    )
    demo_output.add_argument("--text", action="store_true", help="show the decision and citations")
    demo.add_argument(
        "--save-workspace", type=Path, metavar="PATH", help="save the demo to a new workspace"
    )
    query = commands.add_parser("query", help="run a read-only trace without saving a checkpoint")
    query.add_argument("--pipeline", choices=TRACE_PIPELINES, default="topic-tracing")
    query.add_argument(
        "--text",
        dest="text_output",
        action="store_true",
        help="show a timeline with original quotes",
    )
    query_input = query.add_mutually_exclusive_group()
    query_input.add_argument("--input", metavar="FILE", help="JSON input file or - for stdin")
    query_input.add_argument(
        "--topic",
        dest="topics",
        action="append",
        metavar="NAME",
        help="topic name or alias, repeat for more topics",
    )
    query.add_argument("--entity", dest="entities", action="append", help="entity name or alias")
    query.add_argument(
        "--event", dest="events", action="append", help="event label, repeat for more events"
    )
    query.add_argument("--speaker", dest="speakers", action="append", help="speaker ID")
    query.add_argument("--project", dest="projects", action="append", help="source project")
    query.add_argument(
        "--contains",
        dest="text",
        metavar="PHRASE",
        help="case-insensitive phrase in a fact or its cited lines",
    )
    query.add_argument("--state-key", help="task or decision state key")
    query.add_argument(
        "--follow-states",
        action="store_true",
        default=None,
        help="include history for state keys found by entity, event, speaker, or phrase",
    )
    query.add_argument("--source", help="source namespace, for example text or memoket")
    query.add_argument("--conversation-id", help="original conversation ID, requires --source")
    query.add_argument("--start-date", help="inclusive YYYY-MM-DD start date")
    query.add_argument("--end-date", help="inclusive YYYY-MM-DD end date")
    query.add_argument(
        "--time-axis", choices=("source", "event"), help="date axis, defaults to source"
    )
    query.add_argument("--limit", type=int, help="maximum facts per page (1–1000)")
    query.add_argument(
        "--evidence-byte-budget", type=int, help="target bytes of cited evidence per page"
    )
    query.add_argument("--cursor", help="next_cursor from the previous page with the same filters")
    query.set_defaults(query_usage=query.format_usage().strip())
    evidence = commands.add_parser(
        "evidence", help="read an original cited line and nearby context"
    )
    evidence.add_argument("source_line_id")
    evidence.add_argument("--context", type=int, default=0, help="lines on either side (0–20)")
    evidence.add_argument(
        "--byte-budget",
        type=int,
        help="target JSON evidence bytes (128–1048576); the cited line is kept whole",
    )
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
    source.add_argument(
        "--format", choices=("text", "memoket", "conversation", *CAPTION_FORMATS), required=True
    )
    source.add_argument("--source-id", help="stable identity for a text or caption file")
    source.add_argument(
        "--split-lines",
        action="store_true",
        help="cite nonblank text lines by their file line number",
    )
    _source_date_arguments(source)
    source.add_argument("--expected-checksum", help="current source checksum for a revision")
    source.add_argument(
        "--page",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="next saved Memoket get_transcripts page, repeat in order",
    )
    source.add_argument(
        "--recording-metadata",
        type=Path,
        metavar="FILE",
        help="saved Memoket get_conversations response or recording item",
    )
    audio = commands.add_parser(
        "audio-source", help="transcribe a local recording into a memory-ingestion input"
    )
    audio.add_argument("recording", type=Path)
    audio.add_argument("--source-id", required=True, help="stable recording identifier")
    audio.add_argument("--model", type=Path, required=True, help="local whisper.cpp model")
    _source_date_arguments(audio)
    audio.add_argument("--expected-checksum", help="current source checksum for a revision")
    audio.add_argument("--language", default="auto", help="Whisper language code or auto")
    audio.add_argument("--whisper-cli", default="whisper-cli", help="trusted executable")
    audio.add_argument("--ffmpeg", default="ffmpeg", help="trusted executable")
    audio.add_argument("--timeout", type=int, default=1800, help="seconds allowed per process")
    ingest = commands.add_parser("ingest", help="import a source with prepared annotations")
    ingest.add_argument("--run-id", help="stable identifier for the ingestion run")
    ingest.add_argument(
        "--resume-existing", action="store_true", help="reuse --run-id only when the input matches"
    )
    ingest.add_argument(
        "--update", action="store_true", help="apply a source revision or add annotations"
    )
    ingest_mode = ingest.add_mutually_exclusive_group(required=True)
    ingest_mode.add_argument("--input", metavar="FILE", help="JSON input file or - for stdin")
    ingest_mode.add_argument("--resume", metavar="RUN_ID", help="resume an existing local import")
    ingest.add_argument(
        "--annotations",
        type=Path,
        metavar="FILE",
        help="annotate stage JSON for a source-only input",
    )
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
    runs.add_argument("--pipeline", help="only show runs for this pipeline ID")
    runs.add_argument(
        "--unfinished", action="store_true", help="hide completed and invalidated runs"
    )
    runs.add_argument(
        "--resumable",
        action="store_true",
        help="show runs with valid next stages and their requirements",
    )
    runs.add_argument("--before", metavar="RUN_ID", help="show runs created before this run")
    for name in ("status", "artifact"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        if name == "artifact":
            command.add_argument("stage_id")
    segments = commands.add_parser("segments", help="read a page of a normalized transcript")
    segments.add_argument("run_id")
    segments.add_argument("stage_id")
    _segment_page_arguments(segments)
    integrity = commands.add_parser(
        "integrity", help="verify the current Codebook against its source records"
    )
    integrity.add_argument(
        "--include-workflows",
        action="store_true",
        help="check SQLite, checkpoints and artifacts in an existing workspace without writing",
    )
    backup = commands.add_parser("backup", help="write a verified private copy to a new directory")
    backup.add_argument("destination", type=Path)
    backup.add_argument(
        "--require-deletions-from",
        type=Path,
        metavar="WORKSPACE",
        help="reject recovery copies missing deletions recorded in this workspace",
    )
    commands.add_parser("deletion-audit", help="show recent deletion receipts without source text")
    deletion_impact = commands.add_parser(
        "deletion-impact", help="review affected conversations and counts from a deletion preview"
    )
    deletion_impact.add_argument("run_id")
    sources = commands.add_parser(
        "sources", help="list conversation identities and current checksums"
    )
    sources.add_argument("--source", help="select a source namespace")
    sources.add_argument("--limit", type=int, help="number of conversations per page (1–1000)")
    sources.add_argument("--cursor", help="continue with the previous page's next_cursor")
    history = commands.add_parser(
        "source-history", help="list one conversation's stored revisions without transcript text"
    )
    history.add_argument("source")
    history.add_argument("conversation_id")
    lines = commands.add_parser(
        "source-lines", help="read original lines from a stored conversation"
    )
    lines.add_argument("source")
    lines.add_argument("conversation_id")
    lines.add_argument("--revision", metavar="CHECKSUM", help="retained revision, required to page")
    _segment_page_arguments(lines)
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


def _replay_ingest(context: Context, checkpoint: dict) -> tuple[dict, ToolError | None]:
    run_id = checkpoint["run_id"]
    output = {"run_id": run_id}
    for stage_id, _ in SCRIPTED_IMPORT_STAGES[checkpoint["pipeline_id"]]:
        try:
            value = artifact(context, run_id, stage_id)["value"]
        except ToolError as exc:
            return {**output, "stage_id": stage_id}, exc
        if stage_id == "commit":
            output["receipt"] = value
        elif stage_id == "verify":
            output["integrity"] = value
    return output, None


def _ingest(
    root: Path,
    directory: Path,
    supplied: dict,
    run_id: str | None,
    extension_root: Path | None,
    *,
    resume_id: str | None = None,
    update: bool = False,
    resume_existing: bool = False,
) -> tuple[dict, ToolError | None]:
    if resume_existing and (run_id is None or resume_id is not None):
        raise ToolError("invalid_input", "--resume-existing requires --run-id and --input")
    if resume_id is not None and run_id is not None:
        raise ToolError("invalid_input", "--run-id is only valid when starting a new ingestion")
    if resume_id is not None and update:
        raise ToolError("invalid_input", "--resume uses the pipeline saved in the checkpoint")
    if resume_id is None and supplied.keys() != {"source", "annotations"}:
        raise ToolError("invalid_input", "ingest requires both source and annotations")
    pipeline = "memory-update" if update else "memory-ingestion"
    checkpoint_workspace = (
        Workspace.open(directory) if resume_id is None else Workspace.inspect(directory)
    )
    with checkpoint_workspace as workspace:
        context = Context(root, workspace, pipeline, extension_root=extension_root)
        if resume_id is None:
            checkpoint = start(
                context,
                supplied,
                run_id,
                expected_stages=SCRIPTED_IMPORT_STAGES[pipeline],
                resume_existing=resume_existing,
            )
        else:
            checkpoint = resume_ingestion(context, resume_id)
        if checkpoint["state"] == "completed":
            return _replay_ingest(context, checkpoint)
    run_id = checkpoint["run_id"]
    pipeline = checkpoint["pipeline_id"]
    output = {"run_id": run_id}
    for stage_id, _ in SCRIPTED_IMPORT_STAGES[pipeline]:
        try:
            # Each successful stage is durable before the next stage begins.
            with Workspace.open(directory) as workspace:
                context = Context(root, workspace, pipeline, extension_root=extension_root)
                result, error = advance(context, run_id, stage_id, {})
        except ToolError as exc:
            return {**output, "stage_id": stage_id}, exc
        if error:
            return {**output, **_stage_summary(result, stage_id)}, error
        if stage_id == "commit":
            output["receipt"] = result["value"]
    return {**output, "integrity": result["value"]}, None


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
        return {
            "value": run_demo(root, metrics=args.metrics, save_workspace=args.save_workspace)
        }, None
    if args.command == "validate-pipeline":
        definition, checksum, skill_root = load_pipeline(
            root, args.pipeline, extension_root=extension_root
        )
        return {"definition": definition, "digest": checksum, "skill_root": str(skill_root)}, None
    if args.command == "pipelines":
        return discover_pipelines(root, extension_root=extension_root), None

    if args.command == "file-source":
        return file_source(
            root,
            args.path,
            args.format,
            source_id=args.source_id,
            source_date=args.source_date,
            started_at=args.started_at,
            expected_checksum=args.expected_checksum,
            split_lines=args.split_lines,
            pages=args.page,
            recording_metadata=args.recording_metadata,
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
            source_date=args.source_date,
            started_at=args.started_at,
            expected_checksum=args.expected_checksum,
        ), None

    if args.command == "backup":
        return Workspace.backup(
            args.workspace,
            args.destination,
            verify_runs=lambda copy: verify_run_storage(copy, root),
            require_deletions_from=args.require_deletions_from,
        ), None

    if args.command in {"source-history", "source-lines"}:
        scope = {"source": args.source, "conversation_id": args.conversation_id}
        validate(SOURCE_SCOPE, scope, "source scope")
        with Workspace.open(args.workspace, read_only=True) as workspace:
            if args.command == "source-history":
                return workspace.source_history(**scope), None
            return source_lines(
                workspace,
                **scope,
                checksum=args.revision,
                offset=args.offset,
                limit=args.limit,
                byte_budget=args.byte_budget,
            ), None

    if args.command == "integrity" and args.include_workflows:
        with Workspace.open(args.workspace, read_only=True) as workspace:
            workspace.verify_sqlite()
            index = TOOLS["validate"].function(Context(root, workspace, ""))
            return {**index, **verify_run_storage(workspace, root)}, None

    if args.command in {"sources", "codebook"}:
        scope = {"source": args.source} if args.source is not None else {}
        conversation_id = getattr(args, "conversation_id", None)
        if conversation_id is not None:
            scope["conversation_id"] = conversation_id
        if scope:
            validate(SOURCE_SCOPE, scope, "source scope")
        with Workspace.inspect(args.workspace, initialize=True) as workspace:
            if args.command == "sources":
                return workspace.source_inventory(
                    args.source, limit=args.limit, cursor=args.cursor
                ), None
            return {"xml": workspace.export_codebook(**scope, active_only=args.active_only)}, None

    if args.command == "ingest":
        return _ingest(
            root,
            args.workspace,
            _ingest_input(args),
            args.run_id,
            extension_root,
            resume_id=args.resume,
            update=args.update,
            resume_existing=args.resume_existing,
        )
    supplied = (
        _query_object(args) if args.command == "query" else _object(getattr(args, "input", None))
    )
    if args.command in {"query", "evidence", "deletion-impact"}:
        with Workspace.open(args.workspace, read_only=True) as workspace:
            if args.command == "deletion-impact":
                context = Context(root, workspace, "", extension_root=extension_root)
                preview = artifact(context, args.run_id, "preview")
                if preview["manifest"]["kind"] != "deletion_plan":
                    raise ToolError("invalid_input", "select a deletion preview")
                return {
                    "value": workspace.deletion_impact(preview["value"], run_id=args.run_id)
                }, None
            if args.command == "evidence":
                return {
                    "value": evidence_context(
                        workspace, args.source_line_id, args.context, byte_budget=args.byte_budget
                    )
                }, None
            return {"value": query_report(Context(root, workspace, args.pipeline), supplied)}, None
    open_workspace = (
        Workspace.inspect(args.workspace, initialize=True)
        if args.command in {"runs", "status", "artifact", "segments"}
        else Workspace.open(args.workspace)
    )
    with open_workspace as workspace:
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
            return recent_runs(
                context,
                args.limit,
                pipeline=args.pipeline,
                unfinished=args.unfinished,
                resumable=args.resumable,
                before=args.before,
            ), None
        if args.command == "status":
            return status(context, args.run_id), None
        if args.command == "artifact":
            return artifact(context, args.run_id, args.stage_id), None
        if args.command == "segments":
            return transcript_page(
                context,
                args.run_id,
                args.stage_id,
                offset=args.offset,
                limit=args.limit,
                byte_budget=args.byte_budget,
            ), None
        if args.command == "deletion-audit":
            rows = workspace.db.execute(
                "SELECT * FROM deletion_audit ORDER BY version DESC LIMIT 100"
            )
            return {"deletions": [dict(row) for row in rows]}, None
        return TOOLS["validate"].function(context), None


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
        try:
            import jsonschema  # noqa: F401
            import yaml  # noqa: F401
        except ImportError as exc:
            raise ToolError("missing_dependency", "install memoket-kite[agent]") from exc
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
    elif args.command == "demo" and args.text:
        print(render_demo(result["value"]))
    elif args.command == "query" and args.text_output:
        print(render_trace(result["value"]))
    elif args.command in {"audio-source", "file-source"}:
        print(encode(result))
    else:
        print(encode({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
