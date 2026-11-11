"""Package verified benchmark artifacts and Python distributions for a release."""

from __future__ import annotations

import argparse
import base64
import copy
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager
from email.parser import BytesHeaderParser
from email.policy import default as email_policy
from pathlib import Path, PurePosixPath

from benchmarks.common.paths import ARTIFACTS_ROOT, REPO_ROOT
from benchmarks.locomo.protocol import summarize as summarize_locomo
from benchmarks.longmemeval.protocol import summarize as summarize_longmemeval
from benchmarks.reproduce import sealed
from benchmarks.reproduce.common import artifact_path, dataset_path, load_manifest, sha256


def _toml_section(document: str, name: str) -> str:
    """Return one simple TOML table without requiring Python 3.11's tomllib."""
    match = re.search(
        rf"(?ms)^\[{re.escape(name)}\]\s*$\n(.*?)(?=^\[|\Z)",
        document,
    )
    if match is None:
        raise RuntimeError(f"pyproject.toml declares no [{name}] table")
    return match.group(1)


def _toml_string(table: str, key: str) -> str:
    match = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"([^"]*)"\s*$', table)
    if match is None:
        raise RuntimeError(f"pyproject.toml declares no string value for {key}")
    return match.group(1)


def _toml_string_array(table: str, key: str) -> list[str]:
    """Parse the small string-array subset used by project dependencies."""
    match = re.search(rf"(?m)^{re.escape(key)}\s*=\s*\[", table)
    if match is None:
        raise RuntimeError(f"pyproject.toml declares no string-array value for {key}")

    start = match.end()
    escaped = False
    quoted = False
    end = start
    for end in range(start, len(table)):
        character = table[end]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character == "]":
            break
    else:
        raise RuntimeError(f"pyproject.toml contains an unterminated array for {key}")
    if quoted:
        raise RuntimeError(f"pyproject.toml contains an unterminated string for {key}")

    body = table[start:end]
    raw_values = re.findall(r'"(?:\\.|[^"\\])*"', body)
    residue = re.sub(r'"(?:\\.|[^"\\])*"', "", body)
    residue = re.sub(r"(?m)#.*$", "", residue)
    if residue.strip(" \t\r\n,"):
        raise RuntimeError(f"pyproject.toml {key} must contain only basic strings")
    try:
        values = [json.loads(value) for value in raw_values]
    except json.JSONDecodeError as error:
        raise RuntimeError(f"pyproject.toml contains an invalid string in {key}") from error
    if any(not isinstance(value, str) for value in values):
        raise RuntimeError(f"pyproject.toml {key} must contain only strings")
    return values


#: The distribution identity this tree actually declares. Reading the small
#: string-only fields directly keeps the source release usable on Python 3.10,
#: while still anchoring artifact metadata to pyproject.toml rather than to an
#: installed (and possibly stale) copy of the package.
_PYPROJECT_BYTES = (REPO_ROOT / "pyproject.toml").read_bytes()
_PYPROJECT_TEXT = _PYPROJECT_BYTES.decode("utf-8")
_PROJECT_TABLE = _toml_section(_PYPROJECT_TEXT, "project")
_URL_TABLE = _toml_section(_PYPROJECT_TEXT, "project.urls")
_OPTIONAL_DEPENDENCIES_TABLE = _toml_section(_PYPROJECT_TEXT, "project.optional-dependencies")
_DISTRIBUTION = _toml_string(_PROJECT_TABLE, "name")
_PACKAGE = "memoket_kite"
_VERSION = _toml_string(_PROJECT_TABLE, "version")
_DESCRIPTION = _toml_string(_PROJECT_TABLE, "description")
_REQUIRES_PYTHON = _toml_string(_PROJECT_TABLE, "requires-python")
_LICENSE_EXPRESSION = _toml_string(_PROJECT_TABLE, "license")
_README_NAME = _toml_string(_PROJECT_TABLE, "readme")
_PROJECT_URLS = {
    key: value
    for key, value in re.findall(r'(?m)^([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"([^"]*)"\s*$', _URL_TABLE)
}
if not _PROJECT_URLS:
    raise RuntimeError("pyproject.toml declares no project URLs")
_OPTIONAL_DEPENDENCIES = {
    name: _toml_string_array(_OPTIONAL_DEPENDENCIES_TABLE, name)
    for name in re.findall(
        r"(?m)^([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*\[", _OPTIONAL_DEPENDENCIES_TABLE
    )
}
_PROVIDES_EXTRA = {re.sub(r"[-_.]+", "-", name).lower() for name in _OPTIONAL_DEPENDENCIES}
_REQUIRES_DIST = set(_toml_string_array(_PROJECT_TABLE, "dependencies"))
for extra, requirements in _OPTIONAL_DEPENDENCIES.items():
    normalized_extra = re.sub(r"[-_.]+", "-", extra).lower()
    for requirement in requirements:
        dependency, separator, marker = requirement.partition(";")
        if separator:
            rendered = f'{dependency.strip()}; ({marker.strip()}) and extra == "{normalized_extra}"'
        else:
            rendered = f'{requirement.strip()}; extra == "{normalized_extra}"'
        _REQUIRES_DIST.add(rendered)

SUMMARIZERS = {"locomo": summarize_locomo, "longmemeval": summarize_longmemeval}


#: Credential-shaped strings. Rule names, never matched bytes, are safe to put
#: in CI output. A benchmark corpus can legitimately contain a credential-shaped
#: string, so this remains a mechanical release gate rather than an assumption.
_SECRET_RULES = (
    ("GitHub token", re.compile(rb"gh[oprsu]_[A-Za-z0-9]{20,}")),
    ("GitHub fine-grained token", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}")),
    ("OpenAI-style API key", re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    ("AWS access key id", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("Slack token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("PEM private key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def _scan_bytes(payload: bytes, hits: list[str]) -> None:
    for rule_name, pattern in _SECRET_RULES:
        if pattern.search(payload):
            hits.append(rule_name)


def _raise_secret_hits(hits: list[str]) -> None:
    if hits:
        rules = list(dict.fromkeys(hits))
        raise RuntimeError(
            "refusing to package credential-shaped content; matched rule(s):\n  "
            + "\n  ".join(rules[:5])
            + "\nRedact the source data and rebuild the corpus before releasing."
        )


def _refuse_secret_payload(payload: bytes) -> None:
    hits: list[str] = []
    _scan_bytes(payload, hits)
    _raise_secret_hits(hits)


def _refuse_secrets(entries: list[tuple[Path, str]]) -> None:
    """Refuse to package any file holding a credential-shaped string.

    Container files are opened and their members scanned: a token inside a
    wheel or tarball does not appear in the container's compressed bytes, so
    scanning the container as a flat file would clear exactly the archives most
    likely to carry one.
    """
    hits: list[str] = []
    for base, _arcname in entries:
        members = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        for path in members:
            name = path.name
            if name.endswith((".whl", ".zip")):
                _scan_bytes(path.read_bytes(), hits)
                with zipfile.ZipFile(path) as bundle:
                    _scan_bytes(bundle.comment, hits)
                    for info in bundle.infolist():
                        _scan_bytes(info.filename.encode("utf-8", errors="surrogateescape"), hits)
                        _scan_bytes(info.comment, hits)
                        _scan_bytes(info.extra, hits)
                        if not info.is_dir():
                            _scan_bytes(bundle.read(info), hits)
            elif name.endswith((".tar.gz", ".tgz", ".tar")):
                _scan_bytes(path.read_bytes(), hits)
                with tarfile.open(path) as bundle:
                    for key, value in bundle.pax_headers.items():
                        _scan_bytes(
                            f"{key}={value}".encode("utf-8", errors="surrogateescape"), hits
                        )
                    for member in bundle.getmembers():
                        metadata = [member.name, member.linkname, member.uname, member.gname]
                        metadata.extend(
                            f"{key}={value}" for key, value in member.pax_headers.items()
                        )
                        for value in metadata:
                            _scan_bytes(str(value).encode("utf-8", errors="surrogateescape"), hits)
                        if member.isfile() and (payload := bundle.extractfile(member)):
                            _scan_bytes(payload.read(), hits)
            else:
                _scan_bytes(path.read_bytes(), hits)
    _raise_secret_hits(hits)


def _archive(entries: list[tuple[Path, str]], output: Path) -> None:
    """Create a path-stable gzip tarball, each entry unpacked at its own name.

    Every entry states the name it unpacks to instead of having one derived
    from where its source file sits under the repository. A run points
    `--plan-cache` wherever it likes, so the plan cache need not live inside
    the tree at all and has no repository-relative position to infer from.
    """
    _refuse_secrets(entries)
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as bundle:
            for base, arcname in entries:
                if not base.exists():
                    raise RuntimeError(f"required release path is missing: {base}")
                members = [base] if base.is_file() else [base, *sorted(base.rglob("*"))]
                for path in members:
                    if path.is_symlink():
                        raise RuntimeError(f"release archives cannot contain symlinks: {path}")
                    name = Path(arcname).joinpath(path.relative_to(base)).as_posix()
                    info = bundle.gettarinfo(str(path), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    if path.is_file():
                        with path.open("rb") as stream:
                            bundle.addfile(info, stream)
                    else:
                        bundle.addfile(info)


def _current_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


#: Everything a number can come from — the pipeline, and the profiles,
#: protocols, evaluators and scorers that drive it. A commit that leaves these
#: untouched cannot have changed a score, whatever else it changed.
#:
#: `benchmarks/reproduce` is excluded because it only ever reads sealed
#: artifacts: it packages, verifies and downloads, and never produces an answer
#: or a verdict. Including it would leave the release tooling unable to describe
#: its own release — writing the measured score into this manifest would itself
#: count as changing the measurement.
MEASURED_PATHS = ("src", "benchmarks", ":(exclude)benchmarks/reproduce")


def _source_commit() -> str:
    """HEAD at packaging time, with the whole tracked tree required clean.

    `_released_commit` only refuses uncommitted MEASURED paths, but a dirty
    README or LICENSE still travels inside the sdist and the wheel METADATA,
    so the artifact would carry bytes that exist at no commit at all.
    """
    pending = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True
    ).strip()
    tracked = [line for line in pending.splitlines() if not line.startswith("??")]
    if tracked:
        raise RuntimeError(
            "the working tree has uncommitted tracked changes:\n"
            + "\n".join(tracked[:10])
            + "\nCommit them before packaging: the distributions are built from this tree."
        )
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _pending_measured_changes() -> str:
    """Uncommitted changes under the paths a score can depend on."""
    return subprocess.check_output(
        ["git", "status", "--porcelain", "--", *MEASURED_PATHS], cwd=REPO_ROOT, text=True
    ).strip()


def _released_commit(manifest: dict, *, allow_unreachable: bool = False) -> str:
    """The commit the sealed results were actually produced by.

    Stamping the release with whatever HEAD happens to be says the numbers came
    from the code being published when they may have come from anything. Every
    benchmark's run manifest names its own commit and whether the tree was
    dirty; they must agree with each other and be clean.

    They need not be HEAD itself: writing the measured score into the README is
    a commit, and it lands after the measurement by definition. The rule is
    therefore that the measured commit is reachable from HEAD and nothing under
    `MEASURED_PATHS` has moved since. A README may change; a profile, a
    protocol or the pipeline may not.

    `allow_unreachable` waives the reachability half for a bundle that is not
    being published: results measured across a rewritten history still describe
    a real run, and a private bundle has no downloader to mislead. The waiver
    is recorded in the manifest, so a bundle built under it says so rather than
    passing itself off as a verified release.
    """
    # The packager's own tree gets the same rule the measured runs get: a
    # release stamped with the measured commit while uncommitted edits sit in
    # a measured path would ship code nobody can check out under a commit
    # that says otherwise.
    pending = _pending_measured_changes()
    if pending:
        raise RuntimeError(
            f"the working tree has uncommitted changes in measured paths:\n{pending}\n"
            f"commit or stash them before packaging"
        )
    commits = {}
    for name, specification in manifest["benchmarks"].items():
        run = artifact_path(specification["result_path"]).parent / "manifest.json"
        if not run.exists():
            raise RuntimeError(f"{name}: no run manifest, so what produced it is unknown")
        recorded = json.loads(run.read_text())
        if recorded.get("dirty"):
            raise RuntimeError(
                f"{name}: was measured on a dirty tree ({recorded.get('diff_sha')}), which is "
                f"not a state anyone can check out; re-run it on a committed tree"
            )
        commits[name] = recorded.get("commit")
    if len(set(commits.values())) != 1:
        if not allow_unreachable:
            raise RuntimeError(
                f"benchmarks were measured on different commits: {commits}; re-run them "
                f"on one commit, or pass --allow-unreachable-commit for a private bundle"
            )
        # No single commit describes this bundle, so name the one the results
        # were sorted to rather than inventing agreement.
        return sorted(filter(None, commits.values()))[0]
    measured = next(iter(commits.values()))
    head = _current_commit()
    if measured != head:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", measured, head], cwd=REPO_ROOT
        )
        if ancestor.returncode != 0 and not allow_unreachable:
            raise RuntimeError(
                f"the sealed results were produced by {measured[:12]}, which is not an "
                f"ancestor of HEAD ({head[:12]}); release the commit that was measured, "
                f"re-run on this one, or pass --allow-unreachable-commit for a private "
                f"bundle"
            )
        moved = subprocess.check_output(
            ["git", "diff", "--name-only", measured, head, "--", *MEASURED_PATHS],
            cwd=REPO_ROOT,
            text=True,
        ).split()
        if moved:
            raise RuntimeError(
                f"the sealed results were produced by {measured[:12]}, and "
                f"{len(moved)} measured file(s) have changed since — {', '.join(moved[:3])}"
                f"{' ...' if len(moved) > 3 else ''}; re-run on HEAD before releasing"
            )
    return measured


def _require_declared_models(name: str, result_dir: Path, declared: dict) -> None:
    """The models the release advertises must be the ones the run recorded.

    The release manifest and the run manifest name models independently, and a
    dated snapshot in one reads exactly as authoritative as a floating alias in
    the other. Only the run's names the model that produced the numbers, while
    a reader reproducing the release pins whatever the release manifest says,
    so whichever form is written the two must agree.
    """
    run = json.loads((result_dir / "manifest.json").read_text())
    judge = json.loads((result_dir / "judge_manifest.json").read_text())
    for role, recorded in (
        ("compile", run.get("model")),
        ("answer", run.get("answer_model")),
        ("judge", judge.get("judge_model")),
    ):
        if declared.get(role) != recorded:
            raise RuntimeError(
                f"{name}: the release manifest declares {role}={declared.get(role)!r}, but the "
                f"run recorded {recorded!r}; name the model that was actually used"
            )


def _verified_plan_cache(name: str, result_dir: Path, *, allow_unreachable: bool = False) -> Path:
    """The plan cache this run used, confirmed to be the one it used.

    Packaging a fixed `artifacts/cache/plans/<tag>` path would ship whatever
    happens to sit there, which need not be the cache behind these answers:
    `--plan-cache` points wherever the run chose. The run manifest records both
    that path and a digest of the cache's contents, so the release takes both
    from there and re-derives the digest before shipping it.

    `allow_unreachable` accepts a cache that has grown since. The cache is
    append-only: a compile that hits returns the stored plan and a compile that
    misses writes a new key, so no entry is ever rewritten or removed. A later
    run on the same directory therefore leaves everything this run read intact,
    and the bundle still carries every plan behind these answers. The digest is
    exact identity, which is a stronger property than the one that matters, so
    the waiver is recorded rather than silent.
    """
    from benchmarks.common import manifest as run_manifest

    recorded = json.loads((result_dir / "manifest.json").read_text())
    location = recorded.get("plan_cache")
    if not location:
        raise RuntimeError(
            f"{name}: the run records no plan cache, so the compiled plans behind these "
            f"answers cannot be shipped; re-run with --plan-cache"
        )
    cache = Path(location)
    if not cache.is_dir():
        raise RuntimeError(f"{name}: the plan cache the run used is gone ({cache})")
    current = run_manifest.plan_cache_digest(cache)
    if current != recorded.get("plan_cache_sha") and not allow_unreachable:
        raise RuntimeError(
            f"{name}: the plan cache at {cache} is now {current}, but the run used "
            f"{recorded.get('plan_cache_sha')}; restore it, or pass "
            f"--allow-unreachable-commit if later runs have only added to it"
        )
    return cache


def _require_directory_files(
    directory: Path,
    expected_names: set[str],
    *,
    ignored_names: set[str] | None = None,
) -> list[Path]:
    """Return an exact regular-file allowlist and reject every opaque extra."""
    ignored_names = ignored_names or set()
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError(f"release input is not a real directory: {directory}")
    children = list(directory.iterdir())
    actual_names = {path.name for path in children}
    missing = sorted(expected_names - actual_names)
    unexpected = sorted(actual_names - expected_names - ignored_names)
    unsafe = sorted(
        path.name
        for path in children
        if path.name in expected_names | ignored_names and (not path.is_file() or path.is_symlink())
    )
    if missing or unexpected or unsafe:
        details = []
        if missing:
            details.append(f"missing {missing[:3]}")
        if unexpected:
            details.append(f"unexpected {unexpected[:3]}")
        if unsafe:
            details.append(f"non-regular {unsafe[:3]}")
        raise RuntimeError(
            f"release directory {directory} violates its file allowlist ({'; '.join(details)})"
        )
    return [directory / name for name in sorted(expected_names)]


def _require_loadable_codebooks(name: str, directory: Path, expected_count: int) -> list[Path]:
    """Require every published Codebook to satisfy the current public loader."""
    paths = sorted(directory.glob("*.xml")) if directory.is_dir() else []
    if len(paths) != expected_count:
        raise RuntimeError(f"{name}: expected {expected_count} Codebooks, found {len(paths)}")
    # Imported lazily so merely inspecting release metadata does not initialize
    # the library. A file count says nothing about loadability: a Codebook whose
    # controlled-vocabulary or provenance references dangle is still a
    # well-formed XML file, and the release wheel cannot open it.
    from memoket_kite.core.algebra import Store

    for path in paths:
        try:
            Store.load([str(path)])
        except Exception as exc:
            raise RuntimeError(
                f"{name}: Codebook {path.name} fails the current loader; rebuild the corpus"
            ) from exc
    return paths


def _release_archive_entries(
    name: str,
    specification: dict,
    codebooks: Path,
    plan_cache: Path,
    result_dir: Path,
    tag: str,
) -> list[tuple[Path, str]]:
    """Enumerate every release member; no source directory is recursively copied."""
    codebook_names = {path.name for path in codebooks.glob("*.xml")}
    codebook_files = _require_directory_files(codebooks, codebook_names)

    plan_names = {
        path.name for path in plan_cache.iterdir() if re.fullmatch(r"[0-9a-f]{64}\.json", path.name)
    }
    if not plan_names:
        raise RuntimeError(f"{name}: verified plan cache contains no keyed JSON plans")
    plan_files = _require_directory_files(plan_cache, plan_names)
    for path in plan_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"{name}: plan cache contains malformed JSON: {path.name}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"{name}: plan cache entry is not a JSON object: {path.name}")

    source_names = (
        {path.name for path in result_dir.glob("results_*.jsonl")}
        if name == "locomo"
        else {"results.jsonl"}
    )
    result_names = {
        "manifest.json",
        "judge_manifest.json",
        "judged.jsonl",
        "score.json",
        *source_names,
    }
    result_files = _require_directory_files(
        result_dir,
        result_names,
        ignored_names={"judge_cache.json"},
    )

    licence_name = {
        "locomo": "LoCoMo-CC-BY-NC-4.0.txt",
        "longmemeval": "LongMemEval-MIT.txt",
    }[name]
    licence = REPO_ROOT / "licenses" / "third-party" / licence_name
    if not licence.is_file() or licence.is_symlink():
        raise RuntimeError(f"{name}: required dataset licence is missing: {licence}")

    result_arc = f"artifacts/results/{result_dir.name}"
    return [
        *((path, f"{specification['codebook_path']}/{path.name}") for path in codebook_files),
        *((path, f"artifacts/cache/plans/{name}-{tag}/{path.name}") for path in plan_files),
        *((path, f"{result_arc}/{path.name}") for path in result_files),
        (REPO_ROOT / "LICENSE-DATA.md", "LICENSE-DATA.md"),
        (licence, f"licenses/third-party/{licence.name}"),
    ]


def _archive_parents(files: set[str]) -> set[str]:
    parents: set[str] = set()
    for name in files:
        for parent in PurePosixPath(name).parents:
            if parent != PurePosixPath("."):
                parents.add(parent.as_posix())
    return parents


def _canonical_member_name(raw: str, *, directory: bool) -> str:
    name = raw[:-1] if directory and raw.endswith("/") else raw
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or ".." in pure.parts or pure.as_posix() != name:
        raise RuntimeError(f"distribution contains an unsafe archive member name: {raw!r}")
    return name


def _require_exact_members(path: Path, actual: set[str], expected: set[str]) -> None:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing {missing[:3]}")
        if unexpected:
            details.append(f"unexpected member(s) {unexpected[:3]}")
        raise RuntimeError(
            f"{path.name} has the wrong member set ({'; '.join(details)}); "
            "clear dist/ and rebuild before packaging"
        )


def _read_sdist(path: Path, expected_files: set[str]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    directories: set[str] = set()
    seen: set[str] = set()
    with tarfile.open(path) as bundle:
        for member in bundle.getmembers():
            name = _canonical_member_name(member.name, directory=member.isdir())
            if name in seen:
                raise RuntimeError(f"{path.name} contains duplicate member {name!r}")
            seen.add(name)
            if member.isdir():
                if member.size:
                    raise RuntimeError(
                        f"{path.name} contains a directory member with data: {name!r}"
                    )
                directories.add(name)
                continue
            if not member.isfile():
                raise RuntimeError(f"{path.name} contains non-regular member {name!r}; rebuild it")
            stream = bundle.extractfile(member)
            if stream is None:
                raise RuntimeError(f"cannot read {name!r} from {path.name}")
            payloads[name] = stream.read()
    _require_exact_members(path, set(payloads), expected_files)
    unexpected_directories = directories - _archive_parents(expected_files)
    if unexpected_directories:
        raise RuntimeError(
            f"{path.name} contains unexpected directories "
            f"{sorted(unexpected_directories)[:3]}; rebuild it"
        )
    return payloads


def _read_wheel(path: Path, expected_files: set[str]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    directories: set[str] = set()
    seen: set[str] = set()
    with zipfile.ZipFile(path) as bundle:
        for info in bundle.infolist():
            name = _canonical_member_name(info.filename, directory=info.is_dir())
            if name in seen:
                raise RuntimeError(f"{path.name} contains duplicate member {name!r}")
            seen.add(name)
            if info.is_dir():
                if info.file_size or info.compress_size:
                    raise RuntimeError(
                        f"{path.name} contains a directory member with data: {name!r}"
                    )
                directories.add(name)
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError(f"{path.name} contains symlink member {name!r}; rebuild it")
            payloads[name] = bundle.read(info)
    _require_exact_members(path, set(payloads), expected_files)
    unexpected_directories = directories - _archive_parents(expected_files)
    if unexpected_directories:
        raise RuntimeError(
            f"{path.name} contains unexpected directories "
            f"{sorted(unexpected_directories)[:3]}; rebuild it"
        )
    return payloads


def _single_metadata_value(message, header: str, artifact: str) -> str:
    values = message.get_all(header, [])
    if len(values) != 1:
        raise RuntimeError(
            f"{artifact} must declare exactly one {header} header, found {len(values)}"
        )
    return str(values[0])


def _metadata_body(payload: bytes, artifact: str) -> bytes:
    for marker in (b"\r\n\r\n", b"\n\n"):
        if marker in payload:
            return payload.split(marker, 1)[1]
    raise RuntimeError(f"{artifact} contains malformed package metadata")


def _strip_builder_identity(sdist: Path, wheel: Path) -> None:
    """Rewrite both distributions with neutral container headers, in place.

    `python -m build` stamps the tar members with the maintainer's username,
    group and uid/gid, and both containers with wall-clock mtimes: identity the
    release then publishes, and noise that makes byte-reproducibility
    impossible. Member payloads, names, order and modes are preserved; only
    ownership and timestamps are neutralised, so the freshness checks that ran
    before this remain valid.

    A tar member carries its time twice. Clearing `mtime` alone leaves the same
    value in the member's PAX header, which `tarfile` writes back out, so two
    builds of one tree still differ by those records. Both copies are cleared,
    and the archive format is stated rather than inherited from the writer's
    default.
    """
    volatile = ("mtime", "atime", "ctime", "uid", "gid", "uname", "gname")
    members: list[tuple[tarfile.TarInfo, bytes]] = []
    with tarfile.open(sdist) as bundle:
        for member in bundle.getmembers():
            payload = bundle.extractfile(member).read() if member.isfile() else b""
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mtime = 0
            for key in volatile:
                member.pax_headers.pop(key, None)
            members.append((member, payload))
    # `filename=""` keeps the archive's own name out of the gzip header, which
    # otherwise records whatever path the writer happened to use.
    with (
        sdist.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as flat,
    ):
        with tarfile.open(fileobj=flat, mode="w", format=tarfile.PAX_FORMAT) as bundle:
            bundle.pax_headers = {}
            for member, payload in members:
                bundle.addfile(member, io.BytesIO(payload) if member.isfile() else None)

    entries: list[tuple[zipfile.ZipInfo, bytes]] = []
    with zipfile.ZipFile(wheel) as bundle:
        for info in bundle.infolist():
            entries.append((info, bundle.read(info)))
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for info, payload in entries:
            neutral = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            neutral.external_attr = info.external_attr
            neutral.compress_type = info.compress_type
            bundle.writestr(neutral, payload)


def _require_fresh_metadata(payload: bytes, artifact: str, readme: bytes) -> None:
    message = BytesHeaderParser(policy=email_policy).parsebytes(payload)
    expected_headers = {
        "Name": _DISTRIBUTION,
        "Version": _VERSION,
        "Summary": _DESCRIPTION,
        "Requires-Python": _REQUIRES_PYTHON,
        "License-Expression": _LICENSE_EXPRESSION,
    }
    for header, expected in expected_headers.items():
        actual = _single_metadata_value(message, header, artifact)
        if actual != expected:
            raise RuntimeError(
                f"{artifact} {header} metadata does not match pyproject.toml; rebuild it"
            )

    urls: dict[str, str] = {}
    for raw in message.get_all("Project-URL", []):
        label, separator, value = str(raw).partition(",")
        label, value = label.strip(), value.strip()
        if not separator or not label or not value or label in urls:
            raise RuntimeError(f"{artifact} contains malformed or duplicate Project-URL metadata")
        urls[label] = value
    if urls != _PROJECT_URLS:
        raise RuntimeError(
            f"{artifact} Project-URL metadata does not match pyproject.toml; rebuild it"
        )

    extras = [str(value) for value in message.get_all("Provides-Extra", [])]
    if len(extras) != len(set(extras)) or set(extras) != _PROVIDES_EXTRA:
        raise RuntimeError(
            f"{artifact} Provides-Extra metadata does not match pyproject.toml; rebuild it"
        )
    requirements = [str(value) for value in message.get_all("Requires-Dist", [])]
    if len(requirements) != len(set(requirements)) or set(requirements) != _REQUIRES_DIST:
        raise RuntimeError(
            f"{artifact} Requires-Dist metadata does not match pyproject.toml; rebuild it"
        )
    if _metadata_body(payload, artifact) != readme:
        raise RuntimeError(f"{artifact} embeds a stale README; rebuild it")


def _require_wheel_control_files(
    payloads: dict[str, bytes], dist_info: str, expected_files: set[str], artifact: str
) -> None:
    wheel = BytesHeaderParser(policy=email_policy).parsebytes(payloads[f"{dist_info}/WHEEL"])
    expected = {
        "Wheel-Version": "1.0",
        "Root-Is-Purelib": "true",
        "Tag": "py3-none-any",
    }
    for header, value in expected.items():
        if _single_metadata_value(wheel, header, artifact) != value:
            raise RuntimeError(f"{artifact} has unexpected {header} metadata; rebuild it")

    try:
        rows = list(csv.reader(payloads[f"{dist_info}/RECORD"].decode("utf-8").splitlines()))
    except (UnicodeDecodeError, csv.Error) as error:
        raise RuntimeError(f"{artifact} contains a malformed RECORD") from error
    if any(len(row) != 3 for row in rows):
        raise RuntimeError(f"{artifact} contains a malformed RECORD")
    recorded = [row[0] for row in rows]
    if len(recorded) != len(set(recorded)) or set(recorded) != expected_files:
        raise RuntimeError(f"{artifact} RECORD does not enumerate the wheel exactly")

    record_name = f"{dist_info}/RECORD"
    for name, recorded_hash, recorded_size in rows:
        if name == record_name:
            if recorded_hash or recorded_size:
                raise RuntimeError(f"{artifact} RECORD must leave its own hash and size empty")
            continue
        payload = payloads[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        expected_hash = f"sha256={digest.decode('ascii')}"
        expected_size = str(len(payload))
        if recorded_hash != expected_hash or recorded_size != expected_size:
            raise RuntimeError(
                f"{artifact} RECORD hash or size does not match wheel member {name!r}"
            )


def _require_fresh_distributions(built: list[Path]) -> tuple[Path, Path]:
    """Require one complete, current sdist and wheel, with no opaque payloads."""
    expected_names = {
        f"{_PACKAGE}-{_VERSION}.tar.gz",
        f"{_PACKAGE}-{_VERSION}-py3-none-any.whl",
    }
    actual_names = [path.name for path in built]
    if (
        len(built) != 2
        or set(actual_names) != expected_names
        or any(not path.is_file() or path.is_symlink() for path in built)
    ):
        raise RuntimeError(
            "expected exactly one wheel and one source distribution in dist/ "
            f"({sorted(expected_names)}), found {actual_names}; clear dist/ and rebuild"
        )
    by_name = {path.name: path for path in built}
    sdist = by_name[f"{_PACKAGE}-{_VERSION}.tar.gz"]
    wheel = by_name[f"{_PACKAGE}-{_VERSION}-py3-none-any.whl"]

    source_root = REPO_ROOT / "src" / _PACKAGE
    source_paths = sorted(source_root.rglob("*.py"))
    if not source_paths:
        raise RuntimeError(f"the source tree contains no {_PACKAGE} Python package")
    wheel_sources = {
        f"{_PACKAGE}/{path.relative_to(source_root).as_posix()}": path.read_bytes()
        for path in source_paths
        if path.is_file()
    }
    root = f"{_PACKAGE}-{_VERSION}"
    egg_info = f"src/{_PACKAGE}.egg-info"
    egg_files = {
        f"{egg_info}/PKG-INFO",
        f"{egg_info}/SOURCES.txt",
        f"{egg_info}/dependency_links.txt",
        f"{egg_info}/requires.txt",
        f"{egg_info}/top_level.txt",
    }
    sdist_files = {
        f"{root}/LICENSE",
        f"{root}/NOTICE",
        f"{root}/PKG-INFO",
        f"{root}/{_README_NAME}",
        f"{root}/pyproject.toml",
        f"{root}/setup.cfg",
        *(f"{root}/src/{name}" for name in wheel_sources),
        *(f"{root}/{name}" for name in egg_files),
    }
    dist_info = f"{_PACKAGE}-{_VERSION}.dist-info"
    wheel_files = {
        *wheel_sources,
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/NOTICE",
        f"{dist_info}/METADATA",
        f"{dist_info}/WHEEL",
        f"{dist_info}/top_level.txt",
        f"{dist_info}/RECORD",
    }
    sdist_payloads = _read_sdist(sdist, sdist_files)
    wheel_payloads = _read_wheel(wheel, wheel_files)

    for wheel_name, expected in wheel_sources.items():
        sdist_name = f"{root}/src/{wheel_name}"
        if sdist_payloads[sdist_name] != expected or wheel_payloads[wheel_name] != expected:
            raise RuntimeError(
                f"the distributions were built from a different tree ({wheel_name} differs); "
                "clear dist/ and rebuild"
            )

    readme = (REPO_ROOT / _README_NAME).read_bytes()
    license_text = (REPO_ROOT / "LICENSE").read_bytes()
    notice_text = (REPO_ROOT / "NOTICE").read_bytes()
    expected_static = {
        f"{root}/{_README_NAME}": readme,
        f"{root}/LICENSE": license_text,
        f"{root}/NOTICE": notice_text,
        f"{root}/pyproject.toml": _PYPROJECT_BYTES,
        f"{root}/setup.cfg": b"[egg_info]\ntag_build = \ntag_date = 0\n\n",
        f"{root}/{egg_info}/dependency_links.txt": b"\n",
        f"{root}/{egg_info}/top_level.txt": f"{_PACKAGE}\n".encode(),
        f"{dist_info}/licenses/LICENSE": license_text,
        f"{dist_info}/top_level.txt": f"{_PACKAGE}\n".encode(),
    }
    for name, expected in expected_static.items():
        payload = sdist_payloads.get(name, wheel_payloads.get(name))
        if payload != expected:
            raise RuntimeError(f"a distribution contains stale generated content ({name}); rebuild")

    sources_manifest = {
        "LICENSE",
        "NOTICE",
        _README_NAME,
        "pyproject.toml",
        *(f"src/{name}" for name in wheel_sources),
        *egg_files,
    }
    sources_payload = sdist_payloads[f"{root}/{egg_info}/SOURCES.txt"]
    try:
        source_rows = sources_payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{sdist.name} contains a malformed SOURCES.txt") from error
    if len(source_rows) != len(set(source_rows)) or set(source_rows) != sources_manifest:
        raise RuntimeError(f"{sdist.name} SOURCES.txt does not enumerate the source tree exactly")

    package_info = sdist_payloads[f"{root}/PKG-INFO"]
    if package_info != sdist_payloads[f"{root}/{egg_info}/PKG-INFO"]:
        raise RuntimeError(f"{sdist.name} contains inconsistent PKG-INFO metadata")
    wheel_metadata = wheel_payloads[f"{dist_info}/METADATA"]
    if package_info != wheel_metadata:
        raise RuntimeError("wheel METADATA and sdist PKG-INFO differ; rebuild both together")
    _require_fresh_metadata(package_info, "PKG-INFO/METADATA", readme)
    _require_wheel_control_files(wheel_payloads, dist_info, wheel_files, wheel.name)
    return sdist, wheel


@contextmanager
def _staged_release(output_dir: Path):
    """Keep an incomplete release private, then promote the whole directory."""
    if output_dir.is_symlink():
        raise RuntimeError(f"release output must not be a symlink: {output_dir}")
    output_dir = output_dir.resolve()
    repository = REPO_ROOT.resolve()
    if (
        output_dir == Path(output_dir.anchor)
        or output_dir == repository
        or output_dir in repository.parents
    ):
        raise RuntimeError(f"refusing to use broad release output path: {output_dir}")
    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise RuntimeError(f"release output must be a real directory path: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()):
        # Never infer ownership of an arbitrary caller-supplied directory.
        # Moving a populated one into the staging area would make any later
        # validation error delete unrelated user data along with the candidate.
        raise RuntimeError(
            f"release output must be absent or empty: {output_dir}; "
            "move the previous release aside explicitly before rebuilding"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.staging-", dir=output_dir.parent
    ) as temporary:
        temporary_root = Path(temporary)
        staging = temporary_root / "candidate"
        staging.mkdir()
        yield staging
        os.replace(staging, output_dir)


def _package_release_staged(
    tag: str, output_dir: Path, manifest: dict, *, allow_unreachable: bool = False
) -> dict:
    manifest["release"]["git_commit"] = _released_commit(
        manifest, allow_unreachable=allow_unreachable
    )
    if allow_unreachable:
        # Say so in the artifact. A bundle whose commit cannot be checked out
        # is still a real measurement, but a reader must not mistake the
        # recorded SHA for something they can verify against.
        manifest["release"]["commit_reachable"] = False
    # Two different commits, recorded separately because they answer different
    # questions. `git_commit` is where the NUMBERS were measured (an ancestor
    # of HEAD is legitimate: writing the score into the README lands after the
    # measurement). `source_commit` is the tree these DISTRIBUTIONS were built
    # from, and the tag must name exactly that — otherwise the wheel on the
    # release and the source at the tag need never have matched.
    manifest["release"]["source_commit"] = _source_commit()
    datasets = {}
    for name, dataset in manifest["datasets"].items():
        path = dataset_path(dataset["path"])
        if not path.is_file() or sha256(path) != dataset["sha256"]:
            raise RuntimeError(f"{name}: pinned dataset is missing or has the wrong checksum")
        datasets[name] = path

    for name, specification in manifest["benchmarks"].items():
        result_path = artifact_path(specification["result_path"])
        codebooks = artifact_path(specification["codebook_path"])
        # Verify the seal FIRST. Recomputing a score from an unverified verdict
        # file and then stamping the release "verified" would let a flipped
        # verdict — or a missing judge manifest — ship under that word.
        records = sealed.verified_records(name, result_path)
        specification["judge_seal"] = sealed.seal(result_path.parent)
        sealed.require_same_corpus(
            name, result_path.parent, dataset=datasets[name], codebooks=codebooks
        )
        if len(records) != specification["result_rows"]:
            raise RuntimeError(
                f"{name}: expected {specification['result_rows']} rows, found {len(records)}"
            )
        metrics = SUMMARIZERS[name](records)
        score = metrics["overall"][specification["metric"]]
        # A declared score is a claim, so packaging checks it rather than
        # overwriting it. Assigning unconditionally would leave the manifest
        # unable to disagree — whatever the sealed rows sum to becomes "the
        # release score" — and a re-judged or swapped result directory would
        # then package silently under the old version number. `verify.py`
        # applies the same rule on the consumer side.
        declared = specification.get("score")
        if declared is not None and abs(score - float(declared)) > 1e-12:
            raise RuntimeError(
                f"{name}: the manifest declares {declared}, but the sealed results recompute "
                f"to {score}; re-run, or update the manifest deliberately"
            )
        specification["score"] = score
        _require_loadable_codebooks(name, codebooks, specification["codebooks"])
        result_dir = result_path.parent
        _require_declared_models(name, result_dir, manifest["models"])
        plan_cache = _verified_plan_cache(name, result_dir, allow_unreachable=allow_unreachable)
        archive = output_dir / specification["asset"]
        # The codebooks and the quoted evidence in the results are derived from
        # the benchmark's own corpus and carry its licence, not the repository's
        # Apache-2.0. Shipping them without saying so relicenses someone else's
        # data by omission, so the terms travel inside the archive.
        archive_entries = _release_archive_entries(
            name,
            specification,
            codebooks,
            plan_cache,
            result_dir,
            tag,
        )
        _archive(archive_entries, archive)
        # Checked, not assigned — the same rule the score gets. The manifest is
        # what a downloader hashes an asset against before unpacking it, so a
        # rebuild that produces different bytes has to say so here rather than
        # quietly redefine what `v0.1.0` is.
        digest = sha256(archive)
        declared = specification.get("asset_sha256")
        if declared is not None and declared != digest:
            raise RuntimeError(
                f"{name}: the manifest pins {specification['asset']} at {declared[:12]}, but "
                f"this build produced {digest[:12]}; publish a new version, or update the "
                f"manifest deliberately"
            )
        specification["asset_sha256"] = digest

    dist_dir = REPO_ROOT / "dist"
    built_distributions = sorted(dist_dir.iterdir()) if dist_dir.is_dir() else []
    if not built_distributions:
        raise RuntimeError("build wheel and source distribution before packaging the release")
    # Metadata is untrusted too. Scan valid regular files before parsing them,
    # so even a credential pasted into a header reaches the redacted error path.
    _refuse_secrets(
        [
            (path, path.name)
            for path in built_distributions
            if path.is_file() and not path.is_symlink()
        ]
    )
    sdist, wheel = _require_fresh_distributions(built_distributions)
    # Neutralise ownership and timestamps BEFORE any digest is taken, so the
    # pinned hashes describe the identity-free bytes that actually ship.
    _strip_builder_identity(sdist, wheel)
    distribution_sources = [sdist, wheel]
    manifest["release"]["files"] = {
        **{
            specification["asset"]: specification["asset_sha256"]
            for specification in manifest["benchmarks"].values()
        },
        **{path.name: sha256(path) for path in distribution_sources},
    }

    # Earned only in memory after every benchmark's seal and distribution has
    # verified. The serialized claim is not written until the secret gate passes.
    manifest["release"]["status"] = "verified"

    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    benchmark_archives = [output_dir / item["asset"] for item in manifest["benchmarks"].values()]
    # Scan everything, including compressed members and the final manifest,
    # before a distribution is copied or a verified manifest becomes visible.
    _refuse_secrets([(path, path.name) for path in [*benchmark_archives, *distribution_sources]])
    _refuse_secret_payload(manifest_payload)

    distributions = []
    for source in distribution_sources:
        destination = output_dir / source.name
        shutil.copy2(source, destination)
        if sha256(destination) != manifest["release"]["files"][source.name]:
            raise RuntimeError(f"{source.name} changed while the release was being assembled")
        distributions.append(destination)

    release_manifest = output_dir / "artifact-manifest.json"
    release_manifest.write_bytes(manifest_payload)
    release_files = [
        *benchmark_archives,
        *distributions,
        release_manifest,
    ]
    checksum_path = output_dir / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in release_files),
        encoding="utf-8",
    )
    return manifest


def package_release(
    tag: str, output_dir: Path, manifest_path: Path, *, allow_unreachable: bool = False
) -> dict:
    with _staged_release(output_dir) as staging:
        manifest = copy.deepcopy(load_manifest(manifest_path))
        if tag != manifest["release"]["version"]:
            raise RuntimeError(
                f"tag {tag!r} does not match manifest release {manifest['release']['version']!r}"
            )
        return _package_release_staged(tag, staging, manifest, allow_unreachable=allow_unreachable)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--output", type=Path, default=ARTIFACTS_ROOT / "release")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument(
        "--allow-unreachable-commit",
        action="store_true",
        help=(
            "package results whose commit is not reachable from HEAD, or that were "
            "measured across more than one commit. Intended for a bundle whose "
            "repository history is not published, where the recorded SHA is provenance "
            "rather than something a reader can check out; the bundle records the waiver."
        ),
    )
    args = parser.parse_args(argv)
    manifest = package_release(
        args.tag,
        args.output.resolve(),
        args.manifest,
        allow_unreachable=args.allow_unreachable_commit,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
