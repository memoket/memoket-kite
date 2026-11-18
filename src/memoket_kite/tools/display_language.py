"""Store translations of graph labels and citations separately from the memory index."""

from collections import defaultdict
from hashlib import sha256
from itertools import chain

from .contracts import ToolError
from .retrieve import memory_snapshot
from .workspace import Workspace

LANGUAGES = ("en", "zh")
KINDS = ("topic", "entity", "facet", "source", "passage", "line")
MAX_PAGE = 200
MAX_TEXT = 8192


def graph_items(snapshot):
    """Bind each translation to the text shown at the stable graph identifier."""
    for node in snapshot["topics"]:
        yield f"topic:{node['code']}", node["code"].replace("_", " ")
    for node in snapshot.get("entities", ()):
        yield f"entity:{node['code']}", node["label"]
    facets = set()
    for row in chain(snapshot["facts"], snapshot.get("passages", ())):
        for axis, value in row.get("facets", {}).items():
            for name in value if isinstance(value, list) else [value]:
                facets.add((axis, name))
    for axis, name in sorted(facets):
        yield f"facet:{axis}:{name}", name.replace("_", " ")
    for revision in snapshot["revisions"]:
        if revision.get("title"):
            yield f"source:{revision['checksum']}", revision["title"]
    for passage in snapshot.get("passages", ()):
        if passage.get("evidence") and passage.get("label"):
            yield f"passage:{passage['evidence'][0]}:{passage['label']}", passage["label"]
    for key, quote in snapshot["evidence"].items():
        yield f"line:{key}", quote["text"]


def _fingerprint(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _binding(workspace) -> dict:
    return {
        "workspace_id": workspace.db.execute(
            "SELECT workspace_id FROM memory WHERE singleton=1"
        ).fetchone()[0],
        "version": workspace.version(),
    }


def store_graph_translations(workspace, language, originals, translations, *, overwrite=True):
    update = "" if overwrite else " WHERE display_translations.fingerprint != excluded.fingerprint"
    workspace.db.executemany(
        "INSERT INTO display_translations VALUES(?, ?, ?, ?) "
        "ON CONFLICT(language, graph_key) DO UPDATE SET "
        "fingerprint=excluded.fingerprint, text=excluded.text" + update,
        (
            (language, key, _fingerprint(originals[key]), text.strip())
            for key, text in translations.items()
        ),
    )


def prepare(directory, language, *, kind=None, offset=0, limit=50):
    if offset < 0 or not 1 <= limit <= MAX_PAGE:
        raise ToolError("invalid_input", "use a nonnegative offset and a limit from 1 to 200")
    with Workspace.open(directory, read_only=True) as workspace:
        binding = _binding(workspace)
        items = sorted(
            {
                key: text
                for key, text in graph_items(memory_snapshot(workspace))
                if text and (kind is None or key.startswith(f"{kind}:"))
            }.items()
        )
    return {
        **binding,
        "language": language,
        "total": len(items),
        "next_offset": offset + limit if offset + limit < len(items) else None,
        "items": dict(items[offset : offset + limit]),
    }


def apply(directory, language, value):
    translations = value.get("translations")
    if (
        set(value) != {"workspace_id", "version", "language", "translations"}
        or value["language"] != language
        or type(value["version"]) is not int
        or not isinstance(translations, dict)
        or not 1 <= len(translations) <= MAX_PAGE
        or not all(
            isinstance(text, str) and text.strip() and len(text) <= MAX_TEXT
            for text in translations.values()
        )
    ):
        raise ToolError(
            "invalid_input", "supply workspace ID, version, language and 1 to 200 translations"
        )
    expected = {key: value[key] for key in ("workspace_id", "version")}
    with Workspace.open(directory) as workspace:
        if expected != _binding(workspace):
            raise ToolError(
                "stale_snapshot", "prepare display translations for the current workspace"
            )
        sources = dict(graph_items(memory_snapshot(workspace)))
        if any(not sources.get(key) for key in translations):
            raise ToolError(
                "invalid_input", "translations must use IDs from the current memory graph"
            )
        store_graph_translations(workspace, language, sources, translations)
    return {
        "language": language,
        "translated": len(translations),
        "memory_version": expected["version"],
    }


def graph_translations(workspace, snapshot) -> dict:
    if not workspace.db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='display_translations'"
    ).fetchone():
        return {}
    rows = workspace.db.execute(
        "SELECT language, graph_key, fingerprint, text FROM display_translations"
    ).fetchall()
    if not rows:
        return {}
    result = defaultdict(dict)
    originals = dict(graph_items(snapshot))
    for language, key, fingerprint, text in rows:
        if (original := originals.get(key)) and _fingerprint(original) == fingerprint:
            result[language][key] = text
    return dict(result)
