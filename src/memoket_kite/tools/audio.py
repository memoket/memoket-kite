"""Convert an explicitly selected local recording into a KITE source input."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory

from .contracts import TEXT, ToolError, validate
from .ingest import validate_conversation

_AUDIO_FORMATS = "wav,mp3,aac,mov,ogg,flac,matroska,webm,aiff"


def _run(command: list[str], tool: str, timeout: int) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ToolError("missing_dependency", f"install {tool} or select its executable") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("transcription_timeout", f"{tool} timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise ToolError("transcription_error", f"{tool} could not process the recording") from exc


def _transcript(path: Path) -> tuple[list[dict], str | None]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        language = result.get("result", {}).get("language")
        segments = []
        for number, line in enumerate(result["transcription"], 1):
            if line["text"].strip():
                segments.append(
                    {
                        "segment_id": f"audio-{number}",
                        "text": line["text"],
                        "start_ms": line["offsets"]["from"],
                        "end_ms": line["offsets"]["to"],
                    }
                )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ToolError(
            "transcription_error", "whisper-cli returned an invalid transcript"
        ) from exc
    if not segments:
        raise ToolError("no_speech", "whisper-cli returned no nonblank transcript segments")
    return segments, language


def audio_source(
    root: Path,
    recording: Path,
    model: Path,
    *,
    source_id: str,
    language: str,
    ffmpeg: str,
    whisper: str,
    timeout: int,
) -> dict:
    validate(TEXT, source_id, "recording identifier")
    if not re.fullmatch(r"auto|[a-z]{2,3}", language) or timeout < 1:
        raise ToolError("invalid_input", "provide a Whisper language code and positive timeout")
    recording, model = recording.expanduser().resolve(), model.expanduser().resolve()
    if not recording.is_file() or not model.is_file():
        raise ToolError("invalid_input", "recording and model must both be local files")

    ffmpeg_path = which(ffmpeg)
    whisper_path = which(whisper)
    if ffmpeg_path is None or whisper_path is None:
        missing = "ffmpeg" if ffmpeg_path is None else "whisper-cli"
        raise ToolError("missing_dependency", f"install {missing} or select its executable")

    before = recording.stat()
    checksum = hashlib.sha256()
    with recording.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)

    with TemporaryDirectory(prefix="kite-transcription-") as temporary:
        directory = Path(temporary)
        waveform = directory / "recording.wav"
        output = directory / "transcript"
        _run(
            [
                ffmpeg_path,
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                _AUDIO_FORMATS,
                "-i",
                str(recording),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(waveform),
            ],
            "ffmpeg",
            timeout,
        )
        after = recording.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ToolError("conflict", "recording changed during transcription preparation")
        _run(
            [
                whisper_path,
                "-m",
                str(model),
                "-f",
                str(waveform),
                "-l",
                language,
                "-oj",
                "-of",
                str(output),
            ],
            "whisper-cli",
            timeout,
        )
        segments, detected = _transcript(output.with_suffix(".json"))

    conversation = {
        "conversation_id": source_id,
        "source": "audio-transcript",
        "metadata": {"source_reference": source_id, "audio_sha256": checksum.hexdigest()},
        "segments": segments,
    }
    if detected:
        conversation["language"] = detected
    validate_conversation(root, conversation)
    del conversation["source"]
    return {"source": {"format": "audio-transcript", "payload": conversation}}
