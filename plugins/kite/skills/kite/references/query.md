## Query memory

For changes to a single transcript, run `source-history SOURCE ID` to find
the baseline checksum, then `source-compare BEFORE --text` for changed original
lines. The two revision checksums identify the exact evidence versions.

For what the workspace knew at a given point, use `context --at-version VERSION`
or `context --known-at TIMESTAMP` with the original question. Supply an offset
with the timestamp. Use `--start-date` and `--end-date` for recording dates and
retain the shown revision checksum with historical citations.
The question itself can include `workspace v12` or `workspace as of TIMESTAMP`.
Use one workspace snapshot per query.

For a recording window such as “the last 30 days,” resolve the dates in the
user's timezone and pass them as inclusive recording bounds. If there are no
matches, check whether the relevant recordings have dates before reporting an
empty period. For an index-only workspace, use `context` for decision changes.
Read the surrounding quotes for approval, explicit effective dates, and later
corrections. Report unknown dates or unresolved conflicts with their citations.
Use times marked timezone unknown for within-day ordering only when the
sources are known to use the same local clock. Keep ties and unknown order
explicit in the answer.
When the user gives an exact quotation and asks for its recording time, use
`context --term QUOTATION --matching-lines --text`. Repeat `--term` to locate
several quotations in one call. With `--question`, **Matching lines** uses its
quoted phrases when present. Open `evidence` for any surrounding turns needed.
Use `--latest` when the task needs newer recordings first. For two independent
questions, run `context --question "$FIRST" --compare-with "$SECOND" --text`.
Restate the subject where needed so each question can retrieve its own quotes.
Both share the snapshot, recording date filters, and total context budget.
Keep related details in one question when they can come from the same passage.
In the graph, **Recording dates** limits search and the result panel groups
passages by their recording date. **Compare** shows both sets of compiled terms.

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
