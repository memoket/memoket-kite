# Running KITE from a host agent

KITE exposes workflow definitions, stage skills, and deterministic tools.
Codex reads [AGENTS.md](AGENTS.md). Claude Code imports the same file via
[CLAUDE.md](CLAUDE.md). Keep the repository and its instructions trusted.
The installed CLI uses the workflow resources shipped with the package. Run
`kite-tool resources` to locate the instruction files and skills from any
directory. Use `kite-tool --extensions DIR` to add trusted project workflows.
If a project definition fails validation, `pipelines` lists the other
available workflows and reports the rejected ID under `invalid_extensions`.
Inspect those diagnostics before selecting a workflow.
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
| Change a topic's parents | `topic-reclassification` |
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
   is `stale_snapshot` or `stale_definition`. Checkpoints bind the schemas
   and tool versions used by their pipeline. The upgrade to these scoped
   fingerprints requires starting a new run for older checkpoints.
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

A pending `memory-ingestion` run for a new source can resume after another
import commits. Its normalized conversation and annotations remain available
when `snapshot_current` is false and `resume.state` is `available`. Commit
validates the source identity, revision and annotation references against the
current memory. Source updates with an expected checksum still require a
current snapshot. Deletion invalidates pending imports and clears their artifacts.

For a long transcript, normalize with `--summary`, then read the source in
pages using the same run and stage IDs.

```sh
kite-tool stage RUN_ID normalize --summary
kite-tool segments RUN_ID normalize --limit 25 --byte-budget 16384
```

Use the returned `next_offset` as the next `--offset` until it is null.
The byte budget applies to the JSON segment array. If a single segment
exceeds the budget, KITE returns that segment intact and sets `over_budget`.
Cite the original segment IDs in the annotations.

Filter a timeline by `query.events` to match normalized event labels from
fact annotations. The query matches any listed event and intersects it with
other filters such as `projects`, `speakers` and date bounds. Check
`unresolved_events` in the report for labels absent from the workspace.

Use `query.entities` for people or other entities named in fact annotations.
It resolves aliases to canonical codes and matches any of the requested
entities. The report lists `resolved_entities`
and `unresolved_entities`. When only some requested names are recognized,
the trace retains matching evidence and marks any reported states partial.
Use `query.speakers` for the recorded speaker on an evidence segment.

By default, timelines and reported states use source dates, timestamps, and
available offsets within a single recording. Use `query.time_axis: event` to
order and compare facts by explicit event dates. Mark a date derived from
context with `event_date_basis: inferred`. Omit the date when it is unknown.
Unbounded event timelines list inferred and unknown dates after explicitly
dated evidence. Date bounds exclude them and report separate omission counts.
Only explicit event dates establish precedence on the event axis. Two facts on
the same event day or known overlapping intervals can leave competing values
ambiguous. Reports retain the source date for citation and separately mark
results truncated by a limit.

When a query supplies `end_date`, the report evaluates supersession through
that day on the selected time axis. Only corrections dated on or before
the cutoff can supersede evidence in that window. An uncertain correction
date marks its affected state partial, even if the correction falls outside
the topic filter. The report uses the current workspace's source revisions.
Unbounded queries apply the latest known supersession links.

For long histories, set `query.limit` to control the number of facts in a
report. Start another run with the same pipeline and query and put the prior
report's `next_cursor` in `query.cursor`. Continue until `next_cursor` is null.
Each page orders its dated entries chronologically. Pages begin with the most
recent dated window, then work backward and finish with any uncertain dates.
A partial page leaves reported states tentative. If memory changes, restart
from the first page.

For topic-filtered traces, check `related_topics` and `related_entities`
for labels that also appear in the current page's evidence. Each list contains
up to ten labels ranked by matching fact count, with up to three supporting
fact IDs per label. The selected topic and its descendants are excluded from
the related topics. Use the codes in a follow-up query to explore another
topic or narrow a trace by entity. The suggestions leave the original timeline
and reported states unchanged.

## Source and access

Select a workspace via `--workspace DIR` or `KITE_DATA_DIR`. The default is
`${XDG_DATA_HOME:-$HOME/.local/share}/kite`. Use a separate private local
workspace per user or access scope. On POSIX the directory and database must
allow only owner access. The workspace uses one SQLite transaction for a
successful stage, its artifacts and its memory changes. Use a local filesystem
that supports SQLite locking.

- **Text** uses an inline `payload.text` and becomes segment `text-1`. For a
  local file, use `kite-tool file-source FILE --format text --source-id STABLE_ID`.
  Add `--split-lines` for notes with one statement per line. Each nonblank
  line becomes a segment such as `text-line-3`, using the original one-based
  line number and preserving its text. The source checksum also tracks changes
  to the original UTF-8 file bytes. Use the same segmentation option for
  repeat imports.
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
running subsequent stages. Provider-suggested event dates are labeled inferred.
To edit candidates, supply revised annotations
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

Use `topic-reclassification` to replace the parents of an existing topic.
Supply a `move` input with `topic` and `parents`, such as
`{"move":{"topic":"checklist","parents":["product"]}}`. Use an empty list to
make the topic a root. Existing aliases resolve to canonical labels. After
`commit` and `verify`, tracing from a new parent includes the moved topic
and its descendants, with the original fact and source IDs.

Deletion uses the source namespace. Set `conversation_id` for one conversation
or `conversation_ids` for a set of up to 100 distinct IDs in that namespace.
Omit both fields to select all existing conversations in the source. Run
`preview`, show its selector and impact, and obtain explicit authorization
for that preview. The batch preview lists IDs that have no stored revision.
Applying the approved batch also blocks reimport of those explicitly selected IDs.
KITE checks the source identity before saving reimport inputs or artifacts.
On upgrade, KITE also scrubs legacy run inputs and artifacts for identities
already recorded as deleted. Only then pass its token via `--approve` to the `apply` stage.
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

Retrieval reuses the stored Codebook after checking its checksum against the
workspace snapshot and validating its source IDs and lines. The first read
from an older workspace verifies the Codebook against a full rebuild.
`kite-tool integrity` repeats that full comparison on demand. If the index
is damaged and the source records are intact, run `memory-rebuild`.

## Back up a workspace

Use a new, private directory on a local filesystem that supports SQLite and
hard links. Before publishing the copy, KITE verifies SQLite integrity, the
Codebook, checkpoint inputs, and the links and digests of saved stage artifacts.
The receipt counts the verified runs and artifacts before removing pending
deletion approvals. Older checkpoints can be backed up even if they need a
fresh run against the current snapshot or workflow definition.

```sh
kite-tool --workspace ./memory backup ./memory-backup
kite-tool --workspace ./memory-backup integrity
kite-tool --workspace ./memory-backup runs
```

Open the backup as a separate workspace to recover saved checkpoints. It
preserves the source records, checkpoints, deletion receipts, and tombstones
present when the snapshot was taken. The copy has its own workspace identity
and any pending deletion previews require fresh authorization. Retain and
remove backup copies under the same policy as other private exports.

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
