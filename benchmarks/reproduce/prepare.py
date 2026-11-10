"""Download and verify pinned datasets or GitHub Release artifacts."""

from __future__ import annotations

import argparse
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from benchmarks.common.paths import ARTIFACTS_ROOT, REPO_ROOT
from benchmarks.reproduce.common import dataset_path, load_manifest, sha256


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with urllib.request.urlopen(url) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        actual = sha256(temporary)
        if actual != expected_sha256:
            raise RuntimeError(
                f"checksum mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_datasets(manifest: dict) -> None:
    for name, dataset in manifest["datasets"].items():
        destination = dataset_path(dataset["path"])
        if destination.exists() and sha256(destination) == dataset["sha256"]:
            print(f"{name}: verified {destination}")
            continue
        print(f"{name}: downloading {dataset['url']}")
        _download(dataset["url"], destination, dataset["sha256"])
        print(f"{name}: verified {destination}")


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            if member.issym() or member.islnk():
                raise RuntimeError(f"archive links are not allowed: {member.name}")
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError(f"unsafe archive member: {member.name}")
        bundle.extractall(destination)


def prepare_artifacts(manifest: dict) -> None:
    release = manifest["release"]["version"]
    base_url = f"https://github.com/memoket/memoket-kite/releases/download/{release}"
    download_dir = ARTIFACTS_ROOT / "downloads" / release
    for name, benchmark in manifest["benchmarks"].items():
        expected = benchmark.get("asset_sha256")
        if not expected:
            raise RuntimeError(
                f"{name} release asset is not published in manifest.json; run the full evaluation first"
            )
        archive = download_dir / benchmark["asset"]
        if not archive.exists() or sha256(archive) != expected:
            _download(f"{base_url}/{benchmark['asset']}", archive, expected)
        _safe_extract(archive, REPO_ROOT)
        print(f"{name}: installed verified release artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=("datasets", "artifacts"))
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.target == "datasets":
        prepare_datasets(manifest)
    else:
        prepare_artifacts(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
