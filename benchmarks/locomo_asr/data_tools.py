"""Dependency-free helpers shared by LoCoMo-ASR tooling."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SESSION_KEY = re.compile(r"^session_\d+$")


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    """Load strict UTF-8 JSON, rejecting non-standard NaN/Infinity values."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"{path} contains non-standard JSON constant {value}")

    with path.open(encoding="utf-8") as handle:
        return json.load(handle, parse_constant=reject_constant)


def iter_sessions(sample: dict[str, Any]) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Yield session keys and turn lists in their original order."""

    conversation = sample["conversation"]
    for key, turns in conversation.items():
        if SESSION_KEY.fullmatch(key):
            yield key, turns


def iter_turns(sample: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every dialogue turn in original session and turn order."""

    for _, turns in iter_sessions(sample):
        yield from turns


def normalise_word(word: str) -> str:
    """Apply the normalization used by the frozen reference WER values."""

    return re.sub(r"[^a-z0-9']", "", word.lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Compute word-level Levenshtein distance divided by reference length."""

    ref = [normalise_word(word) for word in reference.split()]
    hyp = [normalise_word(word) for word in hypothesis.split()]
    ref = [word for word in ref if word]
    hyp = [word for word in hyp if word]
    if not ref:
        return 0.0

    previous = list(range(len(hyp) + 1))
    for row, ref_word in enumerate(ref, 1):
        current = [row]
        for column, hyp_word in enumerate(hyp, 1):
            if ref_word == hyp_word:
                current.append(previous[column - 1])
            else:
                current.append(
                    1
                    + min(
                        previous[column],
                        current[column - 1],
                        previous[column - 1],
                    )
                )
        previous = current
    return previous[-1] / len(ref)
