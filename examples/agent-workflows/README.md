# Conversation workflows in Codex or Claude Code

Open the KITE checkout in your agent and run the offline demo.

```sh
python -m pip install -e '.[agent]'
python examples/agent-workflows/demo.py
```

The demo imports an early text proposal and a later Memoket transcript in
separate processes, resumes from their checkpoints, and traces the topic by
its alias. The report cites the line where the team chose a short checklist.

## Try the stages yourself

```sh
export KITE_DATA_DIR="$(mktemp -d)"
kite-tool start memory-ingestion --run-id meeting-a --input examples/agent-workflows/first-meeting.json
kite-tool stage meeting-a normalize
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
kite-tool stage trace-a retrieve
kite-tool stage trace-a report
```

Resume after any command with `kite-tool status RUN_ID`. In the report, look
at `current_reported_states.onboarding-design`, then match its fact ID to the
timeline and original evidence.

## Give your agent a task

> Read AGENTS.md. Use the synthetic meeting inputs in examples/agent-workflows.
> Run memory-ingestion for both meetings and use topic-tracing to tell me what
> was decided about first run, what the team rejected, and which original line
> supports each conclusion. Use a new temporary KITE workspace.

For an authorized real transcript, start with only its `source` input, run
`normalize`, then use the stage skill to create an `annotations` JSON object.
Pass it via `kite-tool stage RUN_ID annotate --input FILE`.
See the [agent guide](../../AGENT_GUIDE.md) for contracts, lifecycle and access.
