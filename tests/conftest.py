"""A unit test that reaches the provider fails immediately.

Without this, a test that forgets to stub one of the several entry points
still passes — it just runs for minutes, silently spends money, and depends on
a network the suite is not supposed to need. One stub is rarely enough: the
answer stage and the plan compiler each hold their own reference to the
provider, so a test that patches one still reaches the other.

Every test is sealed by default. A test that genuinely needs the provider must
say so with `@pytest.mark.provider`, which makes the exception visible in the
file rather than in a timing anomaly.
"""

from __future__ import annotations

import importlib
import json
import socket
from pathlib import Path

import pytest

_SEALED = (
    "memoket_kite.providers.llm",
    "memoket_kite.pipeline.compile_plan",
    "memoket_kite.pipeline.answer",
    "memoket_kite.pipeline.retrieve",
    "benchmarks.longmemeval.score",
    "benchmarks.locomo.score",
)
_NAMES = ("llm", "llm_json", "_http_llm")


def pytest_configure(config):
    config.addinivalue_line("markers", "provider: this test may call the real provider")
    _warm_the_tokenizer()


def _warm_the_tokenizer() -> None:
    """Resolve the token encoder before any test's socket tripwire is armed.

    `tiktoken` fetches its encoding table on first use and caches it on disk.
    On a cold cache — every CI runner and every fresh clone — that first use
    lands inside whichever test prices a row first, where a fetch of a static
    data file is indistinguishable from a test reaching the network and trips
    the guard.

    Resolving it here keeps the tripwire strict without making it wrong. A
    machine that is genuinely offline still gets the character estimate, and
    the guards that require an exact count still refuse.
    """
    from memoket_kite.pipeline import ledger

    ledger.text_tokens("")


class _Reached(BaseException):
    """Not an ``Exception``: the provider wraps its calls in a retry loop that
    swallows every ``Exception`` and sleeps between attempts, so a tripwire
    derived from ``Exception`` is caught by the code it is meant to catch."""


#: A miniature corpus, entirely synthetic, carried in the repository.
#:
#: The real benchmark data is large and separately licensed, so a suite that
#: reads it can only run on a machine where it has been built — which means
#: the guarantees these tests exist to prove would go unchecked in every clean
#: clone. This corpus has the same shape and three questions.
FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture
def corpus(monkeypatch, tmp_path):
    """Point the harness at the synthetic corpus, with a writable results dir."""
    results = tmp_path / "results"
    results.mkdir()
    for module in (
        "benchmarks.common.paths",
        "benchmarks.longmemeval.evaluate",
        "benchmarks.longmemeval.score",
        "benchmarks.locomo.evaluate",
        "benchmarks.locomo.score",
        "benchmarks.reproduce.sealed",
    ):
        imported = importlib.import_module(module)
        for name, value in (
            ("DATASETS_ROOT", FIXTURE_CORPUS / "datasets"),
            ("CODEBOOKS_ROOT", FIXTURE_CORPUS / "codebooks"),
            ("RESULTS_ROOT", results),
            ("ARTIFACTS_ROOT", FIXTURE_CORPUS),
        ):
            if hasattr(imported, name):
                monkeypatch.setattr(imported, name, value)
    for module, name, value in (
        (
            "benchmarks.longmemeval.evaluate",
            "DATASET",
            FIXTURE_CORPUS / "datasets" / "longmemeval" / "longmemeval_s_cleaned.json",
        ),
        (
            "benchmarks.longmemeval.score",
            "DATASET",
            FIXTURE_CORPUS / "datasets" / "longmemeval" / "longmemeval_s_cleaned.json",
        ),
        (
            "benchmarks.locomo.evaluate",
            "DATASET",
            FIXTURE_CORPUS / "datasets" / "locomo" / "locomo10.json",
        ),
        (
            "benchmarks.locomo.score",
            "DATASET",
            FIXTURE_CORPUS / "datasets" / "locomo" / "locomo10.json",
        ),
    ):
        imported = importlib.import_module(module)
        if hasattr(imported, name):
            monkeypatch.setattr(imported, name, value)
    sealed = importlib.import_module("benchmarks.reproduce.sealed")
    monkeypatch.setattr(
        sealed, "LOCOMO_DATASET", FIXTURE_CORPUS / "datasets" / "locomo" / "locomo10.json"
    )
    monkeypatch.setattr(
        sealed,
        "LONGMEMEVAL_DATASET",
        FIXTURE_CORPUS / "datasets" / "longmemeval" / "longmemeval_s_cleaned.json",
    )
    # `_load_dataset` caches, and both LoCoMo harnesses go through it.
    locomo = importlib.import_module("benchmarks.locomo.evaluate")
    if hasattr(locomo._load_dataset, "cache_clear"):
        locomo._load_dataset.cache_clear()
        monkeypatch.setattr(
            locomo,
            "_load_dataset",
            lambda: json.loads(
                (FIXTURE_CORPUS / "datasets" / "locomo" / "locomo10.json").read_text()
            ),
        )
        for user in ("benchmarks.locomo.score",):
            imported = importlib.import_module(user)
            if hasattr(imported, "_load_dataset"):
                monkeypatch.setattr(imported, "_load_dataset", locomo._load_dataset)
    return results


@pytest.fixture(autouse=True)
def _no_provider(request, monkeypatch):
    """Replace every provider entry point, and the socket, with a tripwire."""
    if request.node.get_closest_marker("provider"):
        return

    def refuse(*args, **kwargs):
        raise _Reached(
            "this test reached the LLM provider. Stub every entry point it can "
            "take — the answer stage and the plan compiler each have their own "
            "— or supply a warm plan cache, or mark the test @pytest.mark.provider."
        )

    import importlib

    for module_name in _SEALED:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for name in _NAMES:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, refuse, raising=False)

    # Belt and braces: a module imported later, or a provider client built from
    # a different path, still cannot open a socket.
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address, *args, **kwargs)
        raise _Reached(f"this test opened a socket to {host}")

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(autouse=True)
def _untracked_files_are_the_runs_problem(monkeypatch):
    """A stray scratch file in the developer's checkout is not a test failure.

    `manifest.write` refuses to start a run in a tree holding untracked files,
    because the patch it stores next to a score covers tracked files only. That
    is a statement about the tree a RUN is measured on, not about the tree a
    unit test happens to execute in, so tests get an empty answer by default.
    `tests/unit/test_manifest_provenance.py` builds a repository of its own and
    restores the real reader to exercise it.
    """
    from benchmarks.common import manifest

    monkeypatch.setattr(manifest, "untracked_files", lambda: ())
