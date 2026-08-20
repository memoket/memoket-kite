# Changelog

## Unreleased

### A single answer exit, and two declared refusal rules

- The post-processing stage provides two rules, `premise` and `hedge`; the
  count repair and zero-answer disclosure are withdrawn. LongMemEval declares
  both; LoCoMo declares none.
- Neither rule acts where the caller says the question has an answer: a refusal
  there is a certain miss. `PostprocInput` carries that as a plain boolean, not
  as the workload concept behind it.
- An empty `KITE_POSTPROC` turns every declared rule off, rather than reading as
  an absent override.
- Where `SPEAKER_LABEL` is enabled, a dialogue line and a quoted source name
  their speaker, so a suggestion the assistant made is not read as something
  the user did. Rendering and pricing share one helper, and a label is a
  bounded, single-line display name. LongMemEval enables it; LoCoMo, whose two
  parties are symmetric, leaves it off and renders exactly what it did before.
- The helpers a binding switch reaches take that switch as a required keyword,
  so a caller cannot price a row one way and render it another.
- A rule receives a `PostprocInput`: the question, the answer, the recorded
  verdicts, the advice predicate the deployment runs, and whether a refusal is
  permitted at all.
- Post-processing runs in one place, `answer.finalize()`, which applies the
  caller's declared policy and then snapshots the ledger, so a recorded cost
  describes the answer that was returned.
- A binding runs the library's advice predicate composed with its own question
  families, and every stage that needs the predicate reads the one the binding
  declared.
- The answerability verdict is computed once per question and travels on the
  result, so what a harness records is what the rules were judged under.
- Post-processing rule names, their order, and their implementations come from
  one registry; a policy is validated against it however it was built.
- The corpus-leak gate scans every string a binding exports and the library
  modules whose text reaches a reader. It reports missing corpora, and release
  runs pass `--require-corpus` to make a missing one an error. CI has no
  licensed corpus, so it asserts only that the gate reports one as unread; the
  audit itself runs locally under `--require-corpus`.
- Run manifests fingerprint the library's own prompts alongside the binding's.
- A run refuses to start in a tree holding untracked files. The patch stored
  beside a score covers tracked files only, so such a tree would record
  `dirty: true` with nothing to rebuild it from.

### Deterministic post-processing and evidence-budget work

- Added a channel ledger (`memoket_kite.pipeline.ledger`) that records, per
  question, the token cost of every contributor to the answer prompt and every
  reader call. Observation-only: pack contents and call topology are unchanged
  with it active, and benchmark records now carry the accounting.
- Added a deterministic, zero-LLM post-processing stage
  (`memoket_kite.pipeline.postproc`). Rules read only the question, the answer
  text, and the verdicts recorded by the answer stage. The stage's current rule
  set and per-binding declarations are above.
- Added `ANSWERABLE_BY_CONSTRUCTION` to the benchmark bindings. Where a
  workload declares a question class answerable, the support check no longer
  downgrades an unsupported answer to a refusal and the premise-refusal rule
  does not fire — a refusal there is a guaranteed miss.
- Bounded evidence admission by both a row count and a token budget, whichever
  binds first, and restored the chronological ordering the answer prompt
  asserts after the retry chain appends rows.
- Added the dual-date annotation to the shared evidence renderer: a row whose
  event date differs from its session date and whose text carries a relative
  phrase is annotated so the reader does not resolve the phrase a second time.
  The LongMemEval binding enables it; LoCoMo leaves it off (`DUAL_DATE`).
- Removed mechanisms that measurement rejected: soft grep matching, span
  widening, render dedup, the support gate, relative-date resolution, and the
  build-stage coverage repair pass.

## 0.1.0

- Simplified the application API to `Memory.load`, `remember`, `recall`, and
  `answer`; its top-level error is `KiteError`.
- Added atomic single-file session persistence and marked `Memory.remember()`
  as experimental.
- Unified `remember()` and `recall()` on `list[Fact]`; `answer()` returns text.
- Moved Codebook, query, profile, and trace APIs under `memoket_kite.research`.
- Reorganized the symbolic memory core as the installable `memoket-kite`
  distribution with the `memoket_kite` import package.
- Separated benchmark harnesses, documentation, tests, and generated artifacts.
- Added pinned-dataset reproduction, offline score verification, and GitHub
  Release packaging commands for LoCoMo and cleaned LongMemEval.
- Standardized provider configuration on `OPENAI_API_KEY` and optional
  `OPENAI_BASE_URL` without import-time environment loading.
- Added pytest regression coverage, examples, packaging metadata, and CI.
- Preserved the existing symbolic algorithms and benchmark protocols.
- Preserved live facet and typed-entity constraints while pruning dead symbolic
  values, and made equal-score evidence selection deterministic.
- Added a lazy corpus-relative specificity tiebreaker for truncating query pipes,
  so equally relevant rows prefer concrete evidence before `head` or `tail`.
- Lazily cached topic closures and candidate scores within each query execution
  to avoid repeated taxonomy traversal during ranking.
- Fingerprinted the complete compile prompt and compilation settings for plan
  caches so changed Codebook context cannot reuse a stale plan, including
  provider endpoints and entity-to-object bridges used by plan scoring; cache
  files are now published atomically.
- Counted aligned-instance mention units in LongMemEval evidence recall.
- Preserved full LongMemEval unit IDs that contain the Fact separator character.
