---
name: symbolic-search
description: Compile an inspectable KITE query plan and execute it over source annotations and original turns.
---

Use the user's retrieval question and an authorized model. Start
`symbolic-search` with `{"request":{"question":"…","model":"…","neighbors":1}}`.
Run the `compile` stage with `--allow-provider`. It sends the question and
workspace index vocabulary to the configured provider. Inspect the plan and
its recording scope, then run the `search` stage. Read the execution trace
and cited original turns. Report gaps in the evidence. If the workspace
changes before search, start a new run against the current version.
Read `branch_coverage` to distinguish a query with no matching turns, a
dependent stage that could not bind, and a query that matched turns outside
the returned quote pack. Check the wording and filters for an empty branch.
For matches that were not quoted, rerun that branch with its original scope
so a follow-up can concentrate the budget on the missing evidence.
To use a plan generated in the current chat, supply it as
`{"request":{"submitted_plan":{"question":"…","snapshot":"…","plan":{…}}}}`.
Advance `compile` locally without `--allow-provider`. In the CLI,
`symbolic --input PLAN.json --save RUN_ID` runs and stores both stages.
Include `project` in the request to limit both stages to that exact imported
project. Use the project's recorded name.
With no explicit scope, a unique project alias in the question scopes the
query. Inspect `project_alias_scope` in preparation or execution. If it shows
multiple projects, check whether the task spans those projects or needs a
specific one. Do not choose a single project based on an ambiguous alias.
For a recording window shared by all branches, add `recording_dates` to the
request with `start_date`, `end_date`, or both as inclusive ISO dates.
For a query after an earlier search, set `since_version` to the version from
that result. The search reserves evidence for matching changed sources and
keeps the ordinary current search. Recording dates and snapshot versions
describe different things.
For dates, the trace can show a unique change candidate linked to an earlier
dated turn in a retained recording by shared wording and source annotations.
The search checks the changed recordings in the same scope. When recording
dates are known, the retained turn must come from the same or an earlier day.
If the trace marks the changes as ambiguous, look up the relevant recordings
and their original wording. Check whether the speakers approved the change.

For source-line plans, use `where` for required constraints. A query can
also include a `prefer` array of up to four case-insensitive regexes to
prioritize matching turns within that candidate set. The trace reports the
number of candidates matching those ranking expressions.
Use `near` inside `where` to require a matching turn nearby in the same
recording. Supply `grep` and `turns` (1–4) in `near` and a separate text
constraint on the cited turn itself.
Set `include_anchor` to true if that same cited turn can satisfy both patterns.
Use `prefer_labels` for optional Topic, Entity and Facet ranking. Its values
are exact label codes from the compiler vocabulary, grouped by axis. The
trace reports which candidate lines were supported on each axis.
