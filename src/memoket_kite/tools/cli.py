"""JSON command line for the agent orchestration contract."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from .activity import FOLLOW_STAGES
from .audio import audio_source
from .captions import CAPTION_FORMATS
from .context import context_pack, question_pack, render_context
from .contracts import (
    HASH,
    TEXT,
    ToolError,
    decode_object,
    digest,
    encode,
    object_schema,
    read_utf8,
    validate,
)
from .demo import render_decision_story, render_demo, run_demo
from .ingest import file_source, prepare_source
from .memoket_source import memoket_source
from .operations import SCHEMAS, SOURCE_SCOPE, TOOLS, Context
from .resources import resource_root
from .retrieve import (
    compare_revisions,
    evidence_context,
    render_revision_comparison,
    segment_context,
    topic_inventory,
)
from .source_batch import commit_batch, prepare_batch, read_transcript_batch
from .source_diff import source_changes
from .trace_text import render_status, render_trace
from .viewer import view_memory
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
    save_context_search,
    save_source_indexing,
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
    source = _object(args.input)
    if args.resume is None and source.keys() != {"source"}:
        raise ToolError(
            "invalid_input", "with --annotations, --input must contain only a source field"
        )
    annotations = decode_object(read_utf8(args.annotations))
    if annotations.keys() != {"annotations"}:
        raise ToolError(
            "invalid_input", "--annotations expects an object with only an annotations field"
        )
    return {**source, **annotations}


def _source_metadata_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", help="project label for later trace queries")
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
    catalog = commands.add_parser(
        "resources", help="show paths to agent instructions and workflows"
    )
    catalog.add_argument(
        "--project-instructions",
        action="store_true",
        help="print a Markdown section for another project's AGENTS.md",
    )
    demo = commands.add_parser("demo", help="run the offline text and Memoket quickstart")
    demo_output = demo.add_mutually_exclusive_group()
    demo_output.add_argument(
        "--metrics", action="store_true", help="emit deterministic fixture metrics"
    )
    demo_output.add_argument("--text", action="store_true", help="show the decision and citations")
    demo_output.add_argument(
        "--decision-story", action="store_true", help="show how memory follows a changed decision"
    )
    demo.add_argument(
        "--save-workspace", type=Path, metavar="PATH", help="save the demo to a new workspace"
    )
    topics = commands.add_parser("topics", help="list queryable topic codes and aliases")
    topics.add_argument("--contains", metavar="PHRASE", help="filter topic codes and aliases")
    topics.add_argument("--limit", type=int, default=50, help="topics per page (1–1000)")
    topics.add_argument("--cursor", help="continue with the previous page's next_cursor")
    viewer = commands.add_parser("view", help="open the workspace memory in a local browser")
    viewer.add_argument(
        "--no-open", action="store_true", help="print the local URL without opening it"
    )
    view_options = viewer.add_mutually_exclusive_group()
    view_options.add_argument(
        "--term", action="append", help="open an original-evidence search in the graph"
    )
    view_options.add_argument("--compare", type=Path, help="compare saved graph snapshots")
    view_options.add_argument("--run", help="open the evidence from a saved search")
    view_options.add_argument("--follow", metavar="RUN_ID", help="follow an import or search run")
    source_index = commands.add_parser("index-sources", help="index original sources without facts")
    index_input = source_index.add_mutually_exclusive_group()
    index_input.add_argument("--input", metavar="FILE", help="source batch JSON or - for stdin")
    index_input.add_argument(
        "--transcripts", type=Path, help="directory of timestamped TXT transcripts"
    )
    source_index.add_argument(
        "--recording-metadata",
        type=Path,
        help="directory of same-ID JSON recording times for TXT import",
    )
    source_index.add_argument(
        "--save",
        nargs="?",
        const="",
        metavar="RUN_ID",
        help="save the batch preview, commit and verification as a workflow",
    )
    context = commands.add_parser("context", help="pack cited original passages for an agent task")
    context_input = context.add_mutually_exclusive_group(required=True)
    context_input.add_argument("--term", action="append", help="search phrase, repeatable")
    context_input.add_argument("--question", help="compile a task into search phrases locally")
    context.add_argument("--compare-with", help="search a second question within the same budget")
    context.add_argument(
        "--from-version",
        type=int,
        help="compare the same question with an earlier workspace version",
    )
    context.add_argument("--compare-start-date", help="second question's earliest recording date")
    context.add_argument("--compare-end-date", help="second question's latest recording date")
    context.add_argument(
        "--save", nargs="?", const="", metavar="RUN_ID", help="save the compile and search results"
    )
    context.add_argument("--source", help="restrict search to one source namespace")
    context.add_argument(
        "--after", metavar="RUN_ID", help="skip original lines from a saved search"
    )
    context.add_argument("--conversation-id", help="search within an original conversation ID")
    context.add_argument("--start-date", help="earliest source date, inclusive")
    context.add_argument("--end-date", help="latest source date, inclusive")
    context.add_argument("--latest", action="store_true", help="give newer dated sources priority")
    context.add_argument(
        "--matching-lines", action="store_true", help="return only original lines matching a phrase"
    )
    context.add_argument(
        "--explain", action="store_true", help="include packing order in JSON and saved searches"
    )
    history_scope = context.add_mutually_exclusive_group()
    history_scope.add_argument("--at-version", type=int, help="search a tracked workspace snapshot")
    history_scope.add_argument(
        "--known-at", help="search what the workspace knew at an ISO timestamp with offset"
    )
    context.add_argument("--limit", type=int, help="maximum matched spans")
    context.add_argument("--max-bytes", type=int, help="original quote byte budget")
    context.add_argument(
        "--compact", action="store_true", help="trim outer neighboring lines in question evidence"
    )
    context.add_argument("--neighbors", type=int, help="adjacent turns on either side (default 1)")
    context.add_argument(
        "--text", action="store_true", help="compact original quotes and citation IDs"
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
    evidence_id = evidence.add_mutually_exclusive_group(required=True)
    evidence_id.add_argument(
        "source_line_id", nargs="?", help="citation such as 184#L4 or a revision-bound line ID"
    )
    evidence_id.add_argument("--segment-id", help="original segment ID in a conversation")
    evidence.add_argument("--source", help="source namespace for a short citation or --segment-id")
    evidence.add_argument("--conversation-id", help="original conversation ID for --segment-id")
    evidence.add_argument("--revision", metavar="CHECKSUM", help="select a retained revision")
    evidence.add_argument("--context", type=int, default=0, help="lines on either side (0–20)")
    evidence.add_argument("--text", action="store_true", help="compact quotes and citation IDs")
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
        "--format",
        choices=("text", "transcript-text", "memoket", "conversation", *CAPTION_FORMATS),
        required=True,
    )
    source.add_argument("--source-id", help="stable identity for a text or caption file")
    source.add_argument(
        "--split-lines",
        action="store_true",
        help="cite nonblank text lines by their file line number",
    )
    _source_metadata_arguments(source)
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
    memoket = commands.add_parser(
        "memoket-source", help="prepare ingestion input for one authorized Memoket recording"
    )
    memoket.add_argument("conversation_id", help="ID of the recording authorized by the user")
    memoket.add_argument(
        "--include-metadata",
        action="store_true",
        help="fetch the recording title, start time and language (requires summaries:read)",
    )
    memoket.add_argument("--project", help="project label for later trace queries")
    memoket.add_argument("--expected-checksum", help="current source checksum for a revision")
    audio = commands.add_parser(
        "audio-source", help="transcribe a local recording into a memory-ingestion input"
    )
    audio.add_argument("recording", type=Path)
    audio.add_argument("--source-id", required=True, help="stable recording identifier")
    audio.add_argument("--model", type=Path, required=True, help="local whisper.cpp model")
    _source_metadata_arguments(audio)
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
        help="annotate stage JSON for a source-only input or a resumed run",
    )
    begin = commands.add_parser("start", help="create a checkpoint for a pipeline run")
    begin.add_argument("pipeline")
    begin.add_argument("--run-id")
    begin.add_argument("--input", metavar="FILE", help="JSON input file or - for stdin")
    begin.add_argument(
        "--visualize", action="store_true", help="open the live graph for the workflow"
    )
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
        else:
            command.add_argument(
                "--text",
                dest="text_output",
                action="store_true",
                help="show checkpoint stages and recovery requirements for a terminal",
            )
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
    sources.add_argument("--project", help="select current revisions with this exact project label")
    sources.add_argument("--limit", type=int, help="number of conversations per page (1–1000)")
    sources.add_argument("--cursor", help="continue with the previous page's next_cursor")
    source_check = commands.add_parser(
        "source-check", help="compare a prepared source with committed revisions before annotating"
    )
    source_check.add_argument(
        "--input", required=True, metavar="FILE", help="source-only JSON or - for stdin"
    )
    source_check.add_argument("--changes", action="store_true", help="show changed segment IDs")
    source_check.add_argument("--limit", type=int, help="changed segments per page (1–200)")
    source_check.add_argument("--cursor", help="continue the source comparison")
    history = commands.add_parser(
        "source-history", help="list one conversation's stored revisions without transcript text"
    )
    history.add_argument("source")
    history.add_argument("conversation_id")
    comparison = commands.add_parser(
        "source-compare", help="compare two stored versions of one source"
    )
    comparison.add_argument(
        "before", metavar="CHECKSUM", help="retained source revision to compare"
    )
    comparison.add_argument(
        "--after", metavar="CHECKSUM", help="compare to this revision instead of the current one"
    )
    comparison.add_argument("--limit", type=int, help="changed segments per page")
    comparison.add_argument("--max-bytes", type=int, help="original quote byte budget")
    comparison.add_argument("--cursor", help="continue with the previous page's next_cursor")
    comparison.add_argument(
        "--text", action="store_true", help="display only the changed quotes and references"
    )
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
            checkpoint = resume_ingestion(context, resume_id, supplied)
        if checkpoint["state"] == "completed":
            return _replay_ingest(context, checkpoint)
    run_id = checkpoint["run_id"]
    pipeline = checkpoint["pipeline_id"]
    output = {"run_id": run_id}
    for stage_id, _ in SCRIPTED_IMPORT_STAGES[pipeline]:
        extra = supplied if resume_id is not None and stage_id == "annotate" else {}
        try:
            # Each successful stage is durable before the next stage begins.
            with Workspace.open(directory) as workspace:
                context = Context(root, workspace, pipeline, extension_root=extension_root)
                result, error = advance(context, run_id, stage_id, extra)
        except ToolError as exc:
            return {**output, "stage_id": stage_id}, exc
        if error:
            return {**output, **_stage_summary(result, stage_id)}, error
        if stage_id == "commit":
            output["receipt"] = result["value"]
    return {**output, "integrity": result["value"]}, None


def _read_evidence(args: argparse.Namespace, workspace: Workspace) -> dict:
    reference = args.source_line_id
    if reference is not None and "#" not in reference:
        if any(value is not None for value in (args.source, args.conversation_id, args.revision)):
            raise ToolError("invalid_input", "source and revision options require --segment-id")
        return evidence_context(workspace, reference, args.context, byte_budget=args.byte_budget)
    source, conversation, segment = args.source, args.conversation_id, args.segment_id
    if reference is not None:
        if conversation is not None:
            raise ToolError(
                "invalid_input", "the short citation already includes a conversation ID"
            )
        if reference.startswith("[") and reference.endswith("]"):
            reference = reference[1:-1]
        conversation, _, segment = reference.rpartition("#")
        if "/" in conversation:
            namespace, conversation = conversation.split("/", 1)
            if source is not None and source != namespace:
                raise ToolError("invalid_input", "the citation and --source namespaces differ")
            source = namespace
    elif source is None or conversation is None:
        raise ToolError("invalid_input", "--segment-id requires --source and --conversation-id")
    validate(TEXT, conversation, "conversation ID")
    validate(TEXT, segment, "segment ID")
    if source is not None:
        validate(SOURCE_SCOPE, {"source": source, "conversation_id": conversation}, "source scope")
    if args.revision is not None:
        validate(HASH, args.revision, "revision checksum")
    return segment_context(
        workspace,
        source=source,
        conversation_id=conversation,
        segment_id=segment,
        checksum=args.revision,
        radius=args.context,
        byte_budget=args.byte_budget,
    )


def _check_source(args: argparse.Namespace, root: Path) -> dict:
    if not args.changes and (args.limit is not None or args.cursor is not None):
        raise ToolError("invalid_input", "source comparison paging requires --changes")
    limit = args.limit if args.limit is not None else 50
    supplied = _object(args.input)
    validate(object_schema({"source": SCHEMAS["source"]}), supplied, "source check input")
    source, conversation = prepare_source(root, supplied["source"])
    checksum = digest(conversation)
    result = {
        "source": conversation["source"],
        "conversation_id": conversation["conversation_id"],
        "checksum": checksum,
    }
    try:
        with Workspace.open(args.workspace, read_only=True) as workspace:
            result.update(
                workspace.check_source(
                    conversation,
                    checksum,
                    changes_limit=limit if args.changes else None,
                    cursor=args.cursor,
                )
            )
    except ToolError as exc:
        if exc.code != "not_found":
            raise
        directory = args.workspace.expanduser()
        if (directory.exists() and not directory.is_dir()) or (directory / "kite.sqlite3").exists():
            raise
        result.update(state="new", current_checksum=None, memory_version=None)
        if args.changes:
            result["changes"] = source_changes(
                conversation,
                None,
                snapshot=(None, 0),
                checksum=checksum,
                limit=limit,
                cursor=args.cursor,
            )
    if "expected_checksum" in source:
        result["expected_checksum_matches"] = (
            source["expected_checksum"] == result["current_checksum"]
        )
    return result


def _dispatch(args) -> tuple[dict, ToolError | None]:
    if args.command == "view":
        return view_memory(
            args.workspace,
            open_browser=not args.no_open,
            terms=args.term,
            comparison=args.compare,
            run_id=args.run,
            follow=args.follow,
        ), None
    if args.command == "describe":
        return _describe(args.tool), None
    root = resource_root(args.repo)
    extension_root = None
    if args.extensions is not None:
        extension_root = args.extensions.expanduser().resolve()
        if not (extension_root / "pipeline_defs").is_dir():
            raise ToolError("invalid_input", "extension directory must contain pipeline_defs/")
    if args.command == "resources":
        examples = root / "examples" / "agent-workflows"
        if args.project_instructions:
            return {"markdown": read_utf8(examples / "project-instructions.md")}, None
        result = {
            "root": str(root),
            "agent_instructions": str(root / "AGENTS.md"),
            "agent_guide": str(root / "AGENT_GUIDE.md"),
            "pipeline_definitions": str(root / "pipeline_defs"),
            "skills": str(root / "skills"),
            "examples": str(examples),
        }
        if extension_root is not None:
            result["extension_root"] = str(extension_root)
        return result, None
    if args.command == "demo":
        return {
            "value": run_demo(
                root,
                metrics=args.metrics,
                decision_story=args.decision_story,
                save_workspace=args.save_workspace,
            )
        }, None
    if args.command == "validate-pipeline":
        definition, checksum, skill_root = load_pipeline(
            root, args.pipeline, extension_root=extension_root
        )
        return {"definition": definition, "digest": checksum, "skill_root": str(skill_root)}, None
    if args.command == "pipelines":
        return discover_pipelines(root, extension_root=extension_root), None

    if args.command == "index-sources":
        metadata = args.recording_metadata
        if metadata is not None and args.transcripts is None:
            raise ToolError(
                "invalid_input", "recording metadata needs a directory and --transcripts"
            )
        if args.save is not None:
            if args.transcripts is None and (args.input is None or args.input == "-"):
                raise ToolError("invalid_input", "--save needs a transcript directory or JSON file")
            if args.transcripts is not None:
                selection = {"transcripts": str(args.transcripts.expanduser().absolute())}
                if metadata is not None:
                    selection["recording_metadata"] = str(metadata.expanduser().absolute())
            else:
                selection = {"batch_file": str(Path(args.input).expanduser().absolute())}
            with Workspace.open(args.workspace) as workspace:
                context = Context(root, workspace, "source-indexing", extension_root=extension_root)
                return save_source_indexing(context, selection, args.save or None)
        payload = _object(args.input) if args.input is not None else {"sources": []}
        legacy = {}
        if args.transcripts is not None:
            payload, legacy = read_transcript_batch(args.transcripts, metadata)
        with Workspace.open(args.workspace) as workspace:
            context = Context(root, workspace, "")
            records, repairs = prepare_batch(
                context,
                payload,
                backfill_recording_time=metadata is not None,
                legacy_transcripts=legacy,
            )
            return commit_batch(context, records, repairs), None

    if args.command == "context":
        if args.compact and args.question is None:
            raise ToolError("invalid_input", "compact context requires a question")
        if args.question is None and (
            args.compare_with is not None or args.from_version is not None
        ):
            raise ToolError("invalid_input", "a comparison requires --question")
        if args.compare_with is None and (
            args.compare_start_date is not None or args.compare_end_date is not None
        ):
            raise ToolError("invalid_input", "comparison recording dates require --compare-with")
        plan = {}
        if args.latest:
            plan["latest"] = True
        if args.matching_lines:
            plan["matching_lines"] = True
        if args.explain:
            plan["explain"] = True
        neighbors = args.neighbors if args.neighbors is not None else int(not args.matching_lines)
        plan.update(
            {
                key: value
                for key in (
                    "source",
                    "conversation_id",
                    "start_date",
                    "end_date",
                    "at_version",
                    "known_at",
                )
                if (value := getattr(args, key)) is not None
            }
        )
        if args.question is not None or args.save is not None or args.after is not None:
            request = {
                **plan,
                "neighbors": neighbors,
            }
            if args.limit is not None:
                request["limit"] = args.limit
            if args.after is not None:
                request["after"] = args.after
            if args.question is None:
                request["terms"] = args.term
            else:
                request["question"] = args.question
                if args.compact:
                    request["compact"] = True
            if args.max_bytes is not None:
                request["max_bytes"] = args.max_bytes
            if args.from_version is not None:
                request["from_version"] = args.from_version
            if args.compare_with is not None:
                request["compare_with"] = args.compare_with
                for key in ("compare_start_date", "compare_end_date"):
                    if (value := getattr(args, key)) is not None:
                        request[key] = value
            if args.save is not None:
                with Workspace.open(args.workspace) as workspace:
                    context = Context(
                        root, workspace, "context-search", extension_root=extension_root
                    )
                    return save_context_search(context, request, args.save or None)
            with Workspace.open(args.workspace, read_only=True) as workspace:
                return question_pack(workspace, request), None
        with Workspace.open(args.workspace, read_only=True) as workspace:
            return context_pack(
                workspace,
                {**plan, "terms": args.term},
                limit=args.limit if args.limit is not None else 8,
                max_bytes=args.max_bytes if args.max_bytes is not None else 6000,
                neighbors=neighbors,
            ), None

    if args.command == "topics":
        with Workspace.open(args.workspace, read_only=True) as workspace:
            return topic_inventory(
                workspace, contains=args.contains, limit=args.limit, cursor=args.cursor
            ), None

    if args.command == "file-source":
        return file_source(
            root,
            args.path,
            args.format,
            source_id=args.source_id,
            source_date=args.source_date,
            started_at=args.started_at,
            project=args.project,
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
            project=args.project,
            expected_checksum=args.expected_checksum,
        ), None

    if args.command == "memoket-source":
        return memoket_source(
            root,
            args.conversation_id,
            include_metadata=args.include_metadata,
            project=args.project,
            expected_checksum=args.expected_checksum,
        ), None

    if args.command == "source-check":
        return _check_source(args, root), None

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

    if args.command == "source-compare":
        request = {
            name: value
            for name in ("before", "after", "limit", "max_bytes", "cursor")
            if (value := getattr(args, name)) is not None
        }
        with Workspace.open(args.workspace, read_only=True) as workspace:
            return compare_revisions(workspace, request), None

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
                    args.source, project=args.project, limit=args.limit, cursor=args.cursor
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
    if args.command == "start":
        if args.visualize and args.pipeline not in FOLLOW_STAGES:
            raise ToolError("invalid_input", "this workflow does not support --visualize")
        if args.visualize and args.pipeline == "context-search":
            request = supplied.get("request")
            if isinstance(request, dict):
                request.setdefault("explain", True)
        with Workspace.open(args.workspace) as workspace:
            context = Context(root, workspace, args.pipeline, extension_root=extension_root)
            checkpoint = start(context, supplied, args.run_id)
        if not args.visualize:
            return checkpoint, None
        try:
            viewer = view_memory(args.workspace, open_browser=True, follow=checkpoint["run_id"])
        except (OSError, ToolError) as error:
            viewer = {"error": str(error)}
        return {**checkpoint, "viewer": viewer}, None
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
                return {"value": _read_evidence(args, workspace)}, None
            report = query_report(Context(root, workspace, args.pipeline), supplied)
        if args.text_output:
            return {"text": render_trace(report, supplied["query"])}, None
        return {"value": report}, None
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
    elif args.command == "resources" and args.project_instructions:
        print(result["markdown"], end="")
    elif args.command == "demo" and args.text:
        print(render_demo(result["value"]))
    elif args.command == "demo" and args.decision_story:
        print(render_decision_story(result["value"]))
    elif args.command == "query" and args.text_output:
        print(result["text"])
    elif args.command == "context" and args.text:
        print(render_context(result))
    elif args.command == "source-compare" and args.text:
        print(render_revision_comparison(result))
    elif args.command == "evidence" and args.text:
        window = result["value"]
        if not window["source_revision_active"]:
            print(f"Historical revision: {window['evidence']['revision_checksum']}")
        print(
            render_context(
                {
                    "spans": [
                        {
                            "source_date": window["source_time"],
                            "evidence": [*window["before"], window["evidence"], *window["after"]],
                        }
                    ]
                }
            )
        )
    elif args.command == "status" and args.text_output:
        print(render_status(result))
    elif args.command in {"audio-source", "file-source", "memoket-source"}:
        print(encode(result))
    else:
        print(encode({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
