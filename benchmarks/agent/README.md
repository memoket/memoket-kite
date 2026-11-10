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
the dated timeline. It exits nonzero if any case or lifecycle check fails.

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

The other cases are `owner-handoff`, `dated-timeline`, and `unconfirmed-email`.
Use a fresh directory per trial. Record the host version, model and configuration
alongside results when comparing runs. The scorer reads the actual checkpoint
and report through KITE, verifies that the task used the expected pipeline at the
seeded snapshot, then checks the returned values and citations against the answer
key. A correct string with a missing or unrelated citation fails.

## Read the results

- `supported_claims / expected_claims` measures answer coverage. An unexpected
  key or an incorrect submitted value or citation counts as an unsupported claim.
- `expected_citations_found` counts satisfied evidence requirements. Timeline
  dates accept any cited segment from the matching date.
- `stage_retries` and `stage_attempts` come from the completed run's checkpoint.
  Offline tool-call totals include the intentionally failed recovery and deletion checks.
- `cited_evidence_bytes` and `source_corpus_bytes` compare the bytes of distinct
  cited source texts against all source texts. They measure evidence volume in
  this fixture. Host prompts and model tokens use a separate accounting boundary.

Keep `deterministic` and `host-agent` results separate. The offline run provides
repeatable tool and index checks. Host scores depend on the selected runtime and
model. LoCoMo and LongMemEval-S retain their own datasets, scoring and reproduction
commands.
