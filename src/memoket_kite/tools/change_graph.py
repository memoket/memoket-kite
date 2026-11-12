"""Record scoped graph structure for a committed change and resolve its original citations."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from memoket_kite.core.vocab import Vocab

from .contracts import ToolError, decode_object, digest
from .ingest import RECORDING_CLOCK_FIELDS, source_day

PAGE_SIZE = 20
LINE_PREVIEW_LIMIT = 4


@dataclass
class GraphActivity:
    kind: str
    changed: int
    seen: int
    sources: list[tuple[str, int | None]]
    previous: dict[str, dict | None] = field(default_factory=dict)
    topics: tuple[str, ...] = ()


def vocabulary_at_commit(xml: bytes | None) -> Vocab:
    if xml:
        with io.BytesIO(xml) as document:
            for _, node in ET.iterparse(document, events=("end",)):
                if node.tag == "vocab":
                    return Vocab.from_xml(node, strict=True)
    return Vocab()


def graph_structure(records: list[dict], vocab: Vocab, focus: tuple[str, ...] = ()) -> dict:
    """Keep original offsets and graph edges, without copying transcript text."""
    topics = {code for name in focus if (code := vocab.resolve_topic(name))}
    if focus:
        children = {
            topic.code for topic in vocab.topics.values() if topics.intersection(topic.parents)
        }
        topics.update(children)
    entities = set()
    revisions = []
    passages = []
    for record in records:
        checksum = record["checksum"]
        conversation = record["conversation"]
        positions = {
            segment["segment_id"]: offset
            for offset, segment in enumerate(conversation["segments"], 1)
        }
        revisions.append({"checksum": checksum, "active": record["active"]})
        topics.update(
            code for topic in record["taxonomy"] if (code := vocab.resolve_topic(topic["name"]))
        )
        for number, fact in enumerate(record["facts"], 1):
            linked = _annotation_links(fact, vocab, positions)
            passages.append({"id": f"{checksum}:fact:{number}", "checksum": checksum, **linked})
        for span in record.get("index_spans", ()):
            linked = _annotation_links(span, vocab, positions)
            # The normalized annotation is stable when its topic resolves to a
            # new code after a merge. Its original citation stays attached.
            passages.append(
                {"id": f"{checksum}:index:{digest(span)}", "checksum": checksum, **linked}
            )
    for passage in passages:
        topics.update(passage["topics"])
        entities.update(passage["entities"])
    pending = list(topics)
    while pending:
        parents = vocab.topics[pending.pop()].parents - topics
        topics.update(parents)
        pending.extend(parents)
    return {
        "topics": [
            {
                "code": code,
                "parents": sorted(vocab.topics[code].parents),
                "aliases": sorted(vocab.topics[code].aliases),
            }
            for code in sorted(topics)
        ],
        "entities": [
            {
                "code": code,
                "label": vocab.entities[code].name or code,
                "aliases": sorted(vocab.entities[code].aliases),
            }
            for code in sorted(entities)
        ],
        "revisions": revisions,
        "passages": passages,
    }


def _annotation_links(annotation, vocab, positions):
    return {
        "topics": sorted(
            {code for name in annotation["topics"] if (code := vocab.resolve_topic(name))}
        ),
        "entities": sorted(
            {
                code
                for name in annotation.get("entities", ())
                if (code := vocab.resolve_entity(name))
            }
        ),
        "facets": annotation.get("facets", {}),
        "offsets": sorted(positions[sid] for sid in annotation["segment_ids"]),
    }


def change_comparison(workspace, version: int, checksum: str, offset: int) -> dict:
    db = workspace.db
    if (
        db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_activity_graphs'").fetchone()
        is None
    ):
        raise ToolError("not_found", "no retained graph was recorded for this change")
    row = db.execute(
        "SELECT * FROM memory_activity_graphs WHERE version=? AND checksum=?",
        (version, checksum),
    ).fetchone()
    if row is None:
        if db.execute(
            "SELECT 1 FROM memory_activity a, deletion_audit d WHERE a.version=? AND d.version>a.version LIMIT 1",
            (version,),
        ).fetchone():
            raise ToolError(
                "invalidated_change", "a permanent deletion removed prior change graphs"
            )
        raise ToolError("not_found", "no retained graph was recorded for this change")
    before, after = decode_object(row["before"]), decode_object(row["after"])
    frames = (before, after)
    indexed = [{item["id"]: item for item in frame["passages"]} for frame in frames]
    active = [{rev["checksum"]: rev["active"] for rev in frame["revisions"]} for frame in frames]
    union = {**indexed[0], **indexed[1]}

    # Show changed memberships first. Page the same identities on both sides.
    def order(key):
        return (
            indexed[0].get(key) == indexed[1].get(key)
            and active[0].get(union[key]["checksum"]) == active[1].get(union[key]["checksum"]),
            union[key]["offsets"],
            key,
        )

    if offset and offset >= len(union):
        raise ToolError("invalid_input", "offset is past the end of the changed memories")
    keys = set(sorted(union, key=order)[offset : offset + PAGE_SIZE])
    originals = {
        key: workspace.record(key)["conversation"] for key in active[0].keys() | active[1].keys()
    }
    raw_previews = originals.keys() - {
        item["checksum"] for frame in frames for item in frame["passages"]
    }
    focus = originals.get(checksum)
    committed_at = dict(
        db.execute(
            "SELECT version, committed_at FROM memory_versions WHERE version IN (?, ?)",
            (version - 1, version),
        ).fetchall()
    )
    return {
        "change_event": version,
        "change_checksum": checksum,
        "page_offset": offset,
        "page_size": PAGE_SIZE,
        "passage_total": len(union),
        "comparison": [
            {
                "name": "",
                "title": (focus.get("title") or focus["conversation_id"]) if focus else "",
                "description": "",
                "before": _hydrate(
                    before,
                    originals,
                    version - 1,
                    committed_at.get(version - 1),
                    keys,
                    raw_previews,
                ),
                "after": _hydrate(
                    after, originals, version, committed_at.get(version), keys, raw_previews
                ),
            }
        ],
    }


def _hydrate(frame, originals, version, committed_at, keys, raw_previews):
    revisions = []
    evidence = {}
    passages = []
    active = {revision["checksum"]: revision["active"] for revision in frame["revisions"]}

    def cite(checksum, offset):
        conversation = originals[checksum]
        line_id = f"c-{checksum}L{offset}"
        if line_id not in evidence:
            if not 1 <= offset <= len(conversation["segments"]):
                raise ToolError("integrity_error", "change graph has an invalid original offset")
            evidence[line_id] = {
                **conversation["segments"][offset - 1],
                "conversation_id": conversation["conversation_id"],
                "source_reference": conversation["metadata"]["source_reference"],
                "revision_checksum": checksum,
                "offset": offset - 1,
            }
        return line_id

    for item in frame["passages"]:
        if item["id"] not in keys:
            continue
        checksum = item["checksum"]
        ids = [cite(checksum, offset) for offset in item["offsets"][:LINE_PREVIEW_LIMIT]]
        first = evidence[ids[0]]
        passages.append(
            {
                **{key: item[key] for key in ("id", "topics", "entities", "facets")},
                "label": f"{first['conversation_id']}#{first['segment_id']}",
                "active": active[checksum],
                "evidence": ids,
                "citation_total": len(item["offsets"]),
            }
        )
    for revision in frame["revisions"]:
        checksum = revision["checksum"]
        conversation = originals[checksum]
        revisions.append(
            {
                **revision,
                "reference": conversation["metadata"]["source_reference"],
                "title": conversation.get("title"),
                "day": source_day(conversation),
                **{
                    field: conversation[field]
                    for field in RECORDING_CLOCK_FIELDS
                    if field in conversation
                },
            }
        )
        if checksum in raw_previews:
            cite(checksum, 1)
    return {
        **frame,
        "version": version,
        "committed_at": committed_at,
        "revisions": revisions,
        "passages": passages,
        "evidence": evidence,
    }
