# LoCoMo-ASR data generation

This directory contains the optional pipeline for constructing new LoCoMo-ASR-style data. It is repository-only, uses a separate heavy dependency environment, and is not included in the `memoket-kite` PyPI distribution.

```text
LoCoMo text
  -> Piper neural TTS, one voice per participant
  -> pyroomacoustics far-field RIR, RT60 approximately 0.4 seconds
  -> six-speaker MUSAN babble at SNR 8 through 2 dB
  -> Whisper base.en
  -> asr_text + turn-level WER
```

## Validate without generation dependencies

The frozen release validator uses only the Python standard library. From the KITE repository root:

```bash
python -m benchmarks.locomo_asr.validate --require-reference-hashes
```

See [`../reference_manifest.json`](../reference_manifest.json) for expected files, counts, WER aggregates, and SHA-256 values.

## Optional generation environment

The direct generation dependencies are pinned in [`requirements.txt`](requirements.txt). They are not a hash-locked transitive environment and are intentionally not added to KITE's `pyproject.toml`.

```bash
python -m venv .venv-generation
python -m pip install -r benchmarks/locomo_asr/generation/requirements.txt
```

`piper-tts==1.4.2` is a GPL-3.0-or-later runtime dependency installed separately. Apache-2.0 does not relicense Piper. Review [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) before redistributing any combined environment, container, wheel, or binary.

External inputs are not bundled:

1. LoCoMo `data/locomo10.json` at commit `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`, matching the source hash in the reference manifest.
2. The MUSAN `speech/` directory and its per-source licence and attribution metadata.
3. Piper voices `en_US-lessac-medium` and `en_US-ryan-medium`, including their `.onnx.json` files.
4. The Whisper `base.en` checkpoint.

The generator checks the recorded LoCoMo and Piper hashes unless the corresponding mismatch option is explicitly supplied.

## Verified equivalent voice source

All four recorded voice asset hashes match Piper Voices tag `v1.0.0` at immutable commit `375a0fe641dea077c2a47b4e9a056d6da521eed3`. The revision used by the original downloader was not recorded, so this is a byte-equivalent source rather than a claim about the original download revision.

| Voice | Model | Configuration | Model card |
|---|---|---|---|
| `en_US-lessac-medium` | [ONNX](https://huggingface.co/rhasspy/piper-voices/resolve/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true) | [JSON](https://huggingface.co/rhasspy/piper-voices/resolve/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true) | [MODEL_CARD](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/lessac/medium/MODEL_CARD) |
| `en_US-ryan-medium` | [ONNX](https://huggingface.co/rhasspy/piper-voices/resolve/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ryan/medium/en_US-ryan-medium.onnx?download=true) | [JSON](https://huggingface.co/rhasspy/piper-voices/resolve/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json?download=true) | [MODEL_CARD](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ryan/medium/MODEL_CARD) |

The model cards do not expressly state the licence of the ONNX artifacts, synthesized audio, or downstream ASR text. The repository does not distribute the voices or intermediate audio and does not claim blanket clearance of the voice pipeline.

## Generate a new run

Run from the KITE repository root after separately obtaining the required assets:

```bash
python -m benchmarks.locomo_asr.generation.generate_locomo_asr \
  --input /path/to/locomo10.json \
  --output-dir /path/to/new-run \
  --musan-speech /path/to/musan/speech \
  --voice-a /path/to/en_US-lessac-medium.onnx \
  --voice-b /path/to/en_US-ryan-medium.onnx
```

Use `python -m benchmarks.locomo_asr.generation.generate_locomo_asr --help` for resume, tier, audio-sampling, and mismatch options.

## Reproducibility boundary

New runs write `generation_manifest.json` with their parameters, selected MUSAN inputs, package versions, asset hashes, output hashes, and checkpoint state.

The original detailed `generation_manifest.json` for the frozen v1.0.0 reference files is unavailable. [`../reference_manifest.json`](../reference_manifest.json) records the known source identity, summarized pipeline, verified asset hashes, counts, mean WER, and exact output hashes. It proves the identity and internal consistency of the released bytes; it does not prove byte-identical end-to-end regeneration of the original run.
