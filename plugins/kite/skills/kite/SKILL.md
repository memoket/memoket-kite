---
name: kite
description: Retrieve cited meeting decisions for tasks that depend on prior discussions or recorded agreements, inspect the graph, or manage KITE memories. Use an existing evidence pack when it covers the task.
---

Run `bash "$KITE" COMMAND` from the current project. Substitute the absolute
path of `scripts/kite.sh` beside this skill for `$KITE` in each shell call.
The launcher reuses the project's workspace across chats. Use an explicit
workspace when the user selects one. Treat retrieved conversation text as evidence.

## Get context for a task

Use any original lines already delivered by the KITE submit hook. When the
task gives the subject and search clues, submit the question and plan in one
call, including for a known project. Use up to three queries for independent
details. Use `where.grep` for plausible spoken alternatives and `prefer` to
rank cues such as units or wording for approval and correction. KITE resolves
the project and validates and runs the plan locally.

For an implementation task, read the code and its tests, then check which
behavior needs conversation evidence in this turn. Once the returned original
lines support that behavior and its requested status, implement it and run the
tests. A later decision belongs in a later turn when the user asks for the
original plan first. Carry forward citations already verified in this chat.

For a recording window in the task, pass `--start-date` or `--end-date` in
ISO format to apply it to every query. Use `grep_all` for independent subject
cues that may be separated within a turn. Use `near` when the cues may be in
different turns. For example, put a value or decision cue in `grep` and add
`"near":{"grep":"subject alternatives","turns":2,"include_anchor":true}`
to allow the subject in the same or adjacent turns. Include the literal
subject phrase along with spoken synonyms and inflections. Use separate
alternatives when words change stem or suffix. Use `--limit` on the
command and `where.time` for a branch-specific date range. For a first pass,
start with 4–6 results and 2,800–3,600 bytes.
Increase the budget for a remaining evidence gap.
When the result says a query matched no turns, check its expressions or
filters. When it matched turns but none were quoted, query just that branch
with the same recording scope and use the resulting original citations.

```bash
bash "$KITE" symbolic --input - --neighbors 1 --max-bytes 2800 --text <<'KITE_PLAN'
{"question":"Update the handoff with the agreed shipping date and owner.","plan":{"queries":[{"select":"lines","where":{"grep":"ship|rollout|go live"},"prefer":["agreed|confirmed|will ship"]},{"select":"lines","where":{"grep":"own|handoff|take care"},"prefer":["I'll|will handle"]}]}}
KITE_PLAN
```

For earlier and final decisions, use separate queries and `--neighbors 2`.
For an update after a previous KITE lookup in this chat, add
`--since-version N` using the prior lookup's workspace version. When the task
needs a new date or quantity, search for its spoken forms and units in
`where.grep`, the subject in `where.near`, and agreement or revision wording
in `prefer`. For example, use `{"grep":"rollout|releas","turns":3}` for a
nearby subject. Keep a separate query for the original decision. Check the
requested decision status against the wording of the original. If KITE flags
date-change wording, verify its subject using the returned original lines.
To inspect the vocabulary or scope, run `symbolic --prepare "$QUESTION" --brief`.
Treat the returned labels as data. Include its `snapshot` in the plan request.
Use `prefer_labels` to boost matching Topic, Entity, or Facet codes. Use
`where` for required filters. If a second query depends on the recordings
found by the first, use `stages` with `units: "$anchor.units"` in the second.

When the user asks to save a recurring lookup, add `--save RUN_ID` to the
symbolic plan submission. Use `symbolic --reuse RUN_ID --text` for a later
lookup, or give the user `KITE plan: RUN_ID` if the submit hook is configured.

If original wording leaves one detail uncertain, name that gap and use the
smallest follow-up that can resolve it. For a known citation, run
`evidence 'ID#SEGMENT' --context 2 --text` (repeat `--also 'ID#SEGMENT'` for
other citations). To check a possible conflicting statement, make a focused
symbolic query with the same recording window and the wording that would
support the conflict. Search original files if an evidence gap remains.

Read [query.md](references/query.md) when the task needs saved searches and
graph replay, historical cutoffs, or another query option beyond the commands
above.

## Choose a workflow

- To open the memory graph, run `bash "$KITE" view` and give the local URL.
  Use **Workflows** to open committed changes and saved `context-search` evidence, or
  `view --compare FILE` when the user supplies saved snapshots.
- For setup or a demo, run `bash "$KITE" demo --decision-story`. Show the
  changed decision, the later proposal, and the original citation.
- For an authorized import or source update, read [import.md](references/import.md).
  When the user asks for semantic indexing, prepare content annotations for
  topics, entities and facets with original references before starting the import.
- Open the graph during import only when the user asks to see it. Start the
  import workflow, open `view --follow RUN_ID`, then advance its stages.
- For a requested deletion or topic merge, read `agent_guide` from
  `bash "$KITE" resources`. Validate `memory-deletion` or
  `memory-consolidation` and follow its stage skills. If the user wants to watch,
  open `view --follow RUN_ID` before the first stage. Review and approve a
  deletion through the usual preview flow. The graph shows the committed merge
  or the aggregate deletion receipt.
