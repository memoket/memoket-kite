## Query memory

For changes to a single transcript, run `source-history SOURCE ID` to find
the baseline checksum, then `source-compare BEFORE --text` for changed original
lines. The two revision checksums identify the exact evidence versions.

For what the workspace knew at a given point, use `context --at-version VERSION`
or `context --known-at TIMESTAMP` with the original question. Supply an offset
with the timestamp. Use `--start-date` and `--end-date` for recording dates and
retain the shown revision checksum with historical citations.

Use `bash "$KITE" topics --contains PHRASE` to find indexed topics, then
`bash "$KITE" query --topic TOPIC --text` for a readable trace. Use
`query --pipeline task-tracing` for assignments or `decision-evolution` for
changes to a decision. Use `--end-date YYYY-MM-DD` to answer as of a date.
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
