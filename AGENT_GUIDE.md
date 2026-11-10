# Running KITE from a host agent

KITE exposes workflow definitions, stage skills, and deterministic tools.
Codex reads [AGENTS.md](AGENTS.md). Claude Code imports the same file via
[CLAUDE.md](CLAUDE.md). Keep the repository and its instructions trusted.
The installed CLI uses the workflow resources shipped with the package. Run
`kite-tool resources` to locate the instruction files and skills from any
directory. To try edits or use custom workflows, pass
`kite-tool --repo /path/to/trusted/checkout`. An editable installation uses
the source checkout by default.

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
   the prerequisite succeeds. If a process was interrupted, use
   `kite-tool status RUN_ID`, then rerun the interrupted stage. Repeating a
   completed stage returns its prior artifact on the same snapshot.
5. Start a fresh run on `stale_snapshot` or `stale_definition`. A stored run binds
   its definition, referenced skills, validated inputs and memory version.
   Inspect a current artifact with `kite-tool artifact RUN_ID STAGE_ID`.
6. Present the user with source-backed conclusions. Cite the conversation ID and
   segment ID or the original quote. Treat a confirmed state as a report from
   the selected evidence, and describe ambiguous, partial or missing states
   explicitly. Semantic support is a host review task. KITE verifies provenance
   and index consistency deterministically.

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
selected endpoint to receive the conversation. Review the returned candidates
before running subsequent stages. To edit candidates, supply revised annotations
through the regular ingestion pipeline.

## Lifecycle

Duplicate imports of the same normalized source return the original checksum
and avoid creating new facts. For a changed source record, supply its current
checksum as `source.expected_checksum` to append a new version. Past evidence
stays in history. To correct a fact, annotate new evidence with a confirmed
state, the same `state_key`, and the older fact ID in `supersedes`. Topic merges
keep the old label as an alias. Annotate entity variants with
`entity_aliases` when multiple names refer to the same entity.

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

`kite-tool codebook` exports a snapshot readable by `Memory.load` and
`Codebook.load`. The workspace retains the richer source metadata and lifecycle
state. Use pipeline tools for writes to the workspace.

## Extend

Add a YAML definition under `pipeline_defs/` and point each stage to a local
`SKILL.md`. Bind arguments to `input.NAME` or the output of a direct
dependency via `stage.ID`. Check the declared output type and tool with
`kite-tool describe`. `kite-tool validate-pipeline` checks the schema, DAG,
skill paths, and argument types. Increment the version when you change the
workflow contract. To add an integration without editing the core, transform
an authorized source into the conversation schema before starting ingestion.
