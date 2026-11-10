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
Set `KITE_ARTIFACTS_DIR` before preparing datasets when you want an isolated
run. Preparation and evaluation use the same directory.

## Run the benchmarks

With the datasets prepared, check the corpus hashes, retrieval profile, worker
counts, and output locations without model credentials.

```bash
bash benchmarks/reproduce/locomo.sh check-offline
bash benchmarks/reproduce/longmemeval.sh check-offline
```

Set credentials to check the local model configuration and run the evaluations.

```bash
cp .env.example .env
# Set OPENAI_API_KEY; optionally set OPENAI_BASE_URL.
source .env

bash benchmarks/reproduce/locomo.sh check
bash benchmarks/reproduce/longmemeval.sh check

bash benchmarks/reproduce/locomo.sh
bash benchmarks/reproduce/longmemeval.sh
```

The `check` commands validate a fresh run's pinned dataset, local model
configuration, worker counts and output locations without sending model
requests. Both full runs and resumes run these checks before building.
Model credentials are checked for presence locally. The provider validates
them on the first model request.

Current LoCoMo runs use the answer prompt without concrete answer examples and
default to the `example-free-v1` result tag. A rerun can reuse existing
Codebooks with `bash benchmarks/reproduce/locomo.sh resume` when their build
provenance matches. New Codebooks record the extraction model and digests of
the provider endpoint, normalized source sessions and build code. A build
resume rejects mismatched or unstamped Codebooks before extraction for that
record. Keep historical Codebooks for verification or evaluate a new build
under a separate `KITE_ARTIFACTS_DIR`. The build command's `--force` option
explicitly replaces existing Codebooks.

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

## Verify historical results

The reference verifier checks the archived paths and scores in the reproduction
manifest. To run the prior LoCoMo prompt configuration, use a separate
checkout of the source revision before the example removal.

```bash
git worktree add --detach ../kite-locomo-reference 618928450ddeab33aab2ec63d1843532fb3a3441
```

Run the setup and reproduction commands from that checkout. Historical
LoCoMo tags are reserved on the current branch. If the historical judged
artifacts are available, verify them against the recorded contract.

```bash
python -m benchmarks.reproduce.verify locomo
python -m benchmarks.reproduce.verify longmemeval
```

The verifier checks the reference paths, local manifests, corpus digests,
Codebook and row counts, and reported aggregates. To validate a new run's
seal and recompute its own metrics, use the tag and judge from that run.

```bash
python -m benchmarks.locomo.score --tag example-free-v1 --judge-model gpt-4.1-mini --offline
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
