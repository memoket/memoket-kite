---
name: kite
description: Retrieve cited meeting decisions for tasks that depend on prior discussions or agreements, inspect the graph, or manage KITE memories. If recall_conversation_evidence is available, call it directly for needed quotes. Use an existing evidence pack when it covers the task.
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

For a previous and latest confirmed amount or limit, use separate queries.
Set `where.change_wording: "reported"` on the current branch when the task
calls for a value already put into effect. For example,
`{"grep":"inspection threshold","change_wording":"reported"}`. Keep the
subject constraint and verify the value and status in the quote. If this
branch misses, check the same scope for other wording that establishes it.

For separate earlier and revised dates, include `"retry_date_subject":true`
alongside `"question"` and `"plan"`. If a date branch has no matches, KITE
can retry it with a checked subject cue in the same recording scope. Use
`where.date_wording: "calendar"` with a narrow subject for the earlier date.
Use `where.date_wording: "change"` with nearby subject or action words for
the revised date. For example, date wording can be on a turn with, or
next to, a phrase about inspection: `{"date_wording":"change","near":
{"grep":"inspection|inspect","turns":3,"include_anchor":true}}`.
Split compound phrases into short spoken cues. If an acronym may touch
Chinese text, use an ASCII boundary such as `(?a:\bQA\b)`. Check which
original turns express a proposal or an approved change.

If a KITE result says `Date comparison needs original turns`, use its two
evidence IDs with `bash "$KITE" evidence FIRST_ID --also SECOND_ID --text`.
Read both original turns before applying the revised date to the task.

For an implementation task, read the code and its tests, then check which
behavior needs conversation evidence in this turn. Once the returned original
lines support that behavior and its requested status, implement it and run the
tests. A later decision belongs in a later turn when the user asks for the
original plan first. Carry forward citations already verified in this chat.
Use the code's fields and the task to give each independent decision or
value its own query. Running tests and editing files are actions to perform
after lookup. Include ordinary spoken words in `grep` and put narrower
status or value cues in `prefer`. For example, use `price|prices|selling`
to admit candidates and `suggest|could sell|per unit` to rank them.

If a quotation identifies the project, let KITE resolve the scope before
opening original files. Give the literal `anchor_quote`
alongside the question and up to three queries. The launcher escapes the
quotation and builds the anchor and dependent stages using the recorded
project. Leave `unit` and `units` out of the queries. This runs in one
local call. Example:

```bash
bash "$KITE" symbolic --input - --limit 8 --neighbors 1 --max-bytes 3600 --text <<'KITE_PLAN'
{"question":"Update the handoff for the project whose meeting mentions a folding hinge.","anchor_quote":"folding hinge","plan":{"queries":[{"select":"lines","where":{"grep":"ship|rollout|go live"},"prefer":["agreed|confirmed|will ship"]},{"select":"lines","where":{"grep":"own|handoff|take care"},"prefer":["I'll|will handle"]}]}}
KITE_PLAN
```

For a separately requested date window, add the recording date flags to
the same command. A missing or ambiguous anchor leaves the dependent
queries unresolved.

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
If KITE returns a scoped alternative where clause, try it in one focused
query with the same recording scope. When a query matched no turns, check
its expressions or filters. When it matched turns but none were quoted, query just that branch
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
For a task spanning other recordings of the same project, use
`anchor_quote` for a literal clue or `units: "$anchor.project_units"`
after a stage with a custom anchor expression. All matching anchor
recordings must belong to a single recorded project. If several projects
match, narrow the anchor or ask which one.

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
