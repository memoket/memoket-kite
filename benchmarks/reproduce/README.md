# Run and verify KITE benchmarks

The reproduction contract has one source of truth:
[`manifest.json`](manifest.json). It pins official dataset bytes, model IDs,
expected artifact counts, metric names, and reported scores.

## Rebuild from official data

```bash
python -m pip install -e ".[benchmark]"
python -m benchmarks.reproduce.prepare datasets
cp .env.example .env
# Set OPENAI_API_KEY; optionally set OPENAI_BASE_URL.
source .env

bash benchmarks/reproduce/locomo.sh
bash benchmarks/reproduce/longmemeval.sh
```

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

See
[`../../docs/reproducibility.md`](../../docs/reproducibility.md) for the complete
workflow.
