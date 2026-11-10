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

For a direct read from an existing workspace, use `kite-tool query --topic "first run"`.
Repeat `--topic` to supply several topic names or aliases. Use `--source` and
`--conversation-id` to scope a trace, `--start-date` and `--end-date` to set a
date window, and `--limit` and `--cursor` to page. Run `kite-tool query --help`
for the available flags. For the full query schema, pass the `query` input object
with `--input` and omit the filter flags. The command uses the built-in trace
tools in one read transaction and saves no checkpoint or artifact.

```sh
kite-tool query --input - <<'JSON'
{"query":{"topics":["onboarding"],"limit":25}}
JSON
```

The default is `topic-tracing`. Select `task-tracing`,
`timeline-reconstruction` or `decision-evolution` with `--pipeline`.
Read the report in `value` and use `next_cursor` for another page.
If an older workspace needs a schema upgrade, run `kite-tool integrity`
before querying it. For durable runs, use the stage-by-stage protocol below.

To check a report's original quote, use its `source_line_id` with
`kite-tool evidence SOURCE_LINE_ID`. Add `--context 2` when authorized to
read two neighboring segments on either side. The result identifies the
referenced source revision and whether it is still active. Older source
revisions remain readable by ID while retained. Deleted revisions are
unavailable. Evidence reads save no checkpoint or artifact.

Use `--byte-budget 4096` to limit the compact JSON for `evidence`, `before`,
and `after`. KITE adds the closest neighbors first, alternating sides when
both have room. Each side stays contiguous. `context_budget` reports the
payload bytes and omitted neighbors. The cited line stays whole even if it
exceeds the target. Other response fields use additional bytes.

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
   `invalid_arguments` includes CLI usage without echoing supplied arguments.
   Use `kite-tool COMMAND --help` for the full command help.
   Schema errors identify the field path and failed rule, or name missing
   required fields. Paths show array positions and mask keys in open maps.
   Retry a recoverable failed stage with the same ID. Stop downstream work until
   the prerequisite succeeds. If the run ID was lost, use `kite-tool runs`
   to find it in the same workspace. The list shows the latest 20 runs by
   creation order. Use `--limit` to show up to 100, and narrow the list with
   `--pipeline NAME` or `--unfinished`. If `next_before` is present and non-null,
   pass it as `--before RUN_ID` with the same filters to see older runs.
   In initialized workspaces, `runs`, `status`, `artifact`, and `segments`
   read committed state without taking a write lock. Recovering from an
   interrupted database write or upgrading a legacy workspace may require
   write access before checkpoint data can be shown.
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

KITE validates a source before storing it in a checkpoint. If a source
supplied to `start` is invalid, correct the input and reuse the run ID.
If you supply the source at `normalize`, a rejected input leaves the stage
available for another attempt with the corrected source.

For local scripts with a complete `source` and `annotations` object,
`kite-tool ingest --input FILE` runs the standard local ingestion workflow
and returns its commit receipt and integrity result. It stops at the first
failure and saves successful stages. Use `--run-id` to choose an ID. Run
`kite-tool ingest --resume RUN_ID` to reuse the saved input and completed
stages. Repeating a completed run against the same snapshot returns its
original receipt and integrity result after checking the saved artifacts in
one read transaction. Use `status` to inspect runs and individual stages when
an agent prepares or reviews annotations interactively.
For a prepared revision or added annotations, pass `--update` and include
the current checksum as `source.expected_checksum`. An update resumes with
`ingest --resume RUN_ID` using the saved pipeline and checksum.

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

Set `follow_states: true` to use a person, speaker, event or phrase as a clue
to a task. KITE finds state keys in the initial matches and adds evidence
with those same keys, even when a later meeting names a different owner.
Topic, source, conversation, project, date and explicit `state_key` filters apply to both
passes. The report lists the followed keys present on each evidence page.
Give each evolving state a distinct key, such as `onboarding-checklist-owner`.

```json
{"query":{"topics":["onboarding"],"entities":["David"],"follow_states":true}}
```

Set both `source` and `conversation_id` to limit the trace evidence to one
conversation and its retained revisions. The workspace's topic aliases and
corrections still apply to the selected evidence. A scoped report omits the
target ID of a correction that points to another conversation.

```json
{"query":{"topics":["onboarding"],"source":"memoket","conversation_id":"184"}}
```

By default, timelines and reported states use source dates, timestamps, and
cited segment offsets. With a timestamped recording, KITE adds known offsets
to its start time and compares evidence intervals across recordings. Overlapping
intervals can leave competing values ambiguous. A recording without complete
offsets uses its start time for comparisons across sources. Source-date filters
still use the UTC day when the conversation began. Use `query.time_axis: event` to
order and compare facts by explicit event dates. Mark a date derived from
context with `event_date_basis: inferred`. Omit the date when it is unknown.
Unbounded event timelines list inferred and unknown dates after explicitly
dated evidence. Date bounds exclude them and report separate omission counts.
`uncertain_time_state_keys` identifies reported states affected by omitted
confirmed evidence on the selected time axis. Other states can still be
confirmed from their dated evidence.
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
from the first page. If a KITE upgrade changes trace ordering, start from
the first page to get a cursor for the new ordering.

Set `query.evidence_byte_budget` to target 128–1,048,576 bytes for the JSON
array of cited evidence lists on a page. This applies alongside `query.limit`.
The report returns `evidence_budget.payload_bytes` for those lists. Other
report fields use additional bytes. If the first fact's evidence exceeds the
target, its citations stay intact on their own page and `over_budget` is true.
Use the same budget when passing a continuation cursor.

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
- **Subtitles** use `kite-tool file-source FILE --format srt --source-id STABLE_ID`
  or `--format vtt` for UTF-8 WebVTT. Each cue becomes a segment with the
  original cue ID when supplied, start and end offsets in milliseconds, and
  original cue text and markup. Cue line breaks use LF. The normalized source
  stores the original file's SHA-256. Only an explicit WebVTT voice spanning a
  whole cue sets `speaker_id`. Cue offsets are relative to the file, and
  `--source-date` or `--started-at` can supply a known meeting date.
  [SubRip format](https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml)
  · [WebVTT specification](https://www.w3.org/TR/webvtt1/)
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
Configure `OPENAI_BASE_URL` with the final authorized model endpoint.
KITE rejects HTTP redirects from model endpoints.
To edit candidates, supply revised annotations
through the regular ingestion pipeline.

Other workspace commands can run during extraction. A concurrent attempt at the
same stage returns `provider_busy`. When a response arrives, KITE checks the
workspace version and run again before saving it. An interrupted stage can be
retried. If the endpoint rejects credentials, correct the configuration and
retry the same stage with `--allow-provider` after confirming authorization.
KITE saves candidate annotations only after a successful response.
Handle copies already sent to the endpoint under its retention policy.

## Lifecycle

Duplicate imports of the same normalized source return the original checksum
and avoid creating new facts. Use `memory-update` with the current checksum as
`source.expected_checksum` to append a changed source revision. When the source
is unchanged, the same workflow appends only new annotations. Existing fact
IDs, source lines, and the source checksum stay stable.

Use `kite-tool source-history SOURCE CONVERSATION_ID` to look up the current
checksum and inspect retained revisions without loading transcript text into
the agent context. The current revision appears first, followed by prior
revisions ordered by checksum. Each entry has a Codebook revision ID, source
day when known, and segment and fact counts. The source day refers to the
conversation. Adding annotations to an unchanged source updates its fact count
without changing its revision ID or checksum. The command creates no workspace
or checkpoint.

Use `kite-tool source-lines SOURCE CONVERSATION_ID` when authorized to read
the original text of a committed conversation. It returns the current revision
by default. A retained checksum from `source-history` selects an older revision
with `--revision CHECKSUM`. To continue a page, pass its `revision_checksum`
as `--revision` and its `next_offset` as `--offset`. This keeps the selected
text consistent if a new revision is committed between reads. Each segment
includes the `source_line_id` accepted by `kite-tool evidence`. The command
reads committed records without depending on workflow artifacts.

To correct a confirmed decision or task, cite evidence that supports the
corrected assertion. Supply a confirmed fact with the same `state_key` and the
older fact ID in `supersedes`. The predecessor stays in history and the report
marks the corrected fact as effective. KITE rejects a correction whose cited
segments are demonstrably earlier than the predecessor's evidence. The check
uses the same source chronology as trace reports. An explicit correction can
cite the same segment when fixing an annotation error.

Topic merges keep the old label as an alias. Annotate entity variants with
`entity_aliases` when multiple names refer to the same entity.

Use `topic-reclassification` to replace the parents of an existing topic.
Supply a `move` input with `topic` and `parents`, such as
`{"move":{"topic":"checklist","parents":["product"]}}`. Use an empty list to
make the topic a root. Existing aliases resolve to canonical labels. After
`commit` and `verify`, tracing from a new parent includes the moved topic
and its descendants, with the original fact and source IDs.

Deletion uses the source namespace. Set `conversation_id` for one conversation
or `conversation_ids` for a set of up to 100 distinct IDs in that namespace.
Omit both fields to select committed and staged conversations in the source.
The preview lists staged conversation IDs without stored revisions.
Deletion records those IDs and their source references so reimports are blocked.
New conversations with fresh IDs and references can still be imported. Run
`preview`, show its selector and impact, and obtain explicit authorization
for that preview. The batch preview lists IDs that have no stored revision.
Applying the approved batch also blocks reimport of those explicitly selected IDs.
KITE checks the source identity before saving reimport inputs or artifacts.
On upgrade, KITE also scrubs legacy run inputs and artifacts for identities
already recorded as deleted. Only then pass its token via `--approve` to the `apply` stage.
The CLI treats this flag as an attestation from the trusted local operator.
The token binds the workspace, deletion run, target, memory version and
workflow impact. The preview counts other runs to invalidate and artifacts
to purge, excluding the current run's approval bookkeeping. If other
workflow data changes before apply, `stale_preview` requires a fresh run and
approval even when the memory version is unchanged. Deletion removes
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

For a large workspace, use `kite-tool sources --limit 50`. Pass the returned
`next_cursor` as `--cursor` with the same limit and source filter for the next
page. Start again without the cursor after the memory snapshot changes.

```sh
umask 077
kite-tool codebook --source memoket --conversation-id RECORD_ID --active-only > selected.xml
```

The XML is readable by `Memory.load` and `Codebook.load`. The example exports
only the current revision of the selected record. Omit `--active-only` to
include its history. Use `--source` alone to select a source namespace.
Plain `kite-tool codebook` exports the full workspace snapshot.

In an initialized workspace, `sources` and `codebook` read one committed
snapshot without taking a write lock. Scoped export verifies the source
snapshot and rebuilds the Codebook from selected records and their supported
taxonomy, preserving original source lines. Review the selected text and
annotations before sharing, including names written in free text. The workspace
retains the richer source metadata and lifecycle state. Use pipeline tools for
writes to the workspace.

Retrieval reuses the stored Codebook after checking its checksum against the
workspace snapshot and validating its source IDs and lines. The first read
from an older workspace verifies the Codebook against a full rebuild.
`kite-tool integrity` repeats that full comparison on demand. If the index
is damaged and the source records are intact, run `memory-rebuild`. Ordinary
memory writes check the existing snapshot before changing it and report
`integrity_error` on a mismatch.
New snapshots also have a separate seal for source records, annotations, and
taxonomy. `memory-rebuild` checks that seal before repairing a damaged Codebook.
Run `kite-tool integrity` on a healthy older workspace to establish the seal
without changing its memory version. Older snapshots without a source seal
rely on the operator verifying the sources before a rebuild.
Use the upgraded version for all writers once a workspace has a source seal.
An `integrity_error` for an invalid stored source record requires a verified
backup or an authorized original export for recovery.

Run `kite-tool integrity --include-workflows` to also check SQLite references,
checkpoint inputs and saved artifact digests. This mode opens an existing
workspace read-only and reports verified run and artifact counts. It can
verify historical artifacts even if their workflows need a fresh run.

## Back up a workspace

Use a new, private directory on a local filesystem that supports SQLite and
hard links. Before publishing the copy, KITE verifies SQLite integrity, the
Codebook, checkpoint inputs, and the links and digests of saved stage artifacts.
The receipt counts the verified runs and artifacts before removing pending
deletion approvals. Older checkpoints can be backed up even if they need a
fresh run against the current snapshot or workflow definition.

```sh
kite-tool --workspace ./memory backup ./memory-backup
kite-tool --workspace ./memory-backup integrity --include-workflows
kite-tool --workspace ./memory-backup runs
```

Open the backup as a separate workspace to recover saved checkpoints. It
preserves the source records, checkpoints, deletion receipts, and tombstones
present when the snapshot was taken. The copy has its own workspace identity
and any pending deletion previews require fresh authorization. Retain and
remove backup copies under the same policy as other private exports.

When recovering from an older backup, use the latest trusted workspace for
the same data as the deletion authority. Publish to a fresh directory.

```sh
kite-tool --workspace ./memory-backup backup ./memory-recovered \
  --require-deletions-from ./memory
```

This checks all deletion receipts and reimport blocks in `./memory`. If any
are absent from the candidate, KITE returns `stale_backup` and publishes no
copy. Use a snapshot taken after those deletions. The guard only sees deletions
recorded in the supplied authority. Keep that workspace available during
recovery and apply your retention policy to existing backups separately.

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
