"""Bind resumable benchmark Codebooks to their construction inputs."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from benchmarks.common import settings
from memoket_kite.core import vocab
from memoket_kite.pipeline import extract
from memoket_kite.pipeline.compile_plan import _provider_cache_identity
from memoket_kite.prompts import extract as prompts
from memoket_kite.providers import llm


@lru_cache
def _protocol_sha(builder: str, adapter: str, profile: str) -> str:
    files = (
        builder,
        adapter,
        profile,
        *(m.__file__ for m in (settings, vocab, extract, prompts, llm)),
    )
    checksum = hashlib.sha256()
    for filename in files:
        source = Path(filename).read_text(encoding="utf-8")
        checksum.update(hashlib.sha256(source.encode()).digest())
    return checksum.hexdigest()


def build_attributes(
    model: str, source: dict, *, builder: str, adapter: str, profile: str
) -> dict[str, str]:
    encoded = json.dumps(source, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return {
        "build_model": model,
        "build_provider_sha256": hashlib.sha256(_provider_cache_identity().encode()).hexdigest(),
        "build_input_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "build_protocol_sha256": _protocol_sha(builder, adapter, profile),
    }


def reuse_existing(output: Path, attributes: dict[str, str], *, resume: bool, force: bool) -> bool:
    if force or not output.exists():
        return False
    if not resume:
        raise RuntimeError(f"Codebook exists; pass --resume or --force: {output}")
    try:
        root = ET.parse(output).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(
            f"Cannot read existing Codebook; rebuild with --force: {output}"
        ) from exc
    different = [key for key, value in attributes.items() if root.get(key) != value]
    if root.tag != "codebook" or root.get("id") != output.stem:
        different.insert(0, "codebook ID")
    if different:
        raise RuntimeError(
            f"Codebook build provenance is missing or differs ({', '.join(different)}): {output}. "
            "Use a separate KITE_ARTIFACTS_DIR or rebuild with --force."
        )
    return True
