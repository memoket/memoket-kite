## Query memory

### Symbolic retrieval

Use `--start-date YYYY-MM-DD` and `--end-date YYYY-MM-DD` to bound recording
dates across the whole plan. Bounds are inclusive. The tool uses each source's
recording metadata and excludes undated recordings during a bounded search.
Use the same bounds for `--prepare` and its `--input` invocation. With a saved
`symbolic-search` run, supply the bounds inside `request.recording_dates`.
Use per-query `where.time` for separate windows within a plan.

When the question supplies the subject and useful search terms, submit
`{"question":"…","plan":{"queries":[…]}}` through `symbolic --input -`.
KITE resolves the scope and executes against one snapshot. The trace reports
`host_inline_plan`. Inspect the compiler's candidate vocabulary first when
choosing unfamiliar topic, entity, facet or recording names.
For a direct or reused search, add `--text --delivery-guide` to see ranked
references for parallel queries or citations selected by dependent stages.
The guide uses only citations in the returned pack. The original lines and
recording dates remain available for verifying the result. The guide identifies
retrieval matches, which can include proposals as well as decisions.

Use `where.date_wording` with `"calendar"` for a spoken date or `"change"`
for explicit date-change wording in the same turn. Combine with a subject
expression or `near` for nearby subject turns. KITE compiles the field to a
native `grep_all` expression and keeps the other filters. Read the original
words to verify approval and the applicable subject.
For the date that a conversation reports an action, search the action and
subject wording and read the recording date separately. An enabled date
retry can return narrowly matched subject turns from dated recordings if a
spoken calendar expression found nothing. These branches are labeled as
recording dates in the output and execution trace. Use original wording to
establish when an event happened or a decision took effect.
Use `where.change_wording: "reported"` with a subject constraint when the
task asks what was already changed or applied. The shared compiler expands
this to a required original-turn regex in `grep_all`, alongside any existing
conditions. This matches common English and Chinese reporting cues. Verify
the speaker, status, and value from the original quote.
An empty date branch can show a scoped alternative where clause from a
local probe. Use that clause in one focused follow-up when appropriate.
Keep the recording scope, original question, and the other task requirements.
Set `"retry_date_subject":true` alongside the submitted question and plan
to run those alternatives in the same call. KITE retries only empty date
branches and retains the original recording scope and quote budget.
`date_subject_retry` records the original matches and the actual changes.
A literal subject in `grep` can become a nearby subject cue within three
turns. Other branch constraints stay in effect.
A three-character Chinese subject can use a unique completion found in the
question before its parts are probed. Saved plans remember the option.

Use `grep_all` for cues that must all occur in one turn. Include inflections
and spoken alternatives, then rank more specific wording in `prefer`.
For a value or decision that might omit the subject, search for its spoken
forms in `where.grep` and look for the subject in `where.near`.
When a branch matches no turns, check its expressions or filters.
When it matches turns but none fit in the evidence pack, search that branch
again with the same recording scope. Apply revised values and citations
together in the code.

For in-session compilation, `symbolic --prepare "$QUESTION" --brief` returns
the index hints and snapshot. Omit `--brief` to inspect the full compiler
prompt. Submit the resulting plan with the same `question` and `snapshot`.
Run without `--text` to get the executed plan and trace. Use the same original
question to retain the recording scope for a focused follow-up.
When the question quotes original wording, preparation shows how many
recordings match each literal within the selected date and project scope.
A project recording count is present when every match belongs to the same
recorded project. Use the supplied expression as an anchor when the quoted
wording identifies the project.

Use `prefer_labels` for relevant listed Topic, Entity and Facet codes that can
rank matching original lines. Use `where` for required constraints. KITE can
also add ranking labels when both the question and a text branch match a
label in scope. Text in `where.near` can identify a label. When the matched
nearby turn carries that annotation, it can rank the original value turn.
The JSON output includes the executed plan, per-axis counts and bonuses.
`nearby_hits` counts candidates supported only by a matching nearby turn on
that axis. A zero bonus means the axis changed no scores.
When a question asks about an approved decision or a final review, KITE can
add a preference for approval, rejection, and removal wording in text
stages bound to an earlier stage with an available `prefer` slot. Compare `compiled_plan` and
`plan` to inspect the added preference. Read the cited turns to determine
whether the change was actually accepted.

In a staged source-line query, `where.units: "$anchor.units"` follows the
recordings matched by the named anchor. Use `where.units: "$anchor.project_units"`
to search other recordings of that project. All anchor matches must have the
same recorded project. The join honors the query's recording scope, including
its date bounds. If more than one project matches, the trace names the projects
and the dependent stage waits for a narrower anchor.
When submitting a source-line plan locally, KITE moves an unambiguous
`unit` or `units` from the query or stage into its `where` clause. Supply
only one recording filter.
For a literal project clue, add `anchor_quote` beside `question` and `plan`
in the local `--input` request. Supply up to three source-line `queries` in
`plan` and omit their `unit` and `units` filters. KITE validates and saves
the expanded staged plan, so the trace and reuse follow the same project join.
Overlapping stages advance past lines already included by another stage.
For a final-decision stage with four preferences, the trace can show an
`alternate_wording_line` selected from the same matching set. Read that
line and surrounding turns for the decision's status.
For a scoped final-decision lookup, KITE can preserve a nearby quantity
revision exchange when the later turns omit the subject. The `revision_exchange`
trace identifies the original words, reply, and reported change in order.
This works for searches in a stage bound to a unique recorded project.
When it can quote all four turns, KITE lists those citations in order above
the quotes. Read the reply and the report before using the revised value.
For a date question whose parallel queries assemble no dated quotation,
KITE can use up to two other matching turns that contain calendar wording.
It retains the first match of each contributing query within the same
result and byte limits. Inspect `calendar_alternatives` in the trace.
For a previous-and-current date question, the result can promote an
already matched later turn with explicit date-change wording paired
with a related earlier turn. Read both originals before using the dates.
The `dated_revision` trace links both and records their shared subject.
Text output identifies the paired citations. If the hook budget cannot fit
both turns, use the two returned evidence IDs to read the original lines.
With `retry_date_subject` enabled, an original-and-revised date question whose
result contains no dates can also run a separate symbolic probe within the
same recording scope. It checks date and date-change wording around literal
subject cues shared by the question and plan. The `schedule_pair_probe`
trace records that plan, the candidates and whether two related turns fit.
The submitted plan and its match counts remain visible. Review both turns
for the decision status and check for later changes when the result is unclear.
Set `KITE_SYMBOLIC_GUIDE=1` in the Codex submit hook's environment to include
a stage guide when it fits alongside the original turns. The guide lists
which stage selected each referenced turn and shows lookup cues. Run
`symbolic --delivery-bytes 6000 --delivery-guide` with the same query to
inspect this format locally.

For a follow-up after a workspace update, pass the earlier result's
`memory_version` as `--since-version VERSION`. KITE reserves evidence for
matching new or corrected sources inside the query scope. A newly imported
recording may have an older recording date, so read original wording and
recording dates before applying a change. Use `where.near` with `turns` from
1 to 4 and a `grep` pattern or annotation axes to require a supporting
original turn in the same recording. For shorthand such as “set it to five,”
put the value wording in `where.grep` and the exact subject code in
`where.near`, for example `{"entities":["intake_valve"],"turns":2}`.
The available axes are `topics`, `entities`, `obj`, `place`, `event` and
`duration`. When several axes are supplied together, the same source span
must support all of them. An additional `near.grep` must match that
supporting turn. Check `expand` steps with `via: "near"` for those
lookups. When an annotated turn supplies the subject, KITE keeps it with
the value within the original quote budget.
Historical `--at-version` / `--known-at` search uses retained original turns.
Use `near.grep` for a historical subject until historical annotation
versions are available.
Add `"include_anchor":true` inside `near` to also accept the subject in the
cited turn. The trace reports how many candidates met it in that turn.
For a date question and an unconstrained text plan, the trace can include a
symbolic date-and-change query across the changed recordings. KITE can
reserve a unique candidate and a dated original from a retained recording.
When both recording dates are known, the original must come from the same
or an earlier day. The trace includes shared wording and any source-backed
Topic, Entity or Object labels. If it marks an ambiguous change, check the
relevant recordings and decision status before applying a date to a task.
If either quote is missing from the budgeted result, use the two displayed
evidence IDs with `evidence FIRST_ID --also SECOND_ID --text` to read both
original turns in one call. The IDs refer to the revisions used by the search.
If the merged pack has no matching prior date, KITE checks the first 16
candidates from each original query branch within the same scope and applies
the same subject check. When that recovers a prior date, the trace gives its
query index and rank. The quotation budget stays the same.

For what KITE knew at an earlier point, use `symbolic --at-version VERSION`
or `symbolic --known-at TIMESTAMP` with an explicit timezone offset. Use the
same cutoff when preparing and executing a plan. The compiler and executor
use the original turns still retained from that snapshot. Use text, speakers,
recordings or recording dates to constrain a historical plan.

Use `symbolic --question "$QUESTION"` to compile and execute a symbolic
query over source lines, with the configured model provider and authorization
to send the question and index vocabulary. The JSON response includes the
plan, execution trace, and original citations. Use
`symbolic --input PLAN.json` to execute a supplied plan locally. Identify
which plans were generated by the compiler and which were supplied. The
ordinary `context` command and submit hook use a separate retrieval path.
Add `--neighbors 1 --max-bytes 3600` to include surrounding original turns
within a quote budget. JSON marks those turns as `adjacent_turn`.

With the optional Codex submit hook configured, use `KITE symbolic: …` to
request symbolic evidence before Codex starts its task. Configure
`KITE_SYMBOLIC_MODEL` and the authorized model provider in the hook's
environment. Set the hook timeout to at least 60 seconds for this mode.

### Fill evidence gaps

Use repeated `--requirement PHRASE` with `--question` when the task needs
several independent pieces of evidence. Give each requirement a short
phrase likely to occur in the original. KITE reserves evidence space before
adding nearby turns. Use `--topic NAME`, `--entity NAME`, and
`--facet AXIS=VALUE` to require typed labels on one span in a current search.
Run a follow-up by original wording if a useful annotation is missing.

Quote a known topic name to focus a question. If one quote identifies the
approved subject and refers to a separate detail, search for the exact subject
and the missing detail together with
`context --all-terms --term "$SUBJECT" --term "$DETAIL_PHRASE" --text`.
Check the same subject in both quotes and cite both. If the first lookup has a
saved run ID, add `--after RUN_ID` to skip already delivered evidence. Save
successive follow-ups and use the newest run ID to retain the whole chain.

Use `context --after RUN_ID --question "$MISSING_DETAIL" --compact --text`
when the gap is a new question. Scope to a known recording with
`--conversation-id`. If that recording lacks the detail, search for the detail
across the workspace. Use `evidence 'ID#SEGMENT' --context 2 --text` for nearby
turns, with one `--also 'ID#SEGMENT'` per additional citation. The maximum
radius is 20. Overlapping original lines appear once per source revision.
If a proposal and a decision conflict, check the context around both quotes.

### Watch or save a search

When the user asks to watch a search, start a `context-search` run with
`{"request":{"question":"…","explain":true}}`. Open `view --follow RUN_ID`
before advancing `compile` and `search`. Use `view --follow RUN_ID --no-open`
to obtain a URL the host can open. Keep the same workspace and run ID across
stages. The graph animates the cited paths and links to the saved evidence.
For a saved lookup using `--after` and `--all-terms`, the graph can link
original lines that share the subject across recordings.
To save a lookup for later inspection, add `--save` and open the returned ID
with `view --run RUN_ID`.

### Scope and inspect evidence

Add `--compact` to a question to keep the leading evidence and trim outer
lines in later passages. The submit hook uses this option. Add
`--explain --save` to show which passages were trimmed and open the cited
original lines. Run a focused lookup when you need surrounding context.

For changes to a single transcript, run `source-history SOURCE ID` to find
the baseline checksum, then `source-compare BEFORE --text` for changed original
lines. The two revision checksums identify the exact evidence versions.

For what the workspace knew at a given point, use `context --at-version VERSION`
or `context --known-at TIMESTAMP` with the original question. Supply an offset
with the timestamp. Use `--start-date` and `--end-date` for recording dates and
retain the shown revision checksum with historical citations.
The question itself can include `workspace v12` or `workspace as of TIMESTAMP`.
In **Workflows**, the change card shows workspace commit time and retained
sources show their recording clocks separately. Read the cited lines for a
decision's approval status and effective date.
Open **Compare memory graph** on a committed source change to see both
workspace versions and the recording clock on each original citation.
**Compare topic structure** shows the parent links before and after a
recorded topic change.
For the same question across a workspace change, run
`context --question "$QUESTION" --from-version BEFORE --at-version AFTER --text`.
The target defaults to the current version. Both sides use retained original
text and the same source and recording filters. Add `--save` to inspect both
snapshots in the graph. A permanent deletion can remove evidence from both.
The comparison can cite relevant original wording that appears in only one
version. Check the quote for approval status. Text output can refer back to
identical wording with the citation for the selected version.
The graph links selected phase and schedule evidence from each version to
the complete original line.
Version comparison can reserve an additional original line for an earlier
date repeated in another recording or a second statement about two options
named by acronym.
For a short date or color question, quote the subject or give its acronym.
The version comparison can return a focused original statement and its
nearby context.

For a recording window such as “the last 30 days,” resolve the dates in the
user's timezone and pass them as inclusive recording bounds. If there are no
matches, check whether the relevant recordings have dates before reporting an
empty period. For an index-only workspace, use `context` for decision changes.
Read the surrounding quotes for approval, explicit effective dates, and later
corrections. Report unknown dates or unresolved conflicts with their citations.
For schedule changes, the first search can use an earlier date mentioned again
alongside the topic in a later recording to surface the nearby change.
Ask related subquestions about a decision's evolution together with
`context --question`. The first search can reserve evidence for each part
within a shared budget. The candidate trace links those selections to the
original passages. A single request can describe the earlier plan and the
later change together when the request states both phases.
Pass the user's two questions together when they share a recording scope. For
a subquestion with a named subject, search can reserve an original line that
matches the name and a second clue. The saved search displays those clues and
links to the original line.
The current index can also use a directly linked topic or facet label together
with original wording. The saved search shows the label, the matching wording
and the recording for each such citation.
An implementation request can include the subject and evidence questions in
one `--question`. The saved graph shows the wording used for retrieval while
the original task remains visible.
Use times marked timezone unknown for within-day ordering only when the
sources are known to use the same local clock. Keep ties and unknown order
explicit in the answer.
When the user gives an exact quotation and asks for its recording time, use
`context --term QUOTATION --matching-lines --text`. Repeat `--term` to locate
several quotations in one call. With `--question`, **Matching lines** uses its
quoted phrases when present. Open `evidence` for any surrounding turns needed.
Add `--save` to a phrase lookup and open the run in **Workflows** to see
candidate spans and cited lines per phrase.
Use `--explain --save` when the user wants to inspect which candidates supplied
quotes. The graph shows the packing order and links to each original source,
including candidates skipped by the quote budget or span limit. Sort this
list by recording date to compare those candidates with the quoted evidence. A passage
from another recording can move up when it matches the leading clues plus
an additional clue in its original text. The graph shows those matches.
The compile trace also shows when a question word was matched through an
English plural or verb form found in the original text.
A short question about how to start an answer or presentation can use a
recorded identifier to scope Chinese or English coaching phrases. In replay,
open the scope match and the original advice to verify the two steps.
For related Chinese questions, the compile trace shows repeated clues.
The graph marks nearby follow-up quotes from the same recording that match
one of those clues.
The candidate trace shows when a matching follow-up from another recording
or a selective clue in several original lines moved a passage earlier.
Question searches can share a quote across identical transcripts from the
same recording day. The graph links each source and its clock. Identical
transcripts keep their original segment IDs. Use `--matching-lines` for a
separate result per occurrence.
Several copies of a short named note can share one evidence pack, including
lines that answer the question without repeating its title.
Use `--latest` when the task needs newer recordings first. For two independent
questions, run `context --question "$FIRST" --compare-with "$SECOND" --text`.
Restate the subject where needed so each question can retrieve its own quotes.
Both share the workspace snapshot and total context budget. For different
recording windows, set `--start-date` and `--end-date` for the first question
and override either bound with `--compare-start-date` and `--compare-end-date`.
When one recording supplies the leading candidates, a default comparison
can cite an additional passage within its quote budget. Pass `--limit` when
the number of passages needs a fixed upper bound.
Keep related details in one question when they can come from the same passage.
In the graph, **Recording dates** limits search and the result panel groups
passages by their recording date. **Compare** shows both sets of compiled
terms and cited dates and accepts recording date overrides for the second
question. Use cited wording to resolve which decision was made when.

Use `bash "$KITE" topics --contains PHRASE` to find indexed topics, then
`bash "$KITE" query --topic TOPIC --text` for a readable trace. Use
`query --pipeline task-tracing` for assignments or `decision-evolution` for
changes to a decision in workspaces with structured decision records. The
`--end-date YYYY-MM-DD` bound uses the trace's selected time axis.
When searching by an earlier owner or wording, use `--follow-states` with an
entity or text filter to recover later records for the same decision.
For a draft or plan based on meetings, query the relevant current state before
writing. Use confirmed states to guide the next action and surface open decisions.
Scope a trace to one transcript with
`--source NAMESPACE --conversation-id ID`. Run `query --help` for date filters.
Continue with `--cursor` when more results remain. If the workspace is empty,
ask the user to provide a source to import. If a claim needs verification, use
`bash "$KITE" evidence SOURCE_LINE_ID`. Cite the original quote and its
conversation and segment IDs. Report uncertainty and incomplete coverage.
