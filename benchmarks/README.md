# KITE benchmarks

Benchmark code is separate from the installable package. Each public benchmark
has the same readable stage layout:

| File | Responsibility |
|---|---|
| `adapter.py` | Convert the official dataset format into conversation episodes |
| `profile.py` | Freeze benchmark prompts, vocabulary, and explicit pipeline settings |
| `protocol.py` | Preserve the official judging rules and deterministic metrics |
| `build.py` | Build complete Codebooks |
| `evaluate.py` | Answer benchmark questions, with explicit resume support |
| `score.py` | Judge results or recompute an existing judged run offline |

## Benchmark policy layer

A benchmark binding may enable deterministic answer policies that belong to the
evaluation protocol rather than to the library — the premise and hedge refusals
in `memoket_kite.pipeline.postproc`. LongMemEval declares both, because it
contains questions with no answer; LoCoMo declares none, because every question
there has one and a refusal would be a certain miss. Three rules keep the layer
honest:

1. **Gold-blind by type.** A policy receives a `PostprocInput` carrying the
   question, the answer's own text, the recorded verdicts, the advice predicate
   the deployment runs, and whether a refusal is permitted at all.
2. **Declared, not ambient.** Which policies a run enables is set in its
   profile (`POSTPROC_RULES`), overridable by `KITE_POSTPROC`, and recorded in
   every run manifest. The policy is applied once, inside the answer stage.
3. **Reported as what they are.** These are protocol-specific policies,
   isolated from the library's own defaults and fingerprinted in every run
   manifest — not universal semantic repairs, and not claimed as such.

### Question predicates

A binding recognises questions that point back at an earlier exchange ("our
previous conversation", "remind me", "you mentioned"). It reads question text
only and lives under `benchmarks/` rather than inside the installable package,
and each run manifest records its digest.

`common/paths.py` contains repository artifact paths. `tools/leak_check.py` is
the only analysis gate retained in the release tree; CI runs it on every push,
and a benchmark or release run should invoke it with `--require-corpus` so a
missing corpus fails rather than reporting clean. Dataset, Codebook, cache, and
result files always belong under the Git-ignored `artifacts/` directory.

## Pinned datasets

- **LoCoMo:** the ten-conversation `data/locomo10.json` release.
- **LongMemEval:** the current official `longmemeval_s_cleaned.json` release.

Exact upstream revisions, download URLs, and SHA256 values are recorded in
[`reproduce/manifest.json`](reproduce/manifest.json). Download and verify both:

```bash
python -m benchmarks.reproduce.prepare datasets
```

## Stage commands

```bash
python -m benchmarks.locomo.build --help
python -m benchmarks.locomo.evaluate --help
python -m benchmarks.locomo.score --help

python -m benchmarks.longmemeval.build --help
python -m benchmarks.longmemeval.evaluate --help
python -m benchmarks.longmemeval.score --help

python -m benchmarks.multihop.adapter --help
python -m benchmarks.multihop.build --help
python -m benchmarks.multihop.evaluate --help
python -m benchmarks.multihop.score --help
```

## Multihop document QA

`multihop/` follows the same adapter/profile/protocol/build/evaluate/score layout
for MuSiQue, HotpotQA and 2Wiki, with one shared encyclopedia profile. The route is
strictly **one passage extraction → one compiled KITE plan → native evidence
retrieval → one final answer**. Extraction and compilation use `gpt-6-luna`
with medium reasoning; the reader uses `gpt-4o-mini` at temperature0.
No additional model passes, candidate scoring, output repair, vectors or new
query operators are enabled. The existing native normalization and evidence
packing remain in use. Full prompts/responses/usage are recorded without headers.
The shared multihop binding keeps the native registry preview limits (80 codes
for extraction, 120 for compilation), but puts exact input mentions of existing
codes, names or registered aliases first. This only selects prompt context;
it does not invent aliases, merge entities, or expand the evidence search.
Bindings without `ENTITY_PREVIEW_BY_MENTION` retain the original catalog order.
Native entity retrieval also expands through associated object classes. That
expands recall without merging identities; a retrieved candidate alone does not
prove the requested relationship. The original class expansion is retained.
An extraction failure is recorded in the Codebook manifest; its original source
is retained with no fabricated facts. A previously attempted source with no
complete native cache is not automatically sent again. Unknown in-flight usage
stays unknown and a corpus with failures is explicitly marked incomplete.
Each encyclopedia call has one input source line. The application binds that
line to the native Fact `src`; the model does not generate a source identifier.
Multi-line conversation bindings retain their original per-line citation path.
Invalid facts or supplied source pointers stop this benchmark's build before
later paid calls. Incomplete banks cannot be evaluated. CLI termination drains
the current source response, then saves an incomplete checkpoint; resuming a
control stop replays completed sources from exact native caches.

Start with the authorized small connectivity check:

```bash
python -m benchmarks.multihop.adapter --tag smoke --smoke-questions 1
python -m benchmarks.multihop.build --tag smoke --env-file /path/to/.env
python -m benchmarks.multihop.evaluate --tag smoke --env-file /path/to/.env
python -m benchmarks.multihop.score --tag smoke
```

The smoke corpus includes **all candidate passages and distractors** of the
first official question per collection, deduplicated by exact title/text. It is
a connectivity check, not a full-corpus benchmark. Full preparation instead uses
the pinned official shared corpora (27,586 passages) and1,000 questions/collection:
`python -m benchmarks.multihop.adapter --tag full`.
Preparing data costs no model calls; do not launch a full extraction implicitly.

Preparation writes separate corpus, question-only and reference files under
`artifacts/datasets/multihop`. Build reads the corpus only. Evaluation reads only
question IDs/text and the new Codebook; runtime guards block reference/raw QA
reads. Score runs separately after all three prediction sets freeze and uses
standard answer-normalized EM/token F1, never an LLM judge. These questions have
already been exposed during development and must not be described as blind tests.

Native stages pass document/date scope, not a dynamically inferred entity value.
The prompt requests evidence for the entire relation chain using existing
stages, topic closures, entities and facets. Unsupported references or invented
bridge names are forbidden. Report the actual plan and evidence; stage count
alone does not prove successful cross-entity multihop reasoning. The binding
does not add an entity binder or graph traversal to conceal this limitation.

LongMemEval's build profile explicitly enables two finalization stages after
Fact extraction and topic/entity consolidation:

```text
align repeated real-world instances → refine topic assignments → atomic publish
```

They run once for every LongMemEval Codebook. They are never selected by
question type or gold label and are not query-time post-processing. LoCoMo does
not enable either stage.

## Reproduction paths

For a full local reproduction, configure the LLM endpoint and run the two
scripts under `reproduce/`. The workflow fetches the pinned upstream datasets
and rebuilds the evaluation under the local `artifacts/` tree. See
[`docs/reproducibility.md`](../docs/reproducibility.md) for the complete contract.
