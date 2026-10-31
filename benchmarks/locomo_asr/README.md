<div align="center">

# LoCoMo-ASR Benchmark

**A controlled dataset for measuring conversational-memory robustness under ASR noise.**

One clean reference view and seven synthetic noisy ASR tiers built from the same LoCoMo conversations and questions.

<a href="../../LICENSE-DATA.md"><img src="https://img.shields.io/badge/dataset-CC%20BY--NC%204.0-blue.svg" alt="Dataset license: CC BY-NC 4.0"></a>
<a href="../../LICENSE"><img src="https://img.shields.io/badge/code-Apache%202.0-green.svg" alt="Code license: Apache 2.0"></a>
<a href="CITATION.cff"><img src="https://img.shields.io/badge/version-1.0.0-informational.svg" alt="Dataset version 1.0.0"></a>

</div>

## Why LoCoMo-ASR

Conversational-memory benchmarks are normally evaluated on clean text. Spoken systems have another error source before retrieval or answering begins: automatic speech recognition can alter names, dates, entities, and relations in the stored conversation.

LoCoMo-ASR isolates that input shift. Conversations, turn identities, QA annotations, categories, evidence, captions, and ordering remain fixed across tiers. Each turn keeps the upstream `text` for analysis and adds `asr_text` as the system-facing transcript plus `wer` as a diagnostic value.

| Held constant | Changed by tier | Default system input |
|---|---|---|
| 10 conversations, 5,882 turns, 1,986 QA annotations, IDs, ordering, evidence, and image captions | `asr_text`, turn-level `wer`, and noise tier | An allowlisted view containing `asr_text` and the unchanged `blip_caption` when present |

## Dataset snapshot

- **Dataset version:** 1.0.0, independent of the KITE software version.
- **Eight JSON files:** one clean reference view and seven noisy tiers at SNR 8 through 2 dB.
- **Each tier:** 10 conversations, 5,882 turns, and all 1,986 upstream LoCoMo QA annotations.
- **Synthetic degradation:** Piper speech synthesis, simulated room acoustics, MUSAN babble, and Whisper transcription.
- **Repository-only:** the data and generation tooling are not included in KITE's PyPI wheel or source distribution.
- **Preserved media fields:** every tier retains upstream HTTP(S) image references and the same two inline JPEG data values. No separate image collection is bundled or fetched by the provided tooling.

| Tier | Dataset file | Mean WER |
|---|---|---:|
| Clean | [`locomo10-clean-asr-view.json`](data/locomo10-clean-asr-view.json) | 0.000000 |
| SNR 8 dB | [`locomo10-asr-musan-full-snr8.json`](data/locomo10-asr-musan-full-snr8.json) | 0.124080 |
| SNR 7 dB | [`locomo10-asr-musan-full-snr7.json`](data/locomo10-asr-musan-full-snr7.json) | 0.151242 |
| SNR 6 dB | [`locomo10-asr-musan-full-snr6.json`](data/locomo10-asr-musan-full-snr6.json) | 0.188776 |
| SNR 5 dB | [`locomo10-asr-musan-full-snr5.json`](data/locomo10-asr-musan-full-snr5.json) | 0.239196 |
| SNR 4 dB | [`locomo10-asr-musan-full-snr4.json`](data/locomo10-asr-musan-full-snr4.json) | 0.304957 |
| SNR 3 dB | [`locomo10-asr-musan-full-snr3.json`](data/locomo10-asr-musan-full-snr3.json) | 0.388380 |
| SNR 2 dB | [`locomo10-asr-musan-full-snr2.json`](data/locomo10-asr-musan-full-snr2.json) | 0.489393 |

WER is a ratio, not a probability. Insertions can make an individual turn's `wer` greater than `1.0`.

## Validate the frozen release

Run from the KITE repository root. Validation uses only the Python standard library; CI performs the complete frozen-data check on Python 3.12.

```bash
python -m benchmarks.locomo_asr.validate --require-reference-hashes
```

The validator checks the exact eight-file set, counts, inherited-field alignment and order, generated-field types, recomputed turn-level WER, aggregate WER, and SHA-256 values from [`reference_manifest.json`](reference_manifest.json).

If you have the fixed upstream LoCoMo source file, add an independent source comparison:

```bash
python -m benchmarks.locomo_asr.validate \
  --require-reference-hashes \
  --source /path/to/locomo10.json
```

Without `--source`, source structure is recovered from the clean view. With it, the validator also requires the supplied file to match the recorded upstream SHA-256.

## Load a tier safely

The provided loader constructs new allowlisted objects and never passes raw dataset turns downstream.

```python
from pathlib import Path

from benchmarks.locomo_asr.loader import load_system_inputs


system_inputs = load_system_inputs(
    Path("benchmarks/locomo_asr/data/locomo10-asr-musan-full-snr8.json")
)

assert len(system_inputs) == 10
assert (
    sum(len(session["turns"]) for sessions in system_inputs.values() for session in sessions)
    == 5_882
)
```

Process one `sample_id` at a time so dialogue IDs from different conversations cannot collide. Keep `sample_id` in the evaluation harness as a grouping key; it need not be shown to the model.

The loader requires a string `asr_text`, preserves an empty string as a valid ASR output, and never falls back to clean `text`. Its system-facing turn contains only `dia_id`, `speaker`, `content=asr_text`, and optional `blip_caption`. Session timestamps are preserved at the session level.

For the default **LoCoMo-compatible ASR** protocol, serialize `blip_caption` as image-caption context when present. This follows the [fixed upstream LoCoMo evaluation code](https://github.com/snap-research/locomo/blob/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/task_eval/gpt_utils.py#L154-L184). An `asr_text`-only input is a separate ablation.

Keep `img_url`, `query`, `re-download`, clean `text`, `wer`, gold answers, adversarial answers, and evidence outside memory ingestion, retrieval, routing, and answer prompts. Do not automatically fetch external image URLs. See [`SCHEMA.md`](SCHEMA.md) for the complete contract.

## Construction

```text
Original LoCoMo JSON
├── Clean reference view
│   └── text -> asr_text, wer = 0
└── Synthetic noisy path
    ├── Piper speech synthesis
    ├── pyroomacoustics room simulation
    ├── MUSAN babble at SNR 8 to 2 dB
    ├── Whisper base.en transcription
    └── asr_text + turn-level wer in release JSON
```

Recorded settings include 16 kHz audio, RT60 of 0.4 seconds, six random rooms, six babble talkers, 8-second noise clips, and random seed 42. The reference manifest records source identity, parameters, asset hashes, counts, aggregate WER, and released-file hashes. Generation source and its separate dependency boundary are documented in [`generation/README.md`](generation/README.md).

## Evaluation contract

A comparable paired evaluation should:

1. use identical `(sample_id, zero-based QA array index)` identities and questions across clean and noisy tiers;
2. give the system only the question and the strict conversation view built from `asr_text` plus `blip_caption` when present;
3. keep clean `text`, `wer`, media references, gold answers, adversarial answers, and evidence outside the system path;
4. use `wer` only after prediction for diagnostic analysis;
5. report categories, exact inclusion rule or list, scorer and settings, denominator, dataset version or commit, and file hashes;
6. score category 5 separately because its 446 adversarial questions require a refusal-oriented protocol.

The JSON release contains all 1,986 QA annotations and does not define a recommended 1,509-question subset. This directory provides the data, strict loader, validator, and generation source, but no LoCoMo-ASR `build`, `evaluate`, or `score` binding and no ASR benchmark results.

## Reproducibility boundary

The released JSON bytes are frozen and hash-verifiable. Supplying `--source` independently verifies their inherited structure against the fixed upstream file.

The original detailed run-time `generation_manifest.json` for these frozen artifacts is unavailable. This repository therefore does not claim exact end-to-end byte reproduction from source LoCoMo, voice assets, simulated audio, and ASR. New generator runs produce their own generation manifest, but cannot retrospectively recover the missing record for the reference run.

## Citation and licences

Please cite both this dataset release and the original LoCoMo work:

- dataset metadata: [`CITATION.cff`](CITATION.cff);
- original benchmark: [*Evaluating Very Long-Term Conversational Memory of LLM Agents*](https://aclanthology.org/2024.acl-long.747/) and the [LoCoMo repository](https://github.com/snap-research/locomo).

| Material | Terms |
|---|---|
| Memoket-authored code and original documentation | [Apache License 2.0](../../LICENSE) |
| LoCoMo-authored and LoCoMo-ASR-adapted JSON content | [CC BY-NC 4.0](../../LICENSE-DATA.md), including attribution, modification notice, and non-commercial-use requirements |
| Preserved external image references and embedded media | Separate third-party rights may apply; Memoket grants no additional rights |
| Piper runtime, voice models, generated audio, Whisper checkpoints, MUSAN assets, and package artifacts | External; not redistributed as part of the Python distribution or as separate assets |

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), [`../../LICENSE-DATA.md`](../../LICENSE-DATA.md), and [`../../NOTICE`](../../NOTICE) for provenance and the complete rights boundary.
