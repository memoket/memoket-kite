# Run and verify KITE benchmarks

The reproduction contract has one source of truth:
[`manifest.json`](manifest.json). It pins official dataset bytes, model IDs,
expected artifact counts, metric names, and reported scores.

## Rebuild from official data

```bash
python -m pip install -e ".[benchmark]"
python -m benchmarks.reproduce.prepare datasets
bash benchmarks/reproduce/locomo.sh check-offline
bash benchmarks/reproduce/longmemeval.sh check-offline

cp .env.example .env
# Set OPENAI_API_KEY; optionally set OPENAI_BASE_URL.
source .env

bash benchmarks/reproduce/locomo.sh
bash benchmarks/reproduce/longmemeval.sh
```

The offline checks need the pinned datasets and do not contact a model provider.
Use `bash .../locomo.sh check-provider` or `bash .../longmemeval.sh check-provider`
to test a configured model with one synthetic request before starting a build.
Use `bash .../locomo.sh resume` or `bash .../longmemeval.sh resume` after an
interrupted run. Failures are never swallowed: the stage exits nonzero and
records failed IDs beside its output.

## Verify the reference reproduction

The current LoCoMo script writes to `locomo-example-free-v1` by default. Use the
[historical checkout instructions](../../docs/reproducibility.md#verify-historical-results)
to run the earlier prompt configuration separately. With the historical judged
artifacts in the paths declared in `manifest.json`, verify the recorded scores.

```bash
python -m benchmarks.reproduce.verify locomo
python -m benchmarks.reproduce.verify longmemeval
```

The verifier checks the reference paths, local manifests, corpus digests,
expected Codebook and row counts, and reported aggregates. For a run made with
the current prompt or a custom tag or model, validate its seal and recompute its
metrics with the corresponding scorer.

```bash
python -m benchmarks.locomo.score --tag example-free-v1 --judge-model gpt-4.1-mini --offline
python -m benchmarks.longmemeval.score --tag my-run --judge-model gpt-4.1-mini --offline
```

## Compare two runs

```bash
python -m benchmarks.reproduce.compare locomo \
  artifacts/results/locomo-baseline artifacts/results/locomo-candidate
python -m benchmarks.reproduce.compare longmemeval \
  artifacts/results/longmemeval-baseline artifacts/results/longmemeval-candidate
```

Pass two sealed result directories with the same judge model, endpoint, rubric,
and question set. The command checks both seals and reports overall and grouped
metric changes, counts of improved and regressed verdicts, and the IDs whose
verdicts changed. It reads the artifacts offline without writing to either run
or including question and answer text in the report. Use the same selections and
judge settings when rerunning the baseline and candidate, and keep their output
tags distinct. An older score without a verifiable seal needs a fresh baseline run.

See
[`../../docs/reproducibility.md`](../../docs/reproducibility.md) for the complete
workflow.
