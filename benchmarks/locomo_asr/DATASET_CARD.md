# Dataset Card for LoCoMo-ASR Benchmark

## Summary

LoCoMo-ASR v1.0.0 is an English JSON benchmark derived from LoCoMo. It exposes controlled ASR corruption across seven synthetic far-field babble-noise tiers while preserving upstream conversations, QA annotations, summaries, observations, ordering, and image-related fields.

The release is repository-hosted under `benchmarks/locomo_asr/data/` and is not included in the KITE PyPI wheel or source distribution. Generated audio, Piper voices, Whisper checkpoints, MUSAN recordings, and prebuilt environments are not distributed.

## Source and modifications

- **Upstream:** [snap-research/locomo](https://github.com/snap-research/locomo) at commit `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`.
- **Source file:** `data/locomo10.json`, SHA-256 `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`.
- **Added fields:** `asr_text` and turn-level `wer`.
- **Tiers:** clean plus SNR 8, 7, 6, 5, 4, 3, and 2 dB.
- **Dataset version:** 1.0.0. The manifest label `reference-v1` names the frozen artifact set and is not the KITE software version.

LoCoMo structure and inherited values remain aligned across all eight files. Only `asr_text` and `wer` vary in noisy tiers.

## Size and task coverage

Every tier contains:

- 10 conversations;
- 5,882 dialogue turns;
- 1,986 QA annotations.

| Category | Upstream task type | Questions |
|---:|---|---:|
| 1 | Multi-hop | 282 |
| 2 | Temporal | 321 |
| 3 | Open-domain | 96 |
| 4 | Single-hop | 841 |
| 5 | Adversarial | 446 |
| **Total** |  | **1,986** |

The release does not define a cleaned or recommended 1,509-question subset. Researchers must report selected categories, the `(sample_id, zero-based QA array index)` inclusion rule or list, scorer, denominator, and file hash.

## Intended uses

- paired measurement of conversational-memory degradation under controlled ASR corruption;
- analysis by SNR tier, WER, QA category, entity type, or temporal content;
- development of leak-safe dataset loaders and evaluation protocols;
- comparison of one system across identical questions and annotations in all tiers.

## Out-of-scope uses

- treating synthetic Piper speech as human or production audio;
- commercial use of LoCoMo-derived content under CC BY-NC 4.0;
- reporting an `asr_text`-only ablation as the default LoCoMo-compatible protocol;
- treating category 5 as ordinary answerable QA;
- claiming multilingual, safety-critical, real-room, or end-to-end speech performance from this dataset alone.

## Evaluation boundary

The default LoCoMo-compatible ASR protocol uses the question and a newly constructed conversation view containing session timestamps, `dia_id`, `speaker`, `asr_text` as content, and unchanged `blip_caption` when present. The strict loader in [`loader.py`](loader.py) implements that allowlist.

Clean `text`, `wer`, `img_url`, `query`, `re-download`, gold answers, adversarial answers, and evidence must remain outside memory ingestion, retrieval, routing, and answer prompts. Empty string `asr_text` values are valid ASR outcomes and must not trigger clean-text fallback.

An `asr_text`-only protocol may omit captions, but must be named and reported separately. Category 5 requires a distinct refusal-oriented scoring contract.

## Limitations and media boundary

- Corruption is produced by two synthetic voices, simulated room responses, MUSAN babble, and Whisper `base.en`; it is not a representative sample of speakers, microphones, rooms, accents, recognizers, or languages.
- The dataset inherits LoCoMo annotations and their limitations.
- The upstream image-related values remain unchanged. Each tier contains HTTP(S) references and the same two inline JPEG data values. Memoket did not create or independently license this media; separate rights may apply.
- External URLs are untrusted references. Dataset loaders must not fetch them automatically, and their continued availability is not guaranteed.
- Caption context is unchanged across tiers. Omitting it can remove information needed by caption-grounded questions.
- A turn-level WER can exceed 1.0 when insertions outnumber normalized reference words.

## Maintenance and verification

The eight JSON files are frozen byte-for-byte. SHA-256 values and aggregate statistics are recorded in [`reference_manifest.json`](reference_manifest.json). CI runs the strict validator on Python 3.12.

Any content correction requires a new dataset version, updated hashes and validation coverage, and an explicit change record. Existing v1.0.0 files must not be silently replaced.

The original detailed run-time generation manifest is unavailable, so exact end-to-end byte reproduction of the reference run is not claimed. See [`generation/README.md`](generation/README.md).

## Licences and citation

LoCoMo-authored and LoCoMo-ASR-adapted JSON content is distributed under CC BY-NC 4.0. Linked and embedded media may have separate third-party rights and is not relicensed by Memoket. Memoket-authored code and original documentation are Apache-2.0.

See [`../../LICENSE-DATA.md`](../../LICENSE-DATA.md), [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), and [`CITATION.cff`](CITATION.cff).
