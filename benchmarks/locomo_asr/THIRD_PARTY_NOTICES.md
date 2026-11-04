# LoCoMo-ASR third-party notices

## LoCoMo

The conversation data and QA annotations are derived from LoCoMo:

> Adyasha Maharana, Dong-Ho Lee, Sergey Tulyakov, Mohit Bansal, Francesco
> Barbieri, and Yuwei Fang. “Evaluating Very Long-Term Conversational Memory
> of LLM Agents.” ACL 2024. arXiv:2402.17753.

- Upstream repository: <https://github.com/snap-research/locomo>
- Fixed revision: `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`
- Upstream licence: CC BY-NC 4.0

The derived JSON files preserve LoCoMo material and remain subject to its
attribution and non-commercial restrictions. See
[`../../LICENSE-DATA.md`](../../LICENSE-DATA.md).

The files also preserve upstream image-related values unchanged. Each tier
contains HTTP(S) image references and the same two upstream
`data:image/jpeg;base64,...` values. Memoket did not create or independently
license those media. External links are untrusted references and must not be
fetched automatically by dataset loaders. KITE grants no additional rights to
linked or embedded media.

## MUSAN

MUSAN speech recordings were used locally to synthesize babble noise. MUSAN
audio is not redistributed. Individual MUSAN sources carry their own licence
and attribution metadata.

> David Snyder, Guoguo Chen, and Daniel Povey. “MUSAN: A Music, Speech, and
> Noise Corpus.” arXiv:1510.08484, 2015.

Project page: <https://www.openslr.org/17/>

## Piper runtime and voices

The optional generation environment imports and calls `piper-tts==1.4.2` in
process. The package is maintained by the OHF-Voice contributors under
GPL-3.0-or-later. Version 1.4.2 is sourced from tag `v1.4.2` at commit
`d6975e21a440c0d8b6e5fb7c41027409af13d44d`. Piper source, wheels, binaries,
and package environments are not vendored. Apache-2.0 does not relicense
Piper. Distribution of a combined environment, container, wheel, or binary
must satisfy the applicable GPL requirements.

Piper project: <https://github.com/OHF-Voice/piper1-gpl/tree/v1.4.2>

The `en_US-lessac-medium` and `en_US-ryan-medium` voice files are external
inputs and are not redistributed. All four voice asset hashes in
[`reference_manifest.json`](reference_manifest.json) match Piper Voices tag
`v1.0.0` at immutable commit
`375a0fe641dea077c2a47b4e9a056d6da521eed3`. This is a verified equivalent
source for the reference assets; the revision used by the original downloader
was not recorded.

- [Lessac model card](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/lessac/medium/MODEL_CARD)
- [Ryan model card](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ryan/medium/MODEL_CARD)
- [Verified voice repository revision](https://huggingface.co/rhasspy/piper-voices/tree/375a0fe641dea077c2a47b4e9a056d6da521eed3)

The fixed Piper Voices repository has repository-level `license: mit`
metadata. The Lessac model card links a custom Blizzard 2013 Research Licence
Agreement for its training data. The Ryan model card identifies its training
data as CC BY-NC-SA 4.0 and states that the voice was fine-tuned from Lessac.
Neither per-voice model card expressly states the licence of the ONNX artifact,
synthesized audio, or downstream ASR text. Do not infer voice-specific output
rights solely from repository-level metadata.

This repository distributes the LoCoMo-derived JSON with downstream
`asr_text` and `wer` fields; it does not distribute voice artifacts or
intermediate audio. That non-redistribution boundary is not a blanket rights
clearance for the voice pipeline. Users and distributors must review the
linked terms and obtain any approval required for their intended use. See also
the [Piper voices licensing discussion](https://github.com/rhasspy/piper/discussions/271).

## Whisper and pyroomacoustics

Whisper `base.en` and pyroomacoustics were used as external generation
dependencies. Their code, model checkpoints, and package distributions are not
redistributed.

- Whisper: <https://github.com/openai/whisper>
- pyroomacoustics: <https://github.com/LCAV/pyroomacoustics>

Reference asset hashes are recorded in
[`reference_manifest.json`](reference_manifest.json).
