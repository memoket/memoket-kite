---
name: context-search
description: Retrieve cited original passages from a task in one local call.
---

Run `kite-tool context --question "$QUESTION" --text` with the user's task.
For two independent questions, use `--question "$FIRST" --compare-with "$SECOND"`.
Give each question the subject it refers to. Both use the same recording bounds
and workspace snapshot. Each receives a share of the quote and span budget.
Answer from the cited passages. For one missing detail, search with a specific
`--term` or narrow to an identified meeting with `--conversation-id`.
Open nearby lines with `kite-tool evidence 'CONVERSATION#SEGMENT' --context 2 --text`.
Use `--at-version VERSION` or `--known-at TIMESTAMP` for what the workspace
knew at a historical point. Use recording dates for meeting windows. Keep
historical revision checksums with their quotes.
The question can supply the scope as `workspace v12`, `workspace as of TIMESTAMP`,
`工作区 v12`, or `工作区截至 TIMESTAMP`. Include the timestamp's offset.
Quote a topic or project name to search its full phrase first.

When the user wants a saved inspection of compilation and search, use the
`context-search` pipeline with `{"request":{"question":"…"}}`. Inspect the
compiled phrases before searching. For a comparison add `compare_with` to the
request. The plan is bound to one workspace version.
