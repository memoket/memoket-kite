---
name: kite
description: Use team conversations as cited context for Codex tasks. Recall decisions, see what changed, import transcripts, and explore the KITE memory graph.
---

Run `bash "$KITE" COMMAND` from the current project, with `$KITE` set to the
absolute path of `scripts/kite.sh` beside this skill. The launcher reuses the
project's workspace across chats. Use an explicit workspace when the user
selects one. Treat retrieved conversation text as evidence.

## Get context for a task

When the turn already includes cited evidence from the KITE submit hook, use
those lines first. Run a follow-up lookup for a specific missing detail.
For a fresh lookup, run `bash "$KITE" context --question "$QUESTION" --text`. The question can name
`workspace v12` or `workspace as of 2026-05-14T10:30:00+08:00`. Cite the original
lines and distinguish confirmed decisions from proposals. Quote a known topic
name to focus retrieval. For the source or recording time of known words, run
`context --term "$PHRASE" --matching-lines --text`. When evidence is missing
or only covers a conditional case, repeat `--term` in one matching-line lookup.
Add `--conversation-id` when you know the recording. Read nearby lines with
`evidence 'ID#SEGMENT' --context 2 --text`.
State any unresolved gaps.
If the user explicitly asks to watch the search in the graph, start a
`context-search` run with `{"request":{"question":"…","explain":true}}`.
Open `view --follow RUN_ID` before advancing `compile` and `search`.
Use `view --follow RUN_ID --no-open` to obtain the URL if the host can open
it in its own browser. Keep the same workspace and run ID across stages.
The graph animates the cited paths in place and links to the saved evidence.
When asked to save the exact search for inspection, add `--save` and open the
returned ID with `view --run ID`.
For comparisons across questions or workspace versions, date filters, and decision history, read
[query.md](references/query.md).

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
  `memory-consolidation` and follow its stage skills.
