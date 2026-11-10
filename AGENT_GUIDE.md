# Running KITE from a host agent

KITE exposes workflow definitions, stage skills, and deterministic tools.
Codex reads [AGENTS.md](AGENTS.md). Claude Code imports the same file via
[CLAUDE.md](CLAUDE.md). Keep the repository and its instructions trusted.
The installed CLI uses the workflow resources shipped with the package. Run
`kite-tool resources` to locate the instruction files and skills from any
directory. Use `kite-tool --extensions DIR` to add trusted project workflows.
To work on a full KITE checkout, pass `kite-tool --repo /path/to/trusted/checkout`.
An editable installation uses the source checkout by default.

```sh
python -m pip install -e '.[agent]'
kite-tool resources
kite-tool pipelines
kite-tool validate-pipeline memory-ingestion
kite-tool describe
kite-tool demo
```

The demo uses synthetic data and a temporary workspace. It requires no model
credentials. See the [copyable CLI walkthrough](examples/agent-workflows/README.md).

## Choose a workflow

| Task | Pipeline |
| --- | --- |
| Import text, a transcript or a Memoket record | `memory-ingestion` |
| Propose facts with an authorized model endpoint | `memory-provider-ingestion` |
| Recover discussions, proposals and decisions by topic | `topic-tracing` |
| Recover assignments, blockers and status reported in conversation | `task-tracing` |
| Rebuild a discussion timeline by date, people, project or topic | `timeline-reconstruction` |
| Follow how a decision or requirement changed | `decision-evolution` |
| Append a source revision or a cited correction | `memory-update` |
| Merge two equivalent topic labels | `memory-consolidation` |
| Audit the source records and Codebook | `memory-maintenance` |
| Rebuild a damaged snapshot from committed records | `memory-rebuild` |
| Preview and apply permanent workspace deletion | `memory-deletion` |

## Advance and recover

1. Read the selected definition using `kite-tool validate-pipeline NAME`.
   Read the skill at each stage's `skill` path. Inspect the JSON contract with
   `kite-tool describe TOOL`.
2. Start the run with `kite-tool start NAME --input INPUT.json`. Supply the
   pipeline's named inputs in a JSON object. Save the returned run ID.
3. Invoke `kite-tool stage RUN_ID STAGE_ID` once the stage's `needs` are completed.
   A stage can receive its own missing input via `--input FILE` or `--input -`.
   For example, normalize a transcript before supplying host-generated annotations.
   Review the input and evidence against the skill, especially for claims marked
   confirmed.
4. Inspect the JSON response, including `value`, `manifest` and `checkpoint`.
   An exit code of 2 means the command failed and the JSON error provides a code.
   Retry a recoverable failed stage with the same ID. Stop downstream work until
   the prerequisite succeeds. If the run ID was lost, use `kite-tool runs`
   to find it in the same workspace. The list shows the latest 20 runs by
   creation order. Use `--limit` to show up to 100.
5. Use `kite-tool status RUN_ID` to inspect `resume.stages`. Each eligible
   stage lists its skill, the names and types of missing inputs, and any flags
   that require explicit user authorization. Repeating a completed stage
   returns its prior artifact on the same snapshot. Inspect a current artifact
   with `kite-tool artifact RUN_ID STAGE_ID`. Start a fresh run if `resume.reason`
   is `stale_snapshot` or `stale_definition`. A change to a stage tool's
   version can invalidate an existing run.
   For intermediate stages whose output you do not need to inspect, use
   `kite-tool stage RUN_ID STAGE_ID --summary`. The response includes stage and
   run status, retry count, and the manifest when successful. Use the full
   response or `kite-tool artifact` when reviewing evidence, provider
   candidates, or a deletion preview.
6. Present the user with source-backed conclusions. Cite the conversation ID and
   segment ID or the original quote. Treat a confirmed state as a report from
   the selected evidence, and describe ambiguous, partial or missing states
   explicitly. Semantic support is a host review task. KITE verifies provenance
   and index consistency deterministically.

By default, timelines and reported states use source dates, timestamps, and
available offsets within a single recording. Use `query.time_axis: event` to
order and compare facts by annotated event dates. Reports retain the source
date for citation. Two facts on the same event day do not establish an order.
Known overlapping source intervals also leave their order unresolved. Reports
mark competing values as ambiguous and distinguish missing dates excluded by
date bounds from results truncated by a limit.

## Source and access

Select a workspace via `--workspace DIR` or `KITE_DATA_DIR`. The default is
`${XDG_DATA_HOME:-$HOME/.local/share}/kite`. Use a separate private local
workspace per user or access scope. On POSIX the directory and database must
allow only owner access. The workspace uses one SQLite transaction for a
successful stage, its artifacts and its memory changes. Use a local filesystem
that supports SQLite locking.

- **Text** uses an inline `payload.text` and becomes segment `text-1`. For a
  local file, use `kite-tool file-source FILE --format text --source-id STABLE_ID`.
- **Transcript** and **audio-transcript** accept source segments from the user or
  an authorized transcriber. For a local audio file, `kite-tool audio-source`
  prepares the input using FFmpeg and a local whisper.cpp model.
  See the [audio skill](skills/ingestion/audio-transcription/SKILL.md).
- **Memoket** accepts an authorized record transcription response with
  `data.id` and `data.segments[].sentences[]`, or the inner data object. The
  connector keeps source IDs, original text, available speaker and millisecond
  offsets. Use `kite-tool file-source EXPORT.json --format memoket` to prepare a
  local export. Preparation discards response fields such as signed audio URLs.
- **Custom sources** use `format: conversation` and the
  [conversation schema](schemas/conversation.schema.json) with a stable source
  name and `metadata.source_reference`. Import a local canonical export with
  `kite-tool file-source EXPORT.json --format conversation`. Keep credentials
  out of the source and metadata. Missing dates and speakers stay unknown.

The normal ingestion workflow expects host-prepared annotations. The optional
provider stage uses KITE's existing extractor and reads its credentials from
the environment. Apply `--allow-provider` only when the user has authorized the
selected endpoint to receive the conversation. Provider extraction sends the
selected transcript and its supplied context, using a fresh taxonomy. Workspace
topics and entity labels stay local. Review the returned candidates before
running subsequent stages. To edit candidates, supply revised annotations
through the regular ingestion pipeline.

Other workspace commands can run during extraction. A concurrent attempt at the
same stage returns `provider_busy`. When a response arrives, KITE checks the
workspace version and run again before saving it. An interrupted stage can be
retried. Handle copies already sent to the endpoint under its retention policy.

## Lifecycle

Duplicate imports of the same normalized source return the original checksum
and avoid creating new facts. Use `memory-update` with the current checksum as
`source.expected_checksum` to append a changed source revision. When the source
is unchanged, the same workflow appends only new annotations. Existing fact
IDs, source lines, and the source checksum stay stable.

To correct a confirmed decision or task, cite evidence that supports the
corrected assertion. Supply a confirmed fact with the same `state_key` and the
older fact ID in `supersedes`. The predecessor stays in history and the report
marks the corrected fact as effective. Topic merges keep the old label as an
alias. Annotate entity variants with `entity_aliases` when multiple names refer
to the same entity.

Deletion uses the source namespace and optionally a conversation ID. Run
`preview`, show its selector and impact, and obtain explicit authorization
for that preview. Only then pass its token via `--approve` to the `apply` stage.
The CLI treats this flag as an attestation from the trusted local operator.
The token binds the workspace, target and memory version. Deletion removes
all target revisions and references, records tombstones, and conservatively
purges prior run inputs and artifacts that could contain the target. When
deleted revisions sit between surviving corrections, KITE reconnects the
surviving facts so the prior owner or decision stays superseded. The workspace
retains a redacted receipt available via `kite-tool deletion-audit`.
Exports, host-agent transcripts and external backups are separate copies and
follow the operator's retention policy. Handle all artifact and Codebook
output as private data.

## Export a Codebook

Run `kite-tool sources` to find conversation IDs and current checksums without
reading transcript text. Use the checksum for incremental source updates or the
source and ID to select an export.

```sh
umask 077
kite-tool codebook --source memoket --conversation-id RECORD_ID --active-only > selected.xml
```

The XML is readable by `Memory.load` and `Codebook.load`. The example exports
only the current revision of the selected record. Omit `--active-only` to
include its history. Use `--source` alone to select a source namespace.
Plain `kite-tool codebook` exports the full workspace snapshot.

Scoped export rebuilds the Codebook from selected records and their supported
taxonomy, preserving original source lines. Review the selected text and
annotations before sharing, including names written in free text. The workspace
retains the richer source metadata and lifecycle state. Use pipeline tools for
writes to the workspace.

## Extend

Use `kite-tool --extensions DIR` to discover and run trusted project workflows
alongside the installed ones. Put YAML definitions under `DIR/pipeline_defs/`
and their skills under `DIR/skills/`. Choose IDs distinct from the bundled
workflows. Read each stage's relative skill path from the `skill_root` returned
by `validate-pipeline`. Use the extension again for `status`, `stage`, and
`artifact` on its runs.

Bind arguments to `input.NAME` or the output of a direct dependency via
`stage.ID`. Check the declared output type and tool with `kite-tool describe`.
The validator checks the schema, DAG, skill paths, and argument types. Increment
the version when you change the workflow contract. A connector can transform an
authorized export into the installed conversation schema and hand it to
`kite-tool file-source`. Follow the [standalone extension walkthrough](examples/agent-workflows/extensions.md).
