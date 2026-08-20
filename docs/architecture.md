# Architecture

KITE separates its core symbolic memory implementation from dataset-specific
evaluation code. Its primary flow is:

```text
conversation episode
→ structured Facts
→ XML symbolic memory representation
→ symbolic retrieval
→ Facts
→ evidence-backed answer
```

## Active package

- `memoket_kite.memory`: stable `Memory` facade for loading, remembering,
  recalling, and answering.
- `memoket_kite.remember`: message validation and session-to-Fact extraction.
- `memoket_kite.defaults`: the built-in profile for the standard Memory workflow.
- `memoket_kite.prompts`: internal prompt templates grouped by extraction,
  recall, answer, and build-time instance-alignment responsibilities.
- `memoket_kite.storage`: XML loading and atomic session persistence.
- `memoket_kite.fact`: the single read-only public Fact returned by Memory.
- `memoket_kite.research`: Codebook/query/profile/trace APIs for experiments.
- `memoket_kite.core.vocab`: controlled topic DAG, entity registry,
  aliases, and governance operators.
- `memoket_kite.core.algebra`: codebook store, posting indexes, query plans,
  deterministic execution, ranking, relaxation, and traces. Its mutable
  `FactRecord` is an internal storage representation, distinct from public
  `Fact`.
- `memoket_kite.pipeline.extract`: Fact extraction, controlled-vocabulary
  normalization, XML writing, vocabulary consolidation, and optional
  completed-Codebook instance alignment.
- `memoket_kite.pipeline.compile_plan`: natural-language question to symbolic
  retrieval plan.
- `memoket_kite.pipeline.retrieve`: plan normalization, symbolic execution,
  lexical retrieval, and evidence-row selection.
- `memoket_kite.pipeline.answer`: evidence packing, answer generation, support
  checking, and bounded recovery.
- `memoket_kite.pipeline.patterns`: question and answer predicates shared by
  retrieval, answering, and post-processing.
- `memoket_kite.pipeline.verdicts`: the premise gate, a deterministic judgement
  about whether a question names anything memory has seen.
- `memoket_kite.pipeline.render`: how a stored line becomes a line of evidence
  a reader can attribute to a speaker.
- `memoket_kite.pipeline.postproc`: deterministic answer rewrites, declared by
  the caller and applied with no model calls. None are enabled by default.
- `memoket_kite.pipeline.ledger`: per-question accounting of every contributor
  to the answer prompt; observation-only.
- `memoket_kite.providers.llm`: OpenAI-compatible HTTP model boundary.

Complex dataclasses remain next to the algorithms that own their invariants.
They were deliberately not redesigned during the repository migration.

## Benchmark boundary

Each benchmark owns a dataset adapter and a frozen profile. Adapters normalize
source data into the session dictionary consumed by `extract_facts`; profiles
carry domain vocabulary, prompts, and explicit pipeline settings.

Generated state never belongs beside source code. Paths are centralized in
`benchmarks.common.paths` and default to `artifacts/`. Each benchmark exposes
direct `build`, `evaluate`, and `score` modules; `protocol.py` contains judging
prompts and deterministic metrics.

## Prompt boundary

The standard `Memory` workflow draws reusable prompt templates from
`memoket_kite.prompts`. Extraction (including topic/entity consolidation),
recall-plan compilation, answer generation, and instance-alignment templates
are separate from their execution code. Frozen
benchmark prompts remain in benchmark profiles because they are part of each
benchmark's reproducibility contract.

## Optional Codebook finalization

LongMemEval opts into two completed-Codebook stages after all conversation
episodes have been extracted and topic/entity consolidation is complete.
Instance alignment groups repeated Fact mentions that identify the same
real-world item or event and stores an `<instances>` registry. Topic refinement
then assigns Facts against the completed taxonomy while preserving root
ancestors. Both run uniformly during an atomic build, not after retrieval.
LoCoMo enables neither stage.

## Stability

`memoket_kite.__init__` exports `Memory`, `Fact`, `Answer`, `KiteError`, its
four public error subclasses, and the package version. Users instantiate
`Memory`; `Fact` and `Answer` are its read-only returned values.
`memoket_kite.research`, `core`, `pipeline`, and `providers` may evolve
independently of the public facade.
