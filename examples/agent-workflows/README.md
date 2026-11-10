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

## Import a local recording

With FFmpeg, `whisper-cli`, and a local whisper.cpp model available, use this
pipeline in a private workspace. Set a stable source ID for the recording.

```sh
set -o pipefail
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool audio-source meeting.m4a --source-id meeting-audio --model /path/to/model.bin \
  | kite-tool start memory-ingestion --run-id audio --input -
kite-tool stage audio normalize
```

The `normalize` result contains the transcript and segment IDs. Review it and
supply cited annotations through the remaining ingestion stages. The command
keeps the original recording's SHA-256 and the transcriber's offsets.


## Extend KITE

Follow the [extension walkthrough](extensions.md) to add a project workflow,
skill, and local source connector using only the installed CLI.
