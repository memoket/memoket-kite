---
name: context-search
description: Retrieve cited original passages from a task in one local call.
---

Run `kite-tool context --question "$QUESTION" --text` with the user's task.
For two independent questions, use `--question "$FIRST" --compare-with "$SECOND"`.
Give each question the subject it refers to. Both use one workspace snapshot
and share the quote and span budget. If the questions refer to different
recording windows, set `--start-date` and `--end-date` for the first and
override either bound for the second with `--compare-start-date` and
`--compare-end-date`. Keep stated event dates and decision status with the
original quotes when tracing what changed.
If one recording dominates the leading candidates of a default comparison,
KITE can quote one more passage within the same byte budget. An explicit
`--limit` fixes the span limit.
Answer from the cited passages. For one missing detail, search with a specific
`--term` or narrow to an identified meeting with `--conversation-id`.
For an exact quotation or its recording time, add `--matching-lines` to
`--term`. With a quoted question, this option searches the quoted phrases.
Repeat `--term` for alternate phrases in one lookup. Add `--save` to show
per-phrase candidate spans and cited lines in the viewer. A candidate span can
be left out by the quote budget or can match across original line boundaries.
In a pipeline, use `request.terms` or `request.question` and optionally set
`request.matching_lines: true`.
Set `request.explain: true` to retain packing order and inclusion reasons.
For a saved search, the graph opens each candidate at its original source
position and shows the clues that moved a passage ahead of other recordings.
Its list shows up to 80 candidates per question.
Open nearby lines with `kite-tool evidence 'CONVERSATION#SEGMENT' --context 2 --text`.
Use `--at-version VERSION` or `--known-at TIMESTAMP` for what the workspace
knew at a historical point. Use recording dates for meeting windows. Keep
historical revision checksums with their quotes.
For questions about earlier or later discussions, check the recording dates
and the quoted wording for each answer. Keep the sequence uncertain when
the citations leave competing recordings or tied times.
The question can supply the scope as `workspace v12`, `workspace as of TIMESTAMP`,
`工作区 v12`, or `工作区截至 TIMESTAMP`. Include the timestamp's offset.
Quote a topic or project name to search its full phrase first.

When the user wants a saved inspection of compilation and search, use the
`context-search` pipeline with `{"request":{"question":"…"}}`. Inspect the
compiled phrases before searching. For a comparison add `compare_with` to the
request, with optional `compare_start_date` and `compare_end_date`. The plan is
bound to one workspace version.
