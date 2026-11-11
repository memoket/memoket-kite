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
An unquoted English word may match an indexed plural or verb form when its
exact form is absent. Cite the original words returned in the evidence.
For several details from the same discussion, keep the related questions
together. KITE can quote a nearby turn that directly mentions a repeated
Chinese clue. Check each original quote for the detail it supports.
When a pack includes multiple recordings, use the cited lines to match
each answer to the right discussion.
Repeat `--term` for alternate phrases in one lookup. Add `--save` to show
per-phrase candidate spans and cited lines in the viewer. A candidate span can
be left out by the quote budget or can match across original line boundaries.
In a pipeline, use `request.terms` or `request.question` and optionally set
`request.matching_lines: true`.
Set `request.explain: true` to retain packing order and inclusion reasons.
For a saved search, the graph opens each candidate at its original source
position and shows the clues that moved a passage ahead of other recordings.
Its list shows up to 80 candidates per question.
If the evidence lists sources with the same transcript text, quote the
original segment ID under the source that supports the answer. Check its
recording clock separately when timing matters.
Open nearby lines with `kite-tool evidence 'CONVERSATION#SEGMENT' --context 2 --text`.
Use `--at-version VERSION` or `--known-at TIMESTAMP` for what the workspace
knew at a historical point. Use recording dates for meeting windows. Keep
historical revision checksums with their quotes.
Add `--from-version BEFORE` to compare the same question with an earlier
tracked version. Both sides search retained original text under the same
filters. Use the version heading and revision checksum with each citation.
Read a highlighted version difference in context before treating it as a
confirmed decision.
For questions about earlier or later discussions, check the recording dates
and the quoted wording for each answer. Keep the sequence uncertain when
the citations leave competing recordings or tied times.
Keep a single request intact when it names the earlier and later phases.
The compiler can record those phases and the search trace links them to
separately selected original lines.
The question can supply the scope as `workspace v12`, `workspace as of TIMESTAMP`,
`工作区 v12`, or `工作区截至 TIMESTAMP`. Include the timestamp's offset.
Quote a topic or project name to search its full phrase first.

When the user wants a saved inspection of compilation and search, use the
`context-search` pipeline with `{"request":{"question":"…"}}`. Inspect the
compiled phrases before searching. For a comparison add `compare_with` to the
request, with optional `compare_start_date` and `compare_end_date`. For a
version comparison set `request.from_version` with an optional target
`request.at_version` or `request.known_at`. The plan is bound to the workspace
version used for compilation.
