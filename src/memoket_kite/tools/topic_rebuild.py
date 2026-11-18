"""Rebuild a selected set of topic postings through KITE vocabulary governance."""

import re
from collections import Counter, defaultdict
from itertools import combinations

from memoket_kite.core.vocab import Vocab, norm_code

from .contracts import COUNT, HASH, TEXT, ToolError, array, digest, object_schema
from .index import annotate
from .ingest import source_day
from .source_batch import BATCH_RECEIPT, BATCH_VERIFIED, verify_source_batch

CATALOG_ENTRY = object_schema({"name": TEXT, "parent": {"type": ["string", "null"]}})
BINDING = object_schema({"topic": TEXT, "segment_ids": array(TEXT, minimum=1)})
RECORDING = object_schema({"checksum": HASH, "bindings": array(BINDING)})
EQUIVALENCE = object_schema({"from_topic": TEXT, "to_topic": TEXT})
PLAN = object_schema(
    {
        "version": COUNT,
        "catalog": array(CATALOG_ENTRY, minimum=1),
        "recordings": array(RECORDING, minimum=1),
        "equivalences": array(EQUIVALENCE),
    }
)
PREVIEW = object_schema(
    {
        "plan_sha256": HASH,
        "memory_version": COUNT,
        "records": array({"type": "object"}),
        "governance": {"type": "object"},
    }
)
RECEIPT = {
    **BATCH_RECEIPT,
    "properties": {**BATCH_RECEIPT["properties"], "governance": {"type": "object"}},
    "required": [*BATCH_RECEIPT["required"], "governance"],
}
VERIFIED = {
    **BATCH_VERIFIED,
    "properties": {**BATCH_VERIFIED["properties"], "governance": {"type": "object"}},
    "required": [*BATCH_VERIFIED["required"], "governance"],
}


def _catalog(entries):
    parents = {}
    for entry in entries:
        code = norm_code(entry["name"])
        parent = None if entry["parent"] is None else norm_code(entry["parent"])
        if not code or code in parents or parent == code or parent == "":
            raise ToolError("invalid_input", "topic names must be unique and have valid parents")
        parents[code] = parent
    levels = {}
    for code in parents:
        seen = {code}
        parent = parents[code]
        while parent is not None:
            if parent not in parents or parent in seen or len(seen) == 3:
                raise ToolError(
                    "invalid_input", "the proposed topics must form a three-level forest"
                )
            seen.add(parent)
            parent = parents[parent]
        levels[code] = len(seen)
    return parents, levels


def _bindings(plan, records, parents):
    bindings = {}
    for row in plan["recordings"]:
        checksum = row["checksum"]
        record = records.get(checksum)
        if record is None or checksum in bindings or record["facts"]:
            raise ToolError("invalid_input", "select each active index-only source exactly once")
        conversation = record["conversation"]
        ids = {span["segment_id"] for span in conversation["segments"]}
        selected = []
        for item in row["bindings"]:
            code = norm_code(item["topic"])
            segments = frozenset(item["segment_ids"])
            if code not in parents or not segments <= ids:
                raise ToolError(
                    "invalid_evidence", "topic bindings must cite their selected original"
                )
            selected.append((code, segments))
        bindings[checksum] = selected
    clusters = _independent_recordings(
        {checksum: records[checksum]["conversation"] for checksum, hits in bindings.items() if hits}
    )
    support = defaultdict(dict)
    for checksum, hits in bindings.items():
        if not hits:
            continue
        unit, date = clusters[checksum]
        positions = {
            turn["segment_id"]: i
            for i, turn in enumerate(records[checksum]["conversation"]["segments"])
        }
        for code, segments in hits:
            segment = min(segments, key=positions.__getitem__)
            while code is not None:
                support[code].setdefault(unit, (date, checksum, segment))
                code = parents[code]
    return bindings, support, len(clusters) - len(set(clusters.values()))


def _independent_recordings(conversations):
    """Count a repeated export once during topic promotion."""
    originals = {
        checksum: re.sub(
            r"[\W_]", "", "".join(turn["text"] for turn in conversation["segments"]).casefold()
        )
        for checksum, conversation in conversations.items()
    }
    grams = {
        checksum: {source[i : i + 4] for i in range(len(source) - 3)}
        for checksum, source in originals.items()
    }
    parents = {checksum: checksum for checksum in conversations}

    def representative(checksum):
        while checksum != parents[checksum]:
            parents[checksum] = parents[parents[checksum]]
            checksum = parents[checksum]
        return checksum

    days = {checksum: source_day(conversation) for checksum, conversation in conversations.items()}
    order = sorted(conversations, key=lambda checksum: (days[checksum] or "", checksum))
    order_index = {checksum: i for i, checksum in enumerate(order)}
    for a, b in combinations(order, 2):
        exact = bool(originals[a]) and originals[a] == originals[b]
        small, large = sorted((len(grams[a]), len(grams[b])))
        near_copy = False
        if not exact and small >= 120 and small >= 0.82 * large:
            shared = len(grams[a] & grams[b])
            near_copy = shared / (small + large - shared) >= 0.82
        if exact or near_copy:
            first, second = sorted(
                (representative(a), representative(b)), key=order_index.__getitem__
            )
            parents[second] = first
    return {
        checksum: (unit, days[unit]) for checksum in order if (unit := representative(checksum))
    }


def _govern(parents, levels, support, equivalences):
    vocab = Vocab()
    for code, parent in parents.items():
        if parent is None and support.get(code):
            vocab.define_root(code)
    candidates_proposed = 0
    overflowed_bindings = 0
    applied = []
    suggested_pairs = set()
    sorted_codes = sorted(support, key=lambda code: (levels[code], -len(support[code]), code))
    for level in (1, 2, 3):
        for code in (code for code in sorted_codes if levels[code] == level):
            parent = parents[code]
            if parent is not None:
                parent = vocab.resolve_topic(parent)
            for unit, (date, _, _) in sorted(
                support[code].items(), key=lambda pair: (pair[1][0] or "", pair[0])
            ):
                if level == 1:
                    vocab.touch_topic(code, unit, date or "")
                elif parent and unit in vocab.topics[parent].usage:
                    current = vocab.resolve_topic(code)
                    result = vocab.propose_topic(code, parent, unit, date or "")
                    if result == code and current is None:
                        candidates_proposed += 1
                    if result == parent:
                        overflowed_bindings += 1

        suggested_pairs.update(vocab.consolidation_candidates())
        for old, new in equivalences:
            if level != levels[old]:
                continue
            loser, winner = vocab.resolve_topic(old), vocab.resolve_topic(new)
            if not loser or not winner or loser == winner:
                continue
            if vocab.topics[loser].parents != vocab.topics[winner].parents:
                continue
            old_units = vocab.topics[loser].usage.keys() & support[old].keys()
            new_units = vocab.topics[winner].usage.keys() & support[new].keys()
            if not old_units or not new_units or len(old_units | new_units) < 2:
                continue
            prior = _first_citation(support[old], old_units)
            later_units = new_units - {prior["support_unit"]}
            if not later_units:
                prior = _first_citation(support[old], old_units - new_units)
                later_units = new_units
            vocab.merge_topics([loser], winner)
            applied.append(
                {
                    "from_topic": loser,
                    "to_topic": winner,
                    "from_evidence": prior,
                    "to_evidence": _first_citation(support[new], later_units),
                }
            )

    promoted = vocab.promotion_pass()

    def surviving(code):
        existing = vocab.resolve_topic(code)
        if existing and vocab.topics[existing].status == "canonical":
            return existing
        return surviving(parents[code]) if parents[code] else None

    resolved = {code: surviving(code) for code in support}
    definitions = {
        code: {
            "name": code,
            "parents": sorted({resolved[p] for p in item.parents if resolved.get(p)} - {code}),
            "aliases": sorted(item.aliases),
        }
        for code, item in vocab.topics.items()
        if item.status == "canonical" and code in resolved
    }
    used = set(resolved.values()) - {None}
    definitions = {code: row for code, row in definitions.items() if code in used}
    layer_counts = Counter()
    for code in definitions:
        level, parent = 1, definitions[code]["parents"]
        while parent:
            level += 1
            parent = definitions[parent[0]]["parents"]
        layer_counts[str(level)] += 1
    result = {
        "source_root_topics": sum(parent is None for parent in parents.values()),
        "candidates_admitted": candidates_proposed,
        "bindings_folded_by_admission": overflowed_bindings,
        "candidates_promoted": len(promoted),
        "candidates_left_unpromoted": sum(
            item.status == "candidate" for item in vocab.topics.values()
        ),
        "native_merge_proposals": len(suggested_pairs),
        "aliases_merged": len(applied),
        "merge_examples": applied,
        "levels": {str(level): layer_counts[str(level)] for level in (1, 2, 3)},
        "topics": len(definitions),
    }
    return resolved, definitions, result


def _first_citation(units, selection):
    unit = min(selection, key=lambda key: (units[key][0] or "", key))
    _, checksum, segment = units[unit]
    return {"checksum": checksum, "segment_id": segment, "support_unit": unit}


def _equivalences(items, parents, levels, support):
    pairs = []
    seen = set()
    for item in items:
        old = norm_code(item["from_topic"])
        new = norm_code(item["to_topic"])
        if (
            old in seen
            or new == old
            or not support.get(old)
            or not support.get(new)
            or levels[old] == 1
            or levels[old] != levels[new]
            or parents[old] != parents[new]
        ):
            raise ToolError(
                "invalid_input", "an equivalent pair must be cited and share one parent"
            )
        seen.add(old)
        pairs.append((old, new))
    if any(new in seen for _, new in pairs):
        raise ToolError("invalid_input", "equivalence groups must have one retained target")
    return pairs


def _rebuild_record(context, record, bindings, resolved, definitions):
    line_topics = defaultdict(set)
    for code, segments in bindings:
        topic = resolved[code]
        if topic:
            for segment in segments:
                line_topics[segment].add(topic)
    spans = []
    covered = set()
    for span in record.get("index_spans", ()):
        segment_ids = span["segment_ids"]
        topics = set().union(*(line_topics.get(sid, set()) for sid in segment_ids))
        spans.append({**span, "topics": sorted(topics)})
        covered.update(segment_ids)
    for code, segments in bindings:
        if resolved[code] and (uncovered := segments - covered):
            spans.append({"segment_ids": sorted(uncovered), "topics": [resolved[code]]})
    involved = set().union(*(set(span["topics"]) for span in spans))
    pending = list(involved)
    while pending:
        for parent in definitions[pending.pop()]["parents"]:
            if parent not in involved:
                involved.add(parent)
                pending.append(parent)
    return annotate(
        context,
        {"conversation": record["conversation"], "checksum": record["checksum"]},
        {
            "facts": [],
            "taxonomy": [definitions[topic] for topic in sorted(involved)],
            "entity_aliases": record["entity_aliases"],
            "index_spans": spans,
        },
    )


def preview_topic_rebuild(context, plan: dict) -> dict:
    if plan["version"] != context.workspace.version():
        raise ToolError("stale_snapshot", "rebuild proposals target another workspace version")
    index = context.workspace.index(verify=True)
    records = {row["checksum"]: row for row in index.revisions.values() if row["active"]}
    parents, levels = _catalog(plan["catalog"])
    bindings, support, duplicate_copies = _bindings(plan, records, parents)
    equivalents = _equivalences(plan["equivalences"], parents, levels, support)
    resolved, definitions, governance = _govern(parents, levels, support, equivalents)
    governance["duplicate_recordings_collapsed_for_governance"] = duplicate_copies
    return {
        "plan_sha256": digest(plan),
        "memory_version": plan["version"],
        "records": [
            _rebuild_record(context, records[checksum], frames, resolved, definitions)
            for checksum, frames in bindings.items()
        ],
        "governance": governance,
    }


def commit_topic_rebuild(context, plan: dict, preview: dict) -> dict:
    if preview["memory_version"] != context.workspace.version() or preview["plan_sha256"] != digest(
        plan
    ):
        raise ToolError("stale_snapshot", "the topic rebuild preview is no longer current")
    return {
        **context.workspace.rebuild_topic_postings(preview["records"]),
        "governance": preview["governance"],
    }


def verify_topic_rebuild(context, receipt: dict) -> dict:
    summary = verify_source_batch(
        context, {name: receipt[name] for name in BATCH_RECEIPT["properties"] if name in receipt}
    )
    return {**summary, "governance": receipt["governance"]}
