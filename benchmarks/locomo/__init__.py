"""LoCoMo benchmark."""

from pathlib import PurePosixPath

from benchmarks.common.paths import safe_tag
from benchmarks.reproduce.common import load_manifest

DEFAULT_TAG = "example-free-v1"


def evaluation_tag(value: str) -> str:
    tag = safe_tag(value)
    reference_path = load_manifest()["benchmarks"]["locomo"]["result_path"]
    reference_tag = PurePosixPath(reference_path).parent.name.removeprefix("locomo-")
    if tag.casefold() in {reference_tag.casefold(), "v0.1.0"}:
        raise ValueError(f"tag {tag!r} is reserved for historical LoCoMo artifacts")
    return tag
