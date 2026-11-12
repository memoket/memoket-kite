---
name: kite
description: Use team conversations as cited context for Codex tasks. Recall decisions, see what changed, import transcripts, and explore the KITE memory graph.
---

Run `bash "$KITE" COMMAND` from the current project. Substitute the absolute
path of `scripts/kite.sh` beside this skill for `$KITE` in each shell call.
The launcher reuses the project's workspace across chats. Use an explicit
workspace when the user selects one. Treat retrieved conversation text as evidence.

## Get context for a task

When the turn already includes cited evidence from the KITE submit hook, use
those lines first. Run a follow-up lookup for a specific missing detail.
For a fresh task, use the user's wording about the meeting and the details
the task needs. Keep that wording together and leave separate instructions
about editing files, running tests, or formatting the answer out of `QUESTION`.
Start with `bash "$KITE" context --question "$QUESTION" --compact --text`.
Use the cited lines to complete the task when they cover the requested details.
Open nearby lines when the wording is ambiguous or a detail is missing. A request to
keep the original schedule and its later changes can retrieve both in one call.
The question can name
`workspace v12` or `workspace as of 2026-05-14T10:30:00+08:00`. Cite the original
lines and distinguish confirmed decisions from proposals. Quote a known topic
name to focus retrieval. Keep each finding tied to the named person, provider
or project, especially when one passage describes several options.
If one quote identifies the approved subject and refers to a separate detail,
search for that exact subject and a phrase about the missing detail together.
Use `context --all-terms --term "$SUBJECT" --term "$DETAIL_PHRASE" --text`.
Check the same subject in the second quote and cite both lines. If a recorded
proposal and a final decision conflict, check the wording around each quote.
For the source or recording time of known words, run
`context --term "$PHRASE" --matching-lines --text`. When evidence is missing
or only covers a conditional case, repeat `--term` in one matching-line lookup.
Add `--conversation-id` when you know the recording. If it lacks the detail,
return to the workspace-wide search for that detail before expanding the same
recording again. Use `evidence 'ID#SEGMENT' --context 2 --text` to check a quote.
For several quotes, use one call with `--also 'ID#SEGMENT'` per extra quote.
Use citation IDs from a search or source listing. Overlapping original lines
appear once. Start with nearby turns and expand only when the missing detail
requires it. The maximum radius is 20.
State any unresolved gaps.
If the first lookup returned a saved run ID, supplement it with
`context --after RUN_ID --question "$MISSING_DETAIL" --compact --text`.
Ask for one concrete missing detail. Reuse any relevant citations from the
earlier evidence. If you need another supplement, save the current one and
use its run ID so later lookups skip evidence across the whole chain.
For a saved lookup using `--after` and `--all-terms`, the graph can link the
original lines that share the subject phrase across recordings.
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
  `memory-consolidation` and follow its stage skills. If the user wants to watch,
  open `view --follow RUN_ID` before the first stage. Review and approve a
  deletion through the usual preview flow. The graph shows the committed merge
  or the aggregate deletion receipt.
