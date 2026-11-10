"""Compile cited agent annotations into the existing KITE Codebook format."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from memoket_kite.core.algebra import Store
from memoket_kite.core.vocab import Vocab, norm_code
from memoket_kite.defaults import DEFAULT_MEMORY_PROFILE
from memoket_kite.pipeline.extract import append_session_to_xml

from .contracts import DATE, TEXT, XML_UNSAFE, ToolError, array, digest, object_schema
from .ingest import source_day

TOPIC = object_schema({"name": TEXT}, {"parents": array(TEXT, maximum=8), "aliases": array(TEXT)})
FACT = object_schema(
    {"content": TEXT, "segment_ids": array(TEXT, 1), "topics": array(TEXT, 1, 16)},
    {
        "entities": array(TEXT),
        "kind": TEXT,
        "subject": TEXT,
        "event_date": DATE,
        "confidence": {"enum": ["low", "med", "high"]},
        "status": {"enum": ["statement", "proposal", "confirmed", "rejected", "open", "inference"]},
        "state_key": TEXT,
        "state": TEXT,
        "supersedes": TEXT,
        "objects": array(TEXT),
        "places": array(TEXT),
        "events": array(TEXT),
    },
)
ENTITY = object_schema({"name": TEXT, "aliases": array(TEXT, 1)})
ANNOTATIONS = object_schema(
    {"facts": array(FACT)}, {"taxonomy": array(TOPIC), "entity_aliases": array(ENTITY)}
)
RECLASSIFY = object_schema({"from_topic": TEXT, "to_topic": TEXT})


def _symbol(value: str) -> str:
    code = norm_code(value)
    if not code:
        raise ToolError("invalid_input", "topic, entity or facet has no indexable characters")
    return code


def _symbols(values) -> list[str]:
    return sorted({_symbol(value) for value in values})


def annotate(context, normalized: dict, annotations: dict) -> dict:
    conversation = normalized["conversation"]
    segment_ids = {segment["segment_id"] for segment in conversation["segments"]}
    facts = []
    seen = set()
    for fact in annotations["facts"]:
        if not set(fact["segment_ids"]) <= segment_ids:
            raise ToolError("invalid_evidence", "fact cites a segment outside its conversation")
        if XML_UNSAFE.search(fact["content"]) or XML_UNSAFE.search(fact.get("subject", "")):
            raise ToolError("invalid_input", "fact contains characters XML cannot represent")
        if ("state" in fact) != ("state_key" in fact):
            raise ToolError("invalid_input", "state and state_key must be supplied together")
        if "supersedes" in fact and fact.get("status") != "confirmed":
            raise ToolError("invalid_input", "only confirmed facts can supersede another fact")
        cleaned = {**fact, "segment_ids": sorted(set(fact["segment_ids"]))}
        for key in ("topics", "entities", "objects", "places", "events"):
            cleaned[key] = _symbols(fact.get(key, ()))
        cleaned["kind"] = _symbol(fact.get("kind", "statement"))
        fingerprint = digest(cleaned)
        if fingerprint not in seen:
            facts.append(cleaned)
            seen.add(fingerprint)
    taxonomy = [
        {
            "name": _symbol(topic["name"]),
            "parents": _symbols(topic.get("parents", ())),
            "aliases": _symbols(topic.get("aliases", ())),
        }
        for topic in annotations.get("taxonomy", ())
    ]
    if any(topic["name"] in topic["parents"] for topic in taxonomy):
        raise ToolError("integrity_error", "a topic cannot be its own parent")
    entities = [
        {"name": _symbol(entity["name"]), "aliases": _symbols(entity["aliases"])}
        for entity in annotations.get("entity_aliases", ())
    ]
    return {**normalized, "facts": facts, "taxonomy": taxonomy, "entity_aliases": entities}


def revision_id(normalized: dict) -> str:
    return "c-" + normalized["checksum"]


def source_key(conversation: dict) -> str:
    return digest([conversation["source"], conversation["metadata"]["source_reference"]])


@dataclass
class Index:
    xml: bytes
    store: Store
    vocab: Vocab
    facts: dict
    segments: dict
    revisions: dict


def _vocabulary(records: list[dict], merges: dict[str, str]) -> Vocab:
    vocab = Vocab()
    definitions: dict[str, set] = {}
    parent_edges: list[tuple[str, str]] = []
    entities: dict[str, set] = {}
    for record in records:
        for topic in record["taxonomy"]:
            name = merges.get(topic["name"], topic["name"])
            vocab.define_root(name)
            definitions.setdefault(name, set()).update(topic["aliases"])
            for parent in topic["parents"]:
                if merges.get(parent, parent) != name:
                    parent_edges.append((name, parent))
        for entity in record.get("entity_aliases", ()):
            vocab.register_entity(entity["name"])
            entities.setdefault(entity["name"], set()).update(entity["aliases"])
    for old, new in sorted(merges.items()):
        vocab.define_root(new)
        definitions.setdefault(new, set()).add(old)
    for name, aliases in sorted(definitions.items()):
        for alias in sorted(aliases):
            resolved = vocab.resolve_topic(alias)
            if resolved and resolved != name:
                raise ToolError("conflict", "topic alias is assigned to multiple topics")
            vocab.alias_topic(alias, name)
    parents: dict[str, set] = {}
    for name, parent in parent_edges:
        canonical = vocab.resolve_topic(parent) or vocab.define_root(parent)
        parents.setdefault(name, set()).add(canonical)
    for name, direct in sorted(parents.items()):
        vocab.move_topic(name, sorted(direct))
    for name, aliases in sorted(entities.items()):
        for alias in sorted(aliases):
            resolved = vocab.resolve_entity(alias)
            if resolved and resolved != name:
                raise ToolError("conflict", "entity alias is assigned to multiple entities")
            vocab.alias_entity(alias, name)
    for record in records:
        for fact in record["facts"]:
            for topic in fact["topics"]:
                if not vocab.resolve_topic(topic):
                    vocab.define_root(topic)
            for entity in fact["entities"]:
                vocab.register_entity(entity)
    return vocab


def build_index(records: list[dict], merges: dict[str, str]) -> Index:
    try:
        vocab = _vocabulary(records, merges)
        root = ET.Element("memory")
        root.append(vocab.to_xml())
        timeline = ET.SubElement(root, "timeline")
        facts, segments, revisions = {}, {}, {}
        speakers = set()
        for record in records:
            conversation = record["conversation"]
            uid = revision_id(record)
            revisions[uid] = record
            line_ids = {}
            utterances = []
            for position, segment in enumerate(conversation["segments"], 1):
                line_id = f"{uid}L{position}"
                line_ids[segment["segment_id"]] = line_id
                segments[line_id] = segment
                speaker = segment.get("speaker_id", "")
                if speaker:
                    speakers.add(speaker.lower())
                utterances.append((line_id, speaker, segment.get("start_ms", ""), segment["text"]))
            encoded = []
            for position, fact in enumerate(record["facts"], 1):
                facts[f"{uid}F{position}"] = fact
                encoded.append(
                    {
                        "content": fact["content"],
                        "kind": fact["kind"],
                        "who": fact.get("subject", ""),
                        "t": fact.get("event_date", ""),
                        "conf": fact.get("confidence", "med"),
                        "topics": sorted({vocab.resolve_topic(topic) for topic in fact["topics"]}),
                        "entities": sorted(
                            {vocab.resolve_entity(entity) for entity in fact["entities"]}
                        ),
                        "obj": fact["objects"],
                        "place": fact["places"],
                        "event": fact["events"],
                        "src": [line_ids[sid] for sid in fact["segment_ids"]],
                    }
                )
            date = source_day(conversation)
            session = append_session_to_xml(
                timeline,
                DEFAULT_MEMORY_PROFILE,
                {
                    "id": uid,
                    "date": date or "",
                    "title": conversation.get("title", ""),
                    "utterances": utterances,
                },
                encoded,
            )
            if date is None:
                session.set("date_unknown", "true")
        root.set("speakers", " ".join(sorted(speakers)))
        # An explicit CR entity survives the XML parser’s newline normalization.
        xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).replace(b"\r", b"&#13;")
        store, loaded_vocab = Store.load([io.BytesIO(xml)], strict=True)
        if set(store.facts) != facts.keys() or set(store.lines) != segments.keys():
            raise ToolError("integrity_error", "Codebook IDs differ from source records")
        if any(
            store.lines[line_id].text != segment["text"] for line_id, segment in segments.items()
        ):
            raise ToolError("integrity_error", "Codebook changed source text")
        return Index(xml, store, loaded_vocab, facts, segments, revisions)
    except (ValueError, ET.ParseError, RecursionError) as exc:
        raise ToolError("integrity_error", "the proposed Codebook failed validation") from exc


def reclassify(context, merge: dict) -> dict:
    index = context.workspace.index()
    old = index.vocab.resolve_topic(merge["from_topic"])
    new = index.vocab.resolve_topic(merge["to_topic"])
    if not old or not new:
        raise ToolError("invalid_input", "both topics must already exist")
    if old == new:
        return {"changed": False, "memory_version": context.workspace.version()}
    merges = context.workspace.merges()
    merges = {key: new if value == old else value for key, value in merges.items()}
    merges[old] = new
    return context.workspace.replace_index(merges)
