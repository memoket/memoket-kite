"""Audit shipped text for terms concentrated in a small part of a corpus.

A term appearing in shipped text and in only a handful of a corpus's documents
is too specific to be task language and too rare to be coincidence. Findings
require review rather than being an automatic verdict.

The scan covers every string a binding exports and the library modules whose
text reaches a reader. A missing corpus is reported as SKIPPED; passing
``--require-corpus`` makes it an error.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import re
from pathlib import Path

from benchmarks.common.paths import DATASETS_ROOT

GENERIC_TERMS = set(
    """about after all answer any are code codes content conversation date dates
    duration entities entity event events evidence extract fact facts field
    fields filter filters first format from group id ids index json kind kinds
    list memory model name names plan plans prompt question questions record
    result return root rules schema search session sessions source sources
    speaker speakers specific stage subject taxonomy text time topic topics
    unit units value values where who with year years""".split()
)

CORPORA = {
    "locomo": DATASETS_ROOT / "locomo" / "locomo10.json",
    "longmemeval": DATASETS_ROOT / "longmemeval" / "longmemeval_s_cleaned.json",
}

#: Library modules whose text a reader sees, or whose literals decide what an
#: answer says.
LIBRARY_MODULES = (
    "memoket_kite.prompts.answer",
    "memoket_kite.prompts.extract",
    "memoket_kite.prompts.recall",
    "memoket_kite.pipeline.answer",
    "memoket_kite.pipeline.patterns",
    "memoket_kite.pipeline.postproc",
    "memoket_kite.pipeline.render",
)

#: A document fraction at or below this, with at least this many occurrences,
#: is what "concentrated" means.
MAX_DOCUMENT_FRACTION = 0.30
MIN_OCCURRENCES = 5


def _prompt_terms(text: str) -> set[str]:
    terms = set()
    for word in re.findall(r"[a-z][a-z_]{3,}", text.lower()):
        terms.update(
            part for part in word.split("_") if len(part) > 3 and part not in GENERIC_TERMS
        )
    return terms


def _documents(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.dumps(item).lower() for item in json.load(stream)]


def _binding_text(benchmark: str) -> dict[str, str]:
    """Every string a binding exports, by the name it exports it under."""
    profile = importlib.import_module(f"benchmarks.{benchmark}.profile")
    scanned: dict[str, str] = {}
    for attribute in dir(profile):
        if attribute.startswith("__"):
            continue
        value = getattr(profile, attribute)
        if isinstance(value, str):
            scanned[attribute] = value
        elif isinstance(value, re.Pattern):
            scanned[attribute] = value.pattern
        elif isinstance(value, (list, tuple, set, frozenset)) and all(
            isinstance(item, str) for item in value
        ):
            scanned[attribute] = " ".join(sorted(value))
        elif callable(value) and getattr(value, "__module__", "") == profile.__name__:
            try:
                scanned[attribute] = inspect.getsource(value)
            except (OSError, TypeError):
                continue
    return scanned


def _module_literals(module) -> str:
    """String literals in a module's own source, minus its documentation.

    A prompt is as often assembled inside the function that sends it as bound
    to a module-level name, so the source is read rather than the namespace.
    Docstrings are held out: they address a reader of the code, not a model.
    """
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError, SyntaxError):
        return ""
    documentation = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            text = ast.get_docstring(node, clean=False)
            if text:
                documentation.add(text)
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in documentation
    ]
    return "\n".join(literals)


def _library_text() -> dict[str, str]:
    """Every string the library can send to a reader, by the module holding it."""
    scanned: dict[str, str] = {}
    for name in LIBRARY_MODULES:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        text = _module_literals(module)
        if text:
            scanned[name] = text
    return scanned


def _scan(source: str, name: str, text: str, corpora: dict[str, list[str]]) -> list[tuple]:
    findings = []
    for term in sorted(_prompt_terms(text)):
        pattern = re.compile(r"\b" + re.escape(term))
        for corpus, documents in corpora.items():
            matching = [document for document in documents if pattern.search(document)]
            if not matching:
                continue
            count = sum(document.count(term) for document in matching)
            fraction = len(matching) / len(documents)
            if fraction <= MAX_DOCUMENT_FRACTION and count >= MIN_OCCURRENCES:
                findings.append((source, name, term, corpus, len(matching), len(documents), count))
    return findings


def audit() -> tuple[list[tuple], list[str]]:
    """Findings, and the corpora that could not be read."""
    corpora: dict[str, list[str]] = {}
    skipped: list[str] = []
    for name, path in CORPORA.items():
        documents = _documents(path)
        if documents:
            corpora[name] = documents
        else:
            skipped.append(name)
    if not corpora:
        return [], skipped

    findings: list[tuple] = []
    for benchmark in CORPORA:
        for name, text in _binding_text(benchmark).items():
            findings.extend(_scan(benchmark, name, text, corpora))
    for name, text in _library_text().items():
        findings.extend(_scan("library", name, text, corpora))
    return findings, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-corpus",
        action="store_true",
        help="fail when a corpus is missing, instead of reporting it as skipped",
    )
    args = parser.parse_args(argv)

    findings, skipped = audit()
    for name in skipped:
        print(f"SKIPPED {name}: corpus not present at {CORPORA[name]}")
    if skipped and args.require_corpus:
        print("Corpus required for this audit; refusing to report a result.")
        return 2
    if findings:
        print("Terms requiring manual review:")
        for item in sorted(findings, key=lambda value: (value[4] / value[5], -value[6])):
            print(
                f"{item[0]}.{item[1]} term={item[2]!r} corpus={item[3]} "
                f"documents={item[4]}/{item[5]} occurrences={item[6]}"
            )
        return 1
    if skipped and not [name for name in CORPORA if name not in skipped]:
        print("No corpus was read; nothing was audited.")
        return 0
    print("No concentrated terms found in the corpora that were read.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
