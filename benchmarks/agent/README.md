# Agent task benchmark

This suite measures whether a host can select and complete a KITE workflow and
return the correct conclusions with original conversation and segment IDs. The
fixed corpus is synthetic. It includes an unrelated conversation, an owner
handoff, an undated source and a proposal that was never confirmed.

Install the `agent` extra and run these commands from the KITE checkout.

## Deterministic verification

```sh
python -m benchmarks.agent offline
```

The runner starts fresh CLI processes against a temporary workspace. It
exercises four trace workflows, a cited owner correction, a duplicate import,
recovery from a failed stage and approval-bound deletion. It checks which
sources appear in each report, including the exclusion of undated records from
the dated timeline. The Memoket recovery case starts from a raw export, fails at
the missing annotation, and verifies a successful retry and exact citation.
The recovery run is older than the default run list and must be located
among newer maintenance runs.
The approved deletion case checks the deletion receipt, the surviving source
inventory, invalidation of the prior report, and citations from a new trace.
It verifies all surviving facts against the raw fixture, including facts outside
the queried topic. The offline run performs the same check before deletion.
The `untrusted-transcript` case checks that an instruction-like transcript
excerpt is preserved verbatim and the supported decision remains citable.
The `incremental_update` result uses its own fixed [fixture](update.json). It
amends a conversation using its current checksum, checks the updated state and
both revisions, and reads the old line by its revision checksum. It also checks
that replay returns the same receipt, a distinct update with a stale checksum is
rejected, and the index and evidence still match all expected sources. The
result records the update fixture digest and the pass or failure of each check.
The runner exits nonzero if any case or lifecycle check fails.

## Evaluate a host agent

Use a standalone KITE installation on the host's PATH. The wheel contains the
runtime instructions and skills. Keep the benchmark checkout and answer key
outside the task directory.

```sh
trial="$(mktemp -d)"
python -m benchmarks.agent prepare format-decision "$trial/task"
```

Start the host in `$trial/task` and ask it to complete `TASK.md` and return the
specified JSON. The task directory contains a seeded synthetic workspace and
`ANSWER.schema.json` for hosts that support structured output. The host can
discover the installed operating guide and skills through `kite-tool resources`.
Save the host's JSON response to `$trial/task/answer.json`. Score it from the
checkout with the same case name.

```sh
python -m benchmarks.agent score format-decision "$trial/task" "$trial/task/answer.json"
```

The other read-only cases are `owner-handoff`, `dated-timeline`,
`unconfirmed-email`, and `untrusted-transcript`. The last case scores the
host's answer and verifies that the transcript with the instruction-like text
was present in its saved report. Prepare a fresh trial when the fixture digest
changes. Use `memoket-resume` for the recovery task with the same
`prepare` and `score` commands. The scorer verifies the original ingestion
checkpoint, one committed source revision, the report's exact source line,
and that the newer maintenance runs remain untouched.
Use `approved-deletion` to test deletion in a disposable synthetic workspace.
Its prepared task includes an authorization file for one exact preview. The
host verifies that preview before applying it and then traces the surviving
evidence. Each trial needs a fresh workspace.
Use a fresh directory per trial. Record the benchmark commit, host version,
model and configuration alongside results when comparing runs. The scorer reads
the actual checkpoint and report through KITE, verifies that the task used the
expected pipeline at the seeded snapshot, then checks the returned values and
citations against the answer key. It checks source coverage and any required
date bounds in the saved trace.
A correct string with a missing or unrelated citation fails.
The scorer checks the returned timeline against the fixed
[trace expectations](trace-expectations.json). Each group selects one or more
original annotations by source namespace, conversation ID and one-based position.
Groups appear in expected chronological order, with no required order within a
group. The scorer uses the fixture's fact text and original cited lines for the
comparison. `trace_expectations_sha256` identifies the selections and order.
It also verifies that the saved report's decision and task states support the
expected claims. Recovery checks original citations and the confirmed state
against its own fixture and allows the host to phrase the annotation in its own words.

## Compare the user prompt with a pasted transcript

Prepare paired prompts for a read-only case.

```sh
trial="$(mktemp -d)"
python -m benchmarks.agent prepare-context format-decision "$trial/comparison"
```

The `kite` directory contains a seeded workspace and a task prompt. The
`pasted` directory contains the same question and the full synthetic corpus
with original text, source dates and citation IDs. Run the same host and model
in fresh sessions from the respective directories. Submit each `TASK.md`
verbatim as the user prompt and use the corresponding `ANSWER.schema.json`
for structured output. The pasted run should answer using its prompt alone.
Save each response as `answer.json` alongside its prompt.

```sh
python -m benchmarks.agent score-context format-decision "$trial/comparison"
```

The scorer checks the prompts against the fixture and records their SHA-256
digests. It scores both answers against the same claims and citation
requirements. The KITE arm
also verifies the saved workflow and evidence. `user_prompt_context_reduction`
is the difference in UTF-8 prompt bytes divided by the pasted prompt's bytes,
and is `null` when either answer fails. This measures the user message. Host
instructions, tool results and total model tokens have their own accounting.
Report the host and model alongside the paired result.

## Read the results

- `supported_claims / expected_claims` measures answer coverage. An unexpected
  key or an incorrect submitted value or citation counts as an unsupported claim.
- `unsupported_claim_rate` divides unsupported claims by all submitted claims.
- `expected_citations_found` counts satisfied evidence requirements. Timeline
  dates accept any cited segment from the matching date.
- `evidence_precision` divides satisfied requirements by all submitted citation
  entries, including duplicates, malformed entries and citations on unexpected
  claims. `evidence_recall` divides by all expected requirements. Each distinct
  citation in the saved report can satisfy at most one requirement per claim.
  A rate with a zero denominator is `null`.
- `source_scope_correct` checks the report for missing or extraneous source
  conversations, omitted undated records, and truncation. `date_scope_correct`
  checks the saved date bounds and time axis when a task specifies a range.
- `trace_fact_recall` divides matched timeline facts by expected facts.
  `trace_fact_precision` divides them by returned facts. The comparison includes
  the original source identities, segment IDs and quote text. A repeated row
  adds to the returned count. `trace_facts_exact` requires the full expected set
  with its original citations and the correct report count.
- `temporal_ordering_accuracy` divides correctly returned pairs of timeline
  facts by all expected pairs across chronological groups. Missing pairs lower
  the score. Pairs within a group are excluded, and cases with no comparable
  pairs return `null`.
- `trace_supported_claims` counts expected claims confirmed by the saved
  report's timeline and reported states. `trace_claims_correct` requires full
  support, even when the host submitted the expected answer and citations.
- `fact_provenance` in the offline summary and `surviving_fact_provenance` in
  the deletion case compare stored facts with the fixture. A match requires the
  fact text and all original source identities, references, segment IDs and quotes.
  `exact_match` also rejects unexpected or duplicated facts.
- `stage_retries`, `stage_attempts` and `stage_retry_rate` come from the submitted
  checkpoint for the expected pipeline. Failed or unfinished runs still count
  their recorded attempts. Recovery and deletion report the write workflow's
  attempts separately from its trace. The rate divides retries by stage attempts.
- `cited_evidence_bytes` and `source_corpus_bytes` compare the bytes of distinct
  cited source texts against all source texts. They measure evidence volume in
  this fixture. Host prompts and model tokens use a separate accounting boundary.

The offline `workflow_metrics` summary includes the raw counts and rates for
pipeline completion, stage retries, recovery, tool calls and end-to-end task
success. Pipeline and stage totals cover each case's trace, the original Memoket
ingestion and the approved deletion. Recovery covers both deliberate
interruptions. Task success covers all seven cases. The top-level tool-call
counts include setup, grading, and intentionally failed calls, so they measure
the complete offline runner's calls, including the incremental update check.

Keep `deterministic` and `host-agent` results separate. The offline run provides
repeatable tool and index checks. Host scores depend on the selected runtime and
model. LoCoMo and LongMemEval-S retain their own datasets, scoring and reproduction
commands.

## Evaluate original conversation context

The source-context benchmark uses a local directory of UTF-8 TXT transcripts.
Each turn starts with a time such as `05:14` or `01:05:14` and a label such as
`Speaker A`. Original lines are cited as `conversation-id#Lline-number`.
Keep transcripts, answer keys, workspaces, and reports under a private,
ignored directory.

```sh
python -m benchmarks.agent.context_eval /path/to/transcripts cases.json artifacts/context-eval
```

Use a JSON array of cases. Each case supplies an `id`, a natural-language
`task`, a list of retrieval `terms` chosen from the task, and an `evidence`
list of exact citation IDs. To run the host comparison, add `answer_patterns`
with regular expressions for independently checked answer requirements.

```sh
python -m benchmarks.agent.context_eval /path/to/transcripts cases.json artifacts/host-eval \
  --model YOUR_CODEX_MODEL --codex /path/to/codex
```

The same model answers each task in fresh, read-only Codex sessions. One arm
can call `kite-tool context` and choose its own phrases. The other searches
the same TXT files with shell tools. The report records actual Codex usage
from its JSON events, elapsed wall time, command counts, and citation and
answer checks. Use `--host-context prefilled` to measure delivering the fixed
context pack directly in the prompt. Report that protocol separately.

Pass `--retrieval question` to test the current compiled question workflow.
This uses each task as the question and the default context budget. In the
live tool comparison, each task directory has a relative launcher or transcript
directory. The file arm starts with a bounded `rg` instruction and chooses its
own search terms. Compare `access_prompt_bytes`, `tool_stdout_bytes`, and Codex
usage separately. `rendered_bytes` measures the offline context pack including
its citation headers.
Use `--repeat 3` to run fresh paired sessions three times per case. Each
repetition reverses the arm order and retains its own Codex usage record.
Add `--matching-lines` to benchmark questions that ask for the source of a
known quotation. Include the quoted phrase in the case question. The file
arm receives the same question and source scope. This setting is recorded in
the result and applies to the first KITE lookup and offline context pack.
Use `--host-follow-up` with live question retrieval to measure a short KITE
hint for batching alternate phrases. The prompt and its byte count are
separate from the default one-command protocol.

For notes, pass `--source-format text` to read UTF-8 `.txt` and `.md` files.
Their citations use `file-stem#text-line-N`. Pass `--recording-metadata DIR`
to read the calendar dates from same-ID JSON files with `conversation_id`
and `started_at`. A case can set inclusive `start_date` and `end_date`.
Both arms use the same source subset for that case. The runner builds a
separate index for the date range and puts the original file copies in date
directories for the file arm. Undated sources are outside any bounded range.

```sh
python -m benchmarks.agent.context_eval /path/to/transcripts cases.json artifacts/dated-host-eval \
  --retrieval question --recording-metadata /path/to/recordings \
  --model YOUR_CODEX_MODEL --codex /path/to/codex
```

Under `--retrieval question`, a case can supply `questions` with exactly
two questions. Both arms see those questions in the task prompt. The fixed
KITE pack uses `--compare-with` and a single quote budget. For field-level
grading, use `claims` in place of `answer_patterns` and `evidence`.
With these case extensions, `./kite context --text` starts with the supplied
task or two questions. Explicit `--question` or `--term` flags remain available
for follow-up retrieval.

```json
{
  "id": "deployment-owner",
  "task": "Who owns the deployment after the handoff, and is there a deadline?",
  "start_date": "2026-04-01",
  "end_date": "2026-04-30",
  "claims": {
    "owner": {"pattern": "Rae", "refs": ["handoff#L14", "followup#L8"]},
    "deadline": {"unknown": true}
  }
}
```

Each answer field needs its own expected citation. Use `all_refs: true` for
claims requiring several lines and `reject` for a disallowed value pattern.
The report counts cited lines outside the visible sources and records corpus,
date, scope, case, and prompt hashes. `host_summary` compares token totals,
uncached input, tool output, and wall time on completed pairs. It reports
claims and completion counts across all recorded trials. A separate
efficiency summary uses only pairs where both answers pass.
Use `--record-tool-output` when diagnosing a retrieval miss. The private
report then includes the commands and the exact output Codex received.

The fixed retrieval result checks exact evidence coverage and the fraction
of tasks where all expected evidence was retrieved. A labeled citation hit
does not assess the relevance of every extra retrieved line. Review answer
pattern failures against the original evidence, including equivalent wording.

Pass `--updates revisions.json` to test edits on a disposable workspace copy.
Each case supplies an `id`, one `evidence` citation, the exact text to
`replace`, its replacement in `with`, and the `terms` to query afterwards.
The result checks that search returns the amended original and revision,
the old citation still opens the historical original, replay is idempotent,
and a stale writer is rejected. These controlled edits measure source
revision handling. Changes in the team's decisions across different meetings
require separate labeled timelines.
