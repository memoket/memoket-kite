"""Convert an explicitly selected local recording into a KITE source input."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory

from .contracts import HASH, TEXT, ToolError, schema_file, validate
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
        raise ToolError(
            "transcription_timeout",
            f"{tool} timed out after {timeout} seconds. Increase --timeout.",
        ) from exc
    except subprocess.CalledProcessError as exc:
        hint = (
            "Check that the file has an audio track in a supported format."
            if tool == "ffmpeg"
            else "Check that --model points to a compatible whisper.cpp model and --language is supported."
        )
        raise ToolError(
            "transcription_error", f"{tool} could not process the recording. {hint}"
        ) from exc


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
    if not segments or all(segment["text"].strip() == "[BLANK_AUDIO]" for segment in segments):
        raise ToolError("no_speech", "whisper-cli returned no spoken transcript segments")
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
    source_date: str | None = None,
    started_at: str | None = None,
    expected_checksum: str | None = None,
) -> dict:
    validate(TEXT, source_id, "recording identifier")
    if not re.fullmatch(r"auto|[a-z]{2,3}", language) or timeout < 1:
        raise ToolError("invalid_input", "provide a Whisper language code and positive timeout")
    if source_date is not None and started_at is not None:
        raise ToolError("invalid_input", "supply only one of --source-date and --started-at")
    dates = {}
    for field, value in (("source_date", source_date), ("started_at", started_at)):
        if value is not None:
            validate(schema_file(root, "conversation")["properties"][field], value, field)
            dates[field] = value
    if expected_checksum is not None:
        validate(HASH, expected_checksum, "expected checksum")
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
        **dates,
    }
    if detected:
        conversation["language"] = detected
    validate_conversation(root, conversation)
    del conversation["source"]
    source = {"format": "audio-transcript", "payload": conversation}
    if expected_checksum is not None:
        source["expected_checksum"] = expected_checksum
    return {"source": source}
