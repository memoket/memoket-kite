# Conversation workflows in Codex or Claude Code

Install KITE with the `agent` extra from a checkout and run the offline demo.

```sh
python -m pip install '.[agent]'
kite-tool demo --text
```

Use `kite-tool resources` to locate the workflow and example files in an installed package.

To try a CI build without cloning, open a successful run for the commit you
want to try in [GitHub Actions](https://github.com/memoket/memoket-kite/actions/workflows/ci.yml).
Sign in to GitHub and download `kite-preview-distributions` from the run's
Artifacts section. Extract it and install the wheel with the `agent` extra
in a fresh virtual environment.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install './memoket_kite-0.1.0-py3-none-any.whl[agent]'
kite-tool demo --text
```

In Windows PowerShell, use `.\.venv\Scripts\Activate.ps1` to activate the
environment.
Use the downloaded wheel's filename if the version differs. CI retains these
builds for 14 days.

The demo imports an early text proposal and a later Memoket transcript in
separate processes, resumes from their checkpoints, and traces the topic by
its alias. The report cites the line where the team chose a short checklist.
Run `kite-tool demo` for the complete JSON report, or
`kite-tool demo --metrics` for deterministic fixture metrics.

Keep a verified copy of the demo in a new workspace to try your own queries
without a checkout.

```sh
kite-tool demo --text --save-workspace ./kite-demo
kite-tool --workspace ./kite-demo query --topic "first run" --text
```

The text report shows the timeline, reported states, and original quotes. Pass a
source line ID from the report to `kite-tool evidence` to read nearby lines.
Omit `--text` for the complete JSON report. The save operation requires a new
destination directory. Use
`kite-tool --workspace ./kite-demo artifact trace report` to read the saved
trace and compare its evidence with a fresh query.

## Try the stages yourself

Run these commands from the KITE checkout.

```sh
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool start memory-ingestion --run-id meeting-a --input examples/agent-workflows/first-meeting.json
kite-tool stage meeting-a normalize
kite-tool runs
kite-tool status meeting-a
kite-tool stage meeting-a annotate
kite-tool stage meeting-a commit
kite-tool stage meeting-a verify

kite-tool start memory-ingestion --run-id meeting-b --input examples/agent-workflows/second-meeting.json
kite-tool stage meeting-b normalize
kite-tool stage meeting-b annotate
kite-tool stage meeting-b commit
kite-tool stage meeting-b verify

kite-tool start topic-tracing --run-id trace-a --input examples/agent-workflows/topic-query.json
kite-tool stage trace-a resolve
kite-tool stage trace-a retrieve --summary
kite-tool stage trace-a report
```

Use `kite-tool runs` to recover a lost run ID, then check
`resume.stages` in `kite-tool status RUN_ID` for the next stage and any
missing inputs. The retrieval stage above uses `--summary` because the report
includes the matching facts and their evidence. Look at
`current_reported_states.onboarding-design` and match its fact ID to the
timeline and original evidence.

For a quick read of the same workspace, pass the existing query file
directly. This produces the same report without saving a run.

```sh
kite-tool query --input examples/agent-workflows/topic-query.json
```

Use flags for an ad hoc query. The example below reads only the Memoket
conversation from September 4 and returns one fact per page.

```sh
kite-tool query --topic "first run" --source memoket --conversation-id 184 \
  --start-date 2026-09-04 --end-date 2026-09-04 --limit 1
```

To continue, repeat the query with the same filters and pass `--cursor` with
the report's `next_cursor` value. After the workspace changes, run the first
page again. Use `--pipeline timeline-reconstruction` for date, speaker or project
queries without a topic.

To read the original transcript for an authorized committed conversation, use
its source namespace and conversation ID directly.

```sh
kite-tool source-lines memoket 184 --limit 1
```

For the next page, pass the returned `revision_checksum` as `--revision` and
`next_offset` as `--offset`. Copy a segment's `source_line_id` to `kite-tool evidence`
to read that line with nearby context. Use `source-history` to find the checksum
of a retained earlier revision.

## Import with prepared annotations

For a local script with a prepared `source` and `annotations`, run the same
ingestion stages with one command in a fresh workspace.

```sh
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool ingest --resume-existing --run-id meeting-a --input examples/agent-workflows/first-meeting.json
kite-tool query --input examples/agent-workflows/topic-query.json
```

The command returns a commit receipt and integrity counts. It saves each stage
before advancing. With `--resume-existing`, rerun the same command to continue
or retrieve the receipt from a completed run at the current snapshot. KITE
checks that the pipeline and inputs match. Run `kite-tool ingest --resume meeting-a`
to use the saved inputs without reading the input file again. Use
`kite-tool status meeting-a` to inspect the checkpoint. Use a new run ID for
corrected annotations or other input changes.
Use the stage-by-stage workflow when the agent still needs to extract or review
annotations.

If you already reviewed the annotations, pass the source and the annotation
file separately. For example, put `The launch review is Tuesday.` on line 3 of
`notes.txt` and save the following as `annotations.json`.

```json
{
  "annotations": {
    "facts": [
      {
        "content": "The launch review is Tuesday.",
        "topics": ["launch review"],
        "segment_ids": ["text-line-3"]
      }
    ]
  }
}
```

```sh
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool file-source notes.txt --format text --source-id launch-notes --split-lines \
  | kite-tool ingest --input - --annotations annotations.json --run-id launch-notes --resume-existing
kite-tool query --topic "launch review" --text
```

`--annotations` takes the same JSON object as `kite-tool stage RUN_ID annotate`.
Use it with a source-only `--input` from `file-source` or `audio-source`. Once
the run has a checkpoint, `kite-tool ingest --resume launch-notes` reads its
saved inputs.

## Give your agent a task

> Run kite-tool resources and read the agent instructions it points to. Use the
> synthetic meeting inputs in the example directory.
> Run memory-ingestion for both meetings and use topic-tracing to tell me what
> was decided about first run, what the team rejected, and which original line
> supports each conclusion. Use a new temporary KITE workspace.

For an authorized real transcript, start with only its `source` input, run
`normalize`, then use the stage skill to create an `annotations` JSON object.
Pass it via `kite-tool stage RUN_ID annotate --input FILE`.
See the [agent guide](../../AGENT_GUIDE.md) for contracts, lifecycle and access.

## Import local notes or a Memoket export

Prepare an authorized file and pipe it directly into a new run.

```sh
set -o pipefail
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool file-source notes.txt --format text --source-id planning-notes --split-lines \
  | kite-tool start memory-ingestion --run-id notes --input -
kite-tool stage notes normalize
```

Finish the notes run with `annotate`, `commit`, and `verify`. Annotations can
cite individual file lines by segment ID, such as `text-line-3`. For prose
that should stay together as one segment, omit `--split-lines`. Then start
the Memoket export in the same workspace.

```sh
kite-tool file-source transcript.json --format memoket \
  | kite-tool start memory-ingestion --run-id transcript --input -
kite-tool stage transcript normalize
```

Finish the export run with `annotate`, `commit`, and `verify`.
Keep the same text source ID and segmentation option for repeated imports. For a changed source, pass
the previously committed checksum with `--expected-checksum` and use
`memory-update`. Date and timestamp flags are optional for text files. Memoket
exports retain the original record identity and any supplied dates.

Use the saved response directly for a complete single-page Memoket
`get_transcripts` result. For a longer recording, follow the two paging
cursors and pass each saved response in the order you received it.
See the [Memoket connector reference](https://eu.memoket.ai/pages/connector).

```sh
kite-tool file-source first-page.json --format memoket \
  --page second-page.json --recording-metadata recordings.json \
  | kite-tool start memory-ingestion --run-id transcript-pages --input -
```

Repeat `--page` for each additional response from the same recording.
The optional `--recording-metadata` file can contain the saved
`get_conversations` response or the matching recording item. KITE selects
the matching ID and preserves its start time, title, and language.

For an authorized WebVTT or SubRip file, use the same ingestion stages.

```sh
kite-tool file-source meeting.vtt --format vtt --source-id planning-captions \
  | kite-tool start memory-ingestion --run-id captions --input -
kite-tool stage captions normalize --summary
kite-tool segments captions normalize --limit 25
```

Use `--format srt` for a SubRip file. Cite the displayed segment IDs in
annotations. Reports preserve cue start and end offsets.

## Add an annotation to an existing conversation

Use the exact same source file, identity, and any original date flags.
Look up the current checksum for the conversation, then prepare an update.

```sh
kite-tool source-history text planning-notes
# Set current_checksum to the current_checksum value from the output above.
kite-tool file-source notes.txt --format text --source-id planning-notes --split-lines \
  --expected-checksum "$current_checksum" \
  | kite-tool start memory-update --run-id amend-notes --input -
kite-tool stage amend-notes normalize
kite-tool stage amend-notes annotate --input additional-annotations.json
kite-tool stage amend-notes commit --summary
kite-tool stage amend-notes verify --summary
```

Supply only the added facts or taxonomy in `additional-annotations.json`.
Each fact cites an existing segment. To correct a confirmed state, use the
same state key and put the previous fact ID in `supersedes`.

If both the source and added annotations are already in one JSON file,
use `kite-tool ingest --update --run-id amend-notes --input update.json`.
Include the current checksum as `source.expected_checksum`. To resume this
scripted update, use `kite-tool ingest --resume amend-notes`.

## Import a local recording

With FFmpeg, `whisper-cli`, and a local whisper.cpp model available, use this
pipeline in a private workspace. Set a stable source ID for the recording.

```sh
set -o pipefail
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool audio-source meeting.m4a --source-id meeting-audio --model /path/to/model.bin \
  --started-at 2026-11-10T09:00:00+08:00 \
  | kite-tool start memory-ingestion --run-id audio --input -
kite-tool stage audio normalize
```

The `normalize` result contains the transcript and segment IDs. Review it and
supply cited annotations through the remaining ingestion stages. The command
keeps the original recording's SHA-256 and the transcriber's offsets.
Use `--source-date YYYY-MM-DD` if you only know the day. Leave both time options
out when the recording date is unknown. For a revised recording, pass the
current checksum via `--expected-checksum` and start `memory-update` with the
same source ID.


## Extend KITE

Follow the [extension walkthrough](extensions.md) to add a project workflow,
skill, and local source connector using only the installed CLI.
