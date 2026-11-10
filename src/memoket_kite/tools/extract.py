"""Opt-in model extraction through the same KITE extractor used by Memory."""

from __future__ import annotations

from memoket_kite.core.vocab import Vocab
from memoket_kite.errors import ProviderError
from memoket_kite.providers.llm import ensure_configured
from memoket_kite.remember import extract_facts

from .contracts import TEXT, ToolError, object_schema
from .ingest import source_day

MODEL = object_schema({"name": TEXT})


def propose(*, allow_provider: bool, normalized: dict, model: dict) -> dict:
    if not allow_provider:
        raise ToolError("permission_denied", "provider extraction requires --allow-provider")
    try:
        ensure_configured(model["name"])
    except ProviderError as exc:
        raise ToolError("configuration_error", str(exc)) from exc
    conversation = normalized["conversation"]
    segments = {f"S{number}": segment for number, segment in enumerate(conversation["segments"], 1)}
    speakers = sorted(
        {segment["speaker_id"] for segment in segments.values() if "speaker_id" in segment}
    )
    session = {
        "id": "candidate",
        "date": source_day(conversation) or "",
        "speakers": speakers,
        "title": conversation.get("title", ""),
        "utterances": [
            (sid, segment.get("speaker_id", ""), "", segment["text"])
            for sid, segment in segments.items()
        ],
    }
    # The user's opt-in covers this conversation. Workspace taxonomy and
    # entity labels can come from other sources and stay local.
    vocab = Vocab()
    vocab.define_root("general")
    try:
        facts = extract_facts(vocab, session, model=model["name"], include_facets=True)
    except (ProviderError, RuntimeError, OSError) as exc:
        raise ToolError("provider_error", "KITE extraction provider request failed") from exc
    used_topics = {topic for fact in facts for topic in fact["topics"]}
    return {
        "taxonomy": [
            {"name": code, "parents": sorted(vocab.topics[code].parents)}
            for code in sorted(used_topics - {"general"})
        ],
        "facts": [
            {
                "content": fact["content"],
                "kind": fact["kind"] or "statement",
                "segment_ids": [segments[sid]["segment_id"] for sid in fact["src"]],
                "topics": fact["topics"] or ["general"],
                "entities": fact["entities"],
                "objects": fact.get("obj", []),
                "places": fact.get("place", []),
                "events": fact.get("event", []),
                **({"subject": fact["who"]} if fact["who"] else {}),
                **({"confidence": fact["conf"]} if fact["conf"] else {}),
                **({"event_date": fact["t"]} if len(fact.get("t", "")) == 10 else {}),
            }
            for fact in facts
        ],
    }
