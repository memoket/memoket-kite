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
If a literal quotation identifies the project, submit `anchor_quote` with
`question` and `plan.queries`. Give each source-line query its own evidence
need. The compile stage expands them into a literal anchor and project-bound
stages. Omit recording filters within those queries.
Include `project` in the request to limit both stages to that exact imported
project. Use the project's recorded name.
With no explicit scope, a unique project alias in the question scopes the
query. Inspect `project_alias_scope` in preparation or execution. If it shows
multiple projects, check whether the task spans those projects or needs a
specific one. Do not choose a single project based on an ambiguous alias.
For a recording window shared by all branches, add `recording_dates` to the
request with `start_date`, `end_date`, or both as inclusive ISO dates.
Preparation can check literal user quotations against original turns in this
scope. It exposes the pattern and recording counts to the compiler. Use the
pattern to anchor a dependent project lookup when the question identifies
a project by that quotation and all matching recordings share the project.
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
If the result supplies two date-comparison evidence IDs, pass the first ID
to `evidence` and the second with `--also` to read both original turns in
one call. The IDs refer to the revisions used by the search.

For source-line plans, use `where` for required constraints. A query can
also include a `prefer` array of up to four case-insensitive regexes to
prioritize matching turns within that candidate set. The trace reports the
number of candidates matching those ranking expressions.
When a question asks about an approved decision or a final review, the
executed plan can add a preference for decision wording in matching text
stages bound to an earlier stage. Read the original turns for their status
and context.
For a scoped final-decision query, a matched subject can lead to nearby
turns on a quantity change. The `revision_exchange` trace links the subject,
revision wording, reply, and reported adjustment within one recording.
This also applies to a stage bound to a uniquely matched project's recordings.
Read those turns in order for the scope and status of the change.
For date questions, a parallel plan with no calendar wording in its first
set of quotes can give space to up to two already matched turns with dates.
The `calendar_alternatives` trace lists those original turns. Check which
date the speakers approved, proposed, or reported.
For a question asking for both a previous and current date, KITE can
retain an already matched change turn alongside a related, quoted turn
from an earlier recording. Look for `dated_revision` and its topic
wording or annotations in the trace. The text names the two related
references. Verify the decision status of both and check that additional
dated turns concern the same subject before using them.
Staged line searches advance past shared hits to give each stage a distinct
candidate within the quote limit. A project-bound final-decision stage can
include an alternate decision wording when the stage already uses all four
ranking patterns. Inspect `alternate_wording_line` in the stage trace.
Use `near` inside `where` to require a matching turn nearby in the same
recording. Supply `grep` and `turns` (1–4) in `near` and a separate text
constraint on the cited turn itself.
Set `include_anchor` to true if that same cited turn can satisfy both patterns.
Use `prefer_labels` for optional Topic, Entity and Facet ranking. Its values
are exact label codes from the compiler vocabulary, grouped by axis. The
trace reports which candidate lines were supported on each axis. A matched
`near` turn can support a label. `nearby_hits` counts candidates with label
support on the matched nearby turn alone.
To continue across meetings in the project that contains a distinctive clue,
use `where.units: "$anchor.project_units"` in a later named stage. The anchor
must match recordings assigned to one project. The trace reports the resolved
project, competing projects, or missing project assignments. The join respects
the requested recording date range.
In a submitted source-line plan, a single `unit` or `units` beside `where`
is moved into `where` before validation. Plans with competing recording
filters require an explicit choice.
