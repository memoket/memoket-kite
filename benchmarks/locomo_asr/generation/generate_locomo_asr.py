#!/usr/bin/env python3
"""Generate LoCoMo-ASR with the frozen far-field speech pipeline.

Pipeline: Piper TTS -> pyroomacoustics RIR -> six-speaker MUSAN babble
-> SNR mixing -> Whisper ASR.  The generated JSON preserves every upstream
LoCoMo field and adds only ``asr_text`` and per-turn ``wer``.

The reference release used seed 42, RT60 0.4 s, Whisper ``base.en``, and SNR
levels 8 through 2 dB.  External model and MUSAN assets are intentionally not
vendored; every run records their hashes in ``generation_manifest.json``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import scipy.signal as sps

from benchmarks.locomo_asr.data_tools import iter_turns, sha256_file, word_error_rate
from benchmarks.locomo_asr.generation.assets import validate_reference_voice

SAMPLE_RATE = 16_000
REFERENCE_SOURCE_SHA256 = "79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_wav(audio: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((clipped * 32767).astype(np.int16).tobytes())


class PiperSynthesizer:
    def __init__(self, voice_a: Path, voice_b: Path):
        from piper import PiperVoice

        self.voice_a = PiperVoice.load(str(voice_a))
        self.voice_b = PiperVoice.load(str(voice_b))

    def synthesize(self, text: str, speaker_is_a: bool) -> np.ndarray:
        text = (text or "").strip()
        if not text:
            return np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)

        voice = self.voice_a if speaker_is_a else self.voice_b
        fd, temporary = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(temporary, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)
            with wave.open(temporary, "rb") as wav_file:
                source_rate = wav_file.getframerate()
                if wav_file.getsampwidth() != 2:
                    raise RuntimeError("Piper produced a non-16-bit WAV")
                audio = (
                    np.frombuffer(
                        wav_file.readframes(wav_file.getnframes()), dtype=np.int16
                    ).astype(np.float32)
                    / 32768.0
                )
        except Exception as exc:
            raise RuntimeError(f"Piper TTS failed for text {text[:80]!r}") from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

        if len(audio) < 10:
            raise RuntimeError(f"Piper returned empty audio for text {text[:80]!r}")
        if source_rate != SAMPLE_RATE:
            audio = sps.resample(audio, int(len(audio) * SAMPLE_RATE / source_rate)).astype(
                np.float32
            )
        return audio


def _read_musan_segment(
    path: Path, seconds: int, rng: np.random.RandomState
) -> tuple[np.ndarray, dict]:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        source_rate = handle.getframerate()
        channels = handle.getnchannels()
        source_frames = handle.getnframes()
        wanted = int(seconds * source_rate)
        start = int(rng.randint(0, max(1, source_frames - wanted))) if source_frames > wanted else 0
        handle.setpos(start)
        raw = np.frombuffer(handle.readframes(min(wanted, source_frames)), dtype=np.int16).astype(
            np.float32
        )
    if channels > 1:
        raw = raw.reshape(-1, channels).mean(axis=1)
    audio = raw / 32768.0
    if source_rate != SAMPLE_RATE and len(audio) > 1:
        audio = sps.resample(audio, int(len(audio) * SAMPLE_RATE / source_rate)).astype(np.float32)
    return audio, {
        "source_sample_rate": source_rate,
        "source_frames": source_frames,
        "start_frame": start,
    }


def build_musan_babble(
    speech_dir: Path,
    rng: np.random.RandomState,
    *,
    number_of_clips: int = 60,
    number_of_talkers: int = 6,
    segment_seconds: int = 8,
    source_pool_size: int = 120,
) -> tuple[list[np.ndarray], list[dict]]:
    """Build the reference six-speaker babble bank from MUSAN speech."""
    files = sorted(speech_dir.rglob("*.wav"))
    if not files:
        raise FileNotFoundError(f"no WAV files below MUSAN path {speech_dir}")
    rng.shuffle(files)

    segment_length = segment_seconds * SAMPLE_RATE
    segments: list[np.ndarray] = []
    provenance: list[dict] = []
    errors: list[str] = []
    for path in files[:source_pool_size]:
        try:
            audio, metadata = _read_musan_segment(path, segment_seconds, rng)
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
            continue
        if len(audio) < segment_length:
            errors.append(f"{path}: shorter than {segment_seconds}s after resampling")
            continue
        segments.append(audio[:segment_length])
        provenance.append(
            {
                "path_relative_to_musan": str(path.relative_to(speech_dir)),
                "sha256": sha256_file(path),
                **metadata,
            }
        )

    if len(segments) < number_of_talkers:
        details = "\n".join(errors[:10])
        raise RuntimeError(
            f"only {len(segments)} valid MUSAN segments; need at least "
            f"{number_of_talkers}. First errors:\n{details}"
        )

    clips = []
    for _ in range(number_of_clips):
        selected = rng.choice(len(segments), size=number_of_talkers, replace=False)
        mixed = np.zeros(segment_length, dtype=np.float32)
        for index in selected:
            mixed += segments[int(index)] * rng.uniform(0.7, 1.0)
        clips.append((mixed / (np.max(np.abs(mixed)) + 1e-9)).astype(np.float32))
    return clips, provenance


def build_rir_bank(
    rt60_seconds: float,
    rng: np.random.RandomState,
    count: int = 6,
) -> tuple[list[np.ndarray], list[dict]]:
    import pyroomacoustics as pra

    bank: list[np.ndarray] = []
    provenance: list[dict] = []
    failures: list[str] = []
    for _ in range(count):
        dimensions = [
            rng.uniform(4, 7),
            rng.uniform(3, 6),
            rng.uniform(2.5, 3.5),
        ]
        try:
            absorption, max_order = pra.inverse_sabine(rt60_seconds, dimensions)
            source = [
                rng.uniform(0.5, dimensions[0] - 0.5),
                rng.uniform(0.5, dimensions[1] - 0.5),
                1.5,
            ]
            microphone = [
                rng.uniform(0.5, dimensions[0] - 0.5),
                rng.uniform(0.5, dimensions[1] - 0.5),
                1.2,
            ]
            room = pra.ShoeBox(
                dimensions,
                fs=SAMPLE_RATE,
                materials=pra.Material(absorption),
                max_order=min(max_order, 12),
            )
            room.add_source(source)
            room.add_microphone(microphone)
            room.compute_rir()
            rir = room.rir[0][0].astype(np.float32)
            bank.append(rir / (np.max(np.abs(rir)) + 1e-9))
            provenance.append(
                {
                    "room_dimensions_m": dimensions,
                    "source_position_m": source,
                    "microphone_position_m": microphone,
                    "absorption": float(absorption),
                    "max_order": int(min(max_order, 12)),
                    "rir_samples": len(rir),
                }
            )
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
    if not bank:
        raise RuntimeError("pyroomacoustics failed to create every RIR: " + "; ".join(failures))
    return bank, provenance


def mix_at_snr(reverberant_signal: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    signal_power = np.mean(reverberant_signal**2) + 1e-12
    noise_power = np.mean(noise**2) + 1e-12
    scale = np.sqrt(signal_power / (10 ** (snr_db / 10)) / noise_power)
    mixed = reverberant_signal + scale * noise
    return (mixed / (np.max(np.abs(mixed)) + 1e-9) * 0.95).astype(np.float32)


def make_clean_view(source: list[dict]) -> list[dict]:
    clean = copy.deepcopy(source)
    for sample in clean:
        for turn in iter_turns(sample):
            turn["asr_text"] = turn.get("text", "")
            turn["wer"] = 0.0
    return clean


def serialise_rng_state(rng: np.random.RandomState) -> dict:
    name, keys, position, has_gauss, cached_gaussian = rng.get_state()
    return {
        "name": name,
        "keys": keys.tolist(),
        "position": int(position),
        "has_gauss": int(has_gauss),
        "cached_gaussian": float(cached_gaussian),
    }


def restore_rng_state(rng: np.random.RandomState, state: dict) -> None:
    rng.set_state(
        (
            state["name"],
            np.asarray(state["keys"], dtype=np.uint32),
            int(state["position"]),
            int(state["has_gauss"]),
            float(state["cached_gaussian"]),
        )
    )


def package_versions() -> dict[str, str]:
    names = [
        "numpy",
        "scipy",
        "piper-tts",
        "pyroomacoustics",
        "openai-whisper",
        "torch",
    ]
    versions = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def whisper_checkpoint_info(model_name: str) -> dict:
    try:
        import whisper

        url = whisper._MODELS.get(model_name, "")  # upstream public registry
        cache_path = Path.home() / ".cache" / "whisper" / Path(url).name
        return {
            "model": model_name,
            "checkpoint_sha256": (sha256_file(cache_path) if cache_path.is_file() else None),
        }
    except Exception:
        return {"model": model_name, "checkpoint_sha256": None}


def voice_info(path: Path) -> dict:
    config_path = Path(f"{path}.json")
    if not path.is_file():
        raise FileNotFoundError(path)
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "config_filename": config_path.name,
        "config_sha256": sha256_file(config_path),
    }


def output_path(output_dir: Path, snr: int) -> Path:
    return output_dir / f"locomo10-asr-musan-full-snr{snr}.json"


def load_shards(state_dir: Path, fingerprint: str) -> list[dict]:
    shards = []
    for expected_index, path in enumerate(sorted(state_dir.glob("sample_*.json"))):
        with path.open(encoding="utf-8") as handle:
            shard = json.load(handle)
        if shard.get("sample_index") != expected_index:
            raise RuntimeError(f"non-contiguous generation checkpoint at {path}")
        if shard.get("config_fingerprint") != fingerprint:
            raise RuntimeError(f"checkpoint configuration mismatch at {path}")
        shards.append(shard)
    return shards


def assemble_outputs(
    output_dir: Path,
    snrs: list[int],
    shards: list[dict],
) -> dict[str, dict]:
    outputs = {}
    for snr in snrs:
        path = output_path(output_dir, snr)
        samples = [shard["tiers"][str(snr)] for shard in shards]
        atomic_json(path, samples)
        wers = [turn["wer"] for sample in samples for turn in iter_turns(sample)]
        outputs[path.name] = {
            "sha256": sha256_file(path),
            "conversations": len(samples),
            "turns": len(wers),
            "mean_wer": sum(wers) / len(wers) if wers else 0.0,
        }
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="upstream locomo10.json")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--musan-speech", required=True, type=Path)
    parser.add_argument("--voice-a", required=True, type=Path)
    parser.add_argument("--voice-b", required=True, type=Path)
    parser.add_argument("--snrs", nargs="+", type=float, default=[8, 7, 6, 5, 4, 3, 2])
    parser.add_argument("--rt60", type=float, default=0.4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--whisper-model", default="base.en")
    parser.add_argument("--max-convs", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--save-audio", type=Path, default=None)
    parser.add_argument("--save-audio-max", type=int, default=30)
    parser.add_argument("--allow-source-mismatch", action="store_true")
    parser.add_argument("--allow-asset-mismatch", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    if not args.musan_speech.is_dir():
        raise FileNotFoundError(args.musan_speech)
    if args.max_convs is not None and args.max_convs < 1:
        raise ValueError("--max-convs must be positive")
    if len(set(args.snrs)) != len(args.snrs):
        raise ValueError("--snrs contains duplicates")
    if any(not float(snr).is_integer() for snr in args.snrs):
        raise ValueError("release filenames require integer --snrs")
    snrs = [int(snr) for snr in args.snrs]

    source_sha256 = sha256_file(args.input)
    if source_sha256 != REFERENCE_SOURCE_SHA256 and not args.allow_source_mismatch:
        raise RuntimeError(
            f"source SHA-256 is {source_sha256}, expected "
            f"{REFERENCE_SOURCE_SHA256}; pass --allow-source-mismatch only if "
            "you intend to create a new dataset variant"
        )
    with args.input.open(encoding="utf-8") as handle:
        source = json.load(handle)
    selected_source = source[: args.max_convs]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    clean_path = args.output_dir / "locomo10-clean-asr-view.json"
    atomic_json(clean_path, make_clean_view(selected_source))

    voice_a = voice_info(args.voice_a)
    voice_b = voice_info(args.voice_b)
    validate_reference_voice(voice_a, role="voice-a", allow_mismatch=args.allow_asset_mismatch)
    validate_reference_voice(voice_b, role="voice-b", allow_mismatch=args.allow_asset_mismatch)

    print("Building deterministic MUSAN babble and physical RIR bank ...", flush=True)
    rng = np.random.RandomState(args.seed)
    noise_bank, musan_provenance = build_musan_babble(args.musan_speech, rng)
    rir_bank, rir_provenance = build_rir_bank(args.rt60, rng)
    versions = package_versions()

    configuration = {
        "pipeline": "piper-rir-musan-babble-whisper-v1",
        "source_sha256": source_sha256,
        "source_conversations": len(selected_source),
        "snr_db": snrs,
        "sample_rate_hz": SAMPLE_RATE,
        "rt60_seconds": args.rt60,
        "rir_count": 6,
        "musan_babble_clips": 60,
        "musan_talkers_per_clip": 6,
        "musan_segment_seconds": 8,
        "musan_source_pool_size": 120,
        "seed": args.seed,
        "whisper_model": args.whisper_model,
        "voice_a": voice_a,
        "voice_b": voice_b,
        "packages": versions,
        "musan_selected_sources": musan_provenance,
        "rir_bank": rir_provenance,
    }
    fingerprint = hashlib.sha256(
        json.dumps(configuration, sort_keys=True).encode("utf-8")
    ).hexdigest()
    state_dir = args.output_dir / ".generation_state" / fingerprint[:16]
    existing_shards = load_shards(state_dir, fingerprint) if state_dir.exists() else []
    if existing_shards and not args.resume:
        raise RuntimeError(
            f"checkpoint exists at {state_dir}; pass --resume or choose a new output directory"
        )
    if len(existing_shards) > len(selected_source):
        raise RuntimeError("checkpoint contains more conversations than this run")
    for index, shard in enumerate(existing_shards):
        if shard.get("sample_id") != selected_source[index].get("sample_id"):
            raise RuntimeError(f"source/checkpoint sample mismatch at index {index}")

    if existing_shards:
        restore_rng_state(rng, existing_shards[-1]["rng_state_after"])
        print(f"Resuming after {len(existing_shards)} conversations", flush=True)

    started = time.time()
    shards = list(existing_shards)
    audio_saved = min(
        sum(1 for sample in selected_source[: len(shards)] for _ in iter_turns(sample)),
        args.save_audio_max,
    )
    if args.save_audio:
        args.save_audio.mkdir(parents=True, exist_ok=True)

    base_manifest = {
        "schema_version": 1,
        "config_fingerprint": fingerprint,
        "configuration": configuration,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": versions,
            "whisper": whisper_checkpoint_info(args.whisper_model),
        },
        "musan_selected_sources": musan_provenance,
        "rir_bank": rir_provenance,
    }

    if len(shards) == len(selected_source):
        outputs = assemble_outputs(args.output_dir, snrs, shards)
        manifest = {
            **base_manifest,
            "progress": {
                "completed_conversations": len(shards),
                "target_conversations": len(selected_source),
                "complete": True,
                "elapsed_seconds_this_process": round(time.time() - started, 3),
            },
            "outputs": {
                clean_path.name: {
                    "sha256": sha256_file(clean_path),
                    "conversations": len(selected_source),
                    "turns": sum(
                        1 for source_sample in selected_source for _ in iter_turns(source_sample)
                    ),
                    "mean_wer": 0.0,
                },
                **outputs,
            },
        }
        atomic_json(args.output_dir / "generation_manifest.json", manifest)
        print(f"Already complete: {len(shards)} conversations", flush=True)
        return 0

    print("Loading Piper voices and Whisper model ...", flush=True)
    synthesizer = PiperSynthesizer(args.voice_a, args.voice_b)
    import whisper

    recognizer = whisper.load_model(args.whisper_model)

    for sample_index in range(len(shards), len(selected_source)):
        sample = selected_source[sample_index]
        sample_id = sample.get("sample_id", f"index-{sample_index}")
        turns = list(iter_turns(sample))
        speaker_a = sample["conversation"]["speaker_a"]
        print(
            f"[{sample_index + 1}/{len(selected_source)} {sample_id}] "
            f"{len(turns)} turns x {len(snrs)} SNR tiers",
            flush=True,
        )
        results: dict[int, dict[str, tuple[str, float]]] = {snr: {} for snr in snrs}

        for turn_index, turn in enumerate(turns):
            dia_id = turn["dia_id"]
            original = turn.get("text", "")
            clean_audio = synthesizer.synthesize(original, turn.get("speaker") == speaker_a)
            rir = rir_bank[int(rng.randint(len(rir_bank)))]
            reverberant = sps.fftconvolve(clean_audio, rir)[: len(clean_audio)].astype(np.float32)
            noise = noise_bank[int(rng.randint(len(noise_bank)))]
            noise = np.tile(noise, int(np.ceil(len(reverberant) / len(noise))))[: len(reverberant)]
            noise = np.roll(noise, int(rng.randint(len(noise))))
            save_this = bool(args.save_audio and audio_saved < args.save_audio_max)
            file_stem = f"{sample_id}_{dia_id.replace(':', '_')}"
            if save_this:
                write_wav(clean_audio, args.save_audio / f"{file_stem}_clean.wav")

            if args.verbose:
                print(f"  [{dia_id}] {original[:80]}", flush=True)
            for snr in snrs:
                degraded = mix_at_snr(reverberant, noise, snr)
                hypothesis = recognizer.transcribe(degraded, language="en", fp16=False)[
                    "text"
                ].strip()
                measured_wer = round(word_error_rate(original, hypothesis), 4)
                results[snr][dia_id] = (hypothesis, measured_wer)
                if save_this:
                    write_wav(
                        degraded,
                        args.save_audio / f"{file_stem}_snr{snr}.wav",
                    )
                if args.verbose:
                    print(
                        f"      SNR{snr}: WER={measured_wer:.4f} | {hypothesis[:72]}",
                        flush=True,
                    )
            if save_this:
                audio_saved += 1
            if not args.verbose and (turn_index + 1) % 40 == 0:
                print(f"    {turn_index + 1}/{len(turns)} turns", flush=True)

        tier_samples = {}
        for snr in snrs:
            generated = copy.deepcopy(sample)
            for turn in iter_turns(generated):
                hypothesis, measured_wer = results[snr][turn["dia_id"]]
                turn["asr_text"] = hypothesis
                turn["wer"] = measured_wer
            tier_samples[str(snr)] = generated

        shard = {
            "schema_version": 1,
            "config_fingerprint": fingerprint,
            "sample_index": sample_index,
            "sample_id": sample_id,
            "tiers": tier_samples,
            "rng_state_after": serialise_rng_state(rng),
        }
        shard_path = state_dir / f"sample_{sample_index:03d}.json"
        atomic_json(shard_path, shard)
        shards.append(shard)
        outputs = assemble_outputs(args.output_dir, snrs, shards)
        manifest = {
            **base_manifest,
            "progress": {
                "completed_conversations": len(shards),
                "target_conversations": len(selected_source),
                "complete": len(shards) == len(selected_source),
                "elapsed_seconds_this_process": round(time.time() - started, 3),
            },
            "outputs": {
                clean_path.name: {
                    "sha256": sha256_file(clean_path),
                    "conversations": len(selected_source),
                    "turns": sum(
                        1 for source_sample in selected_source for _ in iter_turns(source_sample)
                    ),
                    "mean_wer": 0.0,
                },
                **outputs,
            },
        }
        atomic_json(args.output_dir / "generation_manifest.json", manifest)
        print(
            f"  saved conversation {sample_id}; checkpoint={shard_path}",
            flush=True,
        )

    if not selected_source:
        raise RuntimeError("source dataset contains no conversations")
    print(
        f"Complete: {len(shards)} conversations in {(time.time() - started) / 60:.1f} minutes",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
