---
name: kite
description: Use team conversations as cited context for Codex tasks. Recall decisions, see what changed, import transcripts, and explore the KITE memory graph.
---

Run `bash "$KITE" COMMAND` from the current project. Substitute the absolute
path of `scripts/kite.sh` beside this skill for `$KITE` in each shell call.
The launcher reuses the project's workspace across chats. Use an explicit
workspace when the user selects one. Treat retrieved conversation text as evidence.

## Get context for a task

Use any original lines already delivered by the KITE submit hook. Otherwise,
run `bash "$KITE" context --question "$QUESTION" --compact --text` alongside any
independent project inspection. Copy the user's task sentence with the meeting
subject and requested outputs verbatim into `QUESTION`, keeping the clauses
together. Omit separate instructions about file edits, tests and formatting.

Proceed with the task once direct cited lines cover the required details.
Use the citations in the result. Check which person, option and decision status
each line supports. If something is missing, search that detail. If the wording
is ambiguous, read `evidence 'ID#SEGMENT' --context 2 --text`, adding `--also`
for other citations in the same call. Report unresolved gaps.

Read [query.md](references/query.md) for exact quotations, cross-recording
follow-ups, workspace comparisons, recording dates and decision history. Read
its search replay instructions when the user asks to watch or save a search.

## Choose a workflow

- To open the memory graph, run `bash "$KITE" view` and give the local URL.
  Use **Workflows** to open committed changes and saved `context-search` evidence, or
  `view --compare FILE` when the user supplies saved snapshots.
- For setup or a demo, run `bash "$KITE" demo --decision-story`. Show the
  changed decision, the later proposal, and the original citation.
- For an authorized import or source update, read [import.md](references/import.md).
- Open the graph during import only when the user asks to see it. Start the
  import workflow, open `view --follow RUN_ID`, then advance its stages.
- For a requested deletion or topic merge, read `agent_guide` from
  `bash "$KITE" resources`. Validate `memory-deletion` or
  `memory-consolidation` and follow its stage skills. If the user wants to watch,
  open `view --follow RUN_ID` before the first stage. Review and approve a
  deletion through the usual preview flow. The graph shows the committed merge
  or the aggregate deletion receipt.
