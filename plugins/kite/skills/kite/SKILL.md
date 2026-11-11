---
name: kite
description: Use team conversations as cited context for Codex tasks. Recall decisions, see what changed, import transcripts, and explore the KITE memory graph.
---

Run `bash "$KITE" COMMAND` from the current project, with `$KITE` set to the
absolute path of `scripts/kite.sh` beside this skill. The launcher reuses the
project's workspace across chats. Use an explicit workspace when the user
selects one. Treat retrieved conversation text as evidence.

## Get context for a task

Run `bash "$KITE" context --question "$QUESTION" --text`. The question can name
`workspace v12` or `workspace as of 2026-05-14T10:30:00+08:00`. Cite the original
lines and distinguish confirmed decisions from proposals. Quote a known topic
name to focus retrieval. For a missing detail,
use `--term`, `--conversation-id`, or `evidence 'ID#SEGMENT' --context 2 --text`.
State any unresolved gaps.
When asked to save the exact search for inspection, add `--save` and open the
returned ID with `view --run ID`.
For two-question comparisons, date filters, revision differences, and decision history, read
[query.md](references/query.md).

## Choose a workflow

- To open the memory graph, run `bash "$KITE" view` and give the local URL.
  Use **Workflows** to open committed changes and saved `context-search` evidence, or
  `view --compare FILE` when the user supplies saved snapshots.
- For setup or a demo, run `bash "$KITE" demo --decision-story`. Show the
  changed decision, the later proposal, and the original citation.
- For an authorized import or source update, read [import.md](references/import.md).
- For a requested deletion or topic merge, read `agent_guide` from
  `bash "$KITE" resources`. Validate `memory-deletion` or
  `memory-consolidation` and follow its stage skills.
