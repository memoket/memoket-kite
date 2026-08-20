# Reproducibility

KITE separates dataset inputs, generated artifacts, and scoring code so a local
reproduction records its inputs, configuration, and outputs.

## Pinned inputs

[`benchmarks/reproduce/manifest.json`](../benchmarks/reproduce/manifest.json)
records the official upstream revision, immutable download URL, local path, and
SHA256 for LoCoMo and cleaned LongMemEval_S. Raw datasets are downloaded from
their publishers and are never redistributed in a KITE release.

```bash
python -m pip install -e ".[benchmark]"
python -m benchmarks.reproduce.prepare datasets
```

The command downloads to a temporary file, verifies its SHA256, and only then
moves it into `artifacts/datasets/`.

## Full reproduction

```bash
cp .env.example .env
# Set OPENAI_API_KEY; optionally set OPENAI_BASE_URL.
source .env

bash benchmarks/reproduce/locomo.sh
bash benchmarks/reproduce/longmemeval.sh
```

Each script prints the dataset path, model IDs, worker count, output directory,
and stage before doing work. Build and evaluation commands support `--resume`;
any failed ID is written to `failed.txt` and the process exits nonzero. A retry
must be explicit—scripts do not hide errors or retry indefinitely.

The complete LongMemEval Codebook build is:

```text
extract Facts
→ consolidate topics and entities
→ align repeated real-world instances
→ refine topic assignments against the final taxonomy
→ atomically write the local Codebook
```

Both finalization stages are enabled visibly in
`benchmarks/longmemeval/profile.py` and run uniformly for all 500 haystacks.

## Verify the reference reproduction

With the default `TAG` and `MODEL`, the scripts write to the paths declared in
the reproduction manifest. After rebuilding both reference runs, verify them
against the recorded contract:

```bash
python -m benchmarks.reproduce.verify locomo
python -m benchmarks.reproduce.verify longmemeval
```

The verifier checks the reference paths, local manifests, corpus digests,
Codebook and row counts, and reported aggregates. Runs made with a custom `TAG`
or `MODEL` can validate their seals and recompute their own metrics directly:

```bash
python -m benchmarks.locomo.score --tag my-run --judge-model gpt-4.1-mini --offline
python -m benchmarks.longmemeval.score --tag my-run --judge-model gpt-4.1-mini --offline
```

Generated benchmark files remain under the Git-ignored `artifacts/` tree.

## The corpus-leak gate

```bash
python -m benchmarks.tools.leak_check --require-corpus
```

The gate reports terms that appear in shipped text and in a small fraction of a
corpus's documents. It scans every string a binding exports and the library
modules whose text reaches a reader. A missing corpus is reported as SKIPPED;
`--require-corpus` makes it an error. Run it with the flag before publishing a
number.
