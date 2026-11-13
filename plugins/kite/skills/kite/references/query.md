## Query memory

### Fill evidence gaps

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
