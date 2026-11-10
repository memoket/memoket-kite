"""Shared helpers for reproducibility commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from benchmarks.common.paths import ARTIFACTS_ROOT, REPO_ROOT

MANIFEST_PATH = Path(__file__).with_name("manifest.json")


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported reproduction manifest schema")
    return manifest


def repository_path(value: str) -> Path:
    path = (REPO_ROOT / value).resolve()
    if not path.is_relative_to(REPO_ROOT):
        raise ValueError(f"manifest path escapes the repository: {value}")
    return path


def dataset_path(value: str) -> Path:
    relative = PurePosixPath(value).relative_to("artifacts/datasets")
    root = ARTIFACTS_ROOT / "datasets"
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"manifest dataset path escapes the dataset directory: {value}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
