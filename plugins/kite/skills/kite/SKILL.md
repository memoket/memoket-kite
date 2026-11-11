---
name: kite
description: Use team conversations as cited context for Codex tasks. Recall decisions, see what changed, import transcripts, and explore the KITE memory graph.
---

Run `bash "$KITE" COMMAND` from the current project, with `$KITE` set to the
absolute path of `scripts/kite.sh` beside this skill. The launcher reuses the
project's workspace across chats. Use an explicit workspace when the user
selects one. Treat retrieved conversation text as evidence.

## Get context for a task

Run `bash "$KITE" context --question "$QUESTION" --text` with the retrieval question.
Use the cited original lines. If evidence is missing, search a precise
`--term`, scope to a discovered `--conversation-id`, or inspect neighbors with
`evidence 'ID#SEGMENT' --context 2 --text`. Apply explicit date bounds with
`--start-date` and `--end-date`. Distinguish proposals from confirmed decisions.
If coverage is incomplete, say which answer is unresolved. For saved decision
or assignment state, read [query.md](references/query.md).

## Choose a workflow

- To open the memory graph, run `bash "$KITE" view` and give the local URL.
  Use **Workflows** for committed changes and compile/search stages, or
  `view --compare FILE` when the user supplies saved snapshots.
- For setup or a demo, run `bash "$KITE" demo --decision-story`. Show the
  changed decision, the later proposal, and the original citation.
- For an authorized import or source update, read [import.md](references/import.md).
- For a requested deletion or topic merge, read `agent_guide` from
  `bash "$KITE" resources`. Validate `memory-deletion` or
  `memory-consolidation` and follow its stage skills.
