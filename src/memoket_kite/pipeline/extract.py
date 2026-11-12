"""Build and finalize a Codebook from conversation sessions.

The module follows the Codebook construction order:

1. extract raw Facts from one conversation session;
2. normalize topics, entities, temporal fields, and retrieval facets;
3. serialize Facts and supporting source lines;
4. optionally consolidate topic and entity vocabularies; and
5. optionally align repeated Fact mentions in the completed Codebook.

Consolidation and instance alignment are explicit build-time finalization
stages. They do not change the Fact extraction prompt or query-time behavior.
"""

import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import date

from memoket_kite.core.vocab import Vocab, norm_code
from memoket_kite.pipeline.compile_plan import _entity_preview_codes, _provider_cache_identity
from memoket_kite.prompts.extract import (
    ENTITY_CONSOLIDATION_PROMPT,
    INSTANCE_ALIGNMENT_PROMPT,
    TOPIC_CONSOLIDATION_PROMPT,
)
from memoket_kite.providers.llm import llm_json

_EMPTY_FACET_VALUES = {
    "other",
    "others",
    "none",
    "null",
    "na",
    "misc",
    "miscellaneous",
    "unknown",
    "unspecified",
    "general",
    "generic",
    "various",
    "event",
    "activity",
    "thing",
    "item",
    "stuff",
}
_DUR_RE = re.compile(r"^P(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?$")


# ---------------------------------------------------------------------------
# Fact normalization


def _model_array(value) -> list | tuple:
    """Accept only JSON-array-shaped provider fields.

    Strings and mappings are iterable too, but treating them as arrays turns
    ``"Alice"`` into five entities and a scalar source id into characters.
    Schema drift should discard that field, never manufacture durable symbols.
    """
    return value if isinstance(value, (list, tuple)) else ()


def _normalize_facet_codes(values, *, singularize: bool = True) -> list:
    """Normalize, deduplicate, and discard empty facet values."""
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple)):
        values = []
    codes = []
    for value in values or []:
        if not isinstance(value, str):
            continue
        code = norm_code(value)
        if not code:
            continue
        if singularize and len(code) > 3 and code.endswith("s") and not code.endswith("ss"):
            code = code[:-3] + "y" if code.endswith("ies") else code[:-1]
        if code in _EMPTY_FACET_VALUES:
            continue
        if code not in codes:
            codes.append(code)
    return codes[:4]


def _normalize_duration(value) -> str:
    duration = str(value or "").strip().upper()
    return duration if _DUR_RE.match(duration) and duration != "P" else ""


def _validate_topic_references(vocabulary: Vocab, response: dict) -> list[tuple[int, str]]:
    """Reject unindexable facts before registration can mutate the bank.

    Native resolution already omits unregistered auxiliary labels. A fact with
    another valid topic remains indexable; rejecting its whole response would
    discard valid facts. Return those omitted labels for diagnostics, without
    repairing paths, inventing topics or modifying the cached model response.
    """
    proposed = set()
    for proposal in response["proposals"]:
        if not isinstance(proposal, dict):
            raise RuntimeError("malformed topic proposal; refusing silent discard")
        code, parent = proposal.get("code"), proposal.get("parent")
        if (not isinstance(code, str) or not norm_code(code) or
                not isinstance(parent, str) or not (
                    vocabulary.resolve_topic(parent) or norm_code(parent) in proposed)):
            raise RuntimeError("invalid topic proposal code/parent; refusing silent discard")
        proposed.add(norm_code(code))
    unresolved = []
    for ordinal, fact in enumerate(response["facts"], 1):
        topics = fact.get("topics") if isinstance(fact, dict) else None
        if not isinstance(topics, list) or not 1 <= len(topics) <= 3:
            raise RuntimeError(f"invalid topic list in fact {ordinal}; expected 1-3 references")
        resolved = False
        for code in topics:
            if not isinstance(code, str):
                raise RuntimeError(f"malformed topic reference in fact {ordinal}")
            if norm_code(code) and (vocabulary.resolve_topic(code) or norm_code(code) in proposed):
                resolved = True
            else:
                unresolved.append((ordinal, code))
        if not resolved:
            raise RuntimeError(f"unknown topic reference in fact {ordinal}; refusing silent discard")
    return unresolved


def _register_topic_proposals(vocabulary: Vocab, proposals, session: dict) -> dict[str, str]:
    """Register valid proposed topics and return their normalized parent map."""
    # Normalise BEFORE testing: " ", "...", "—" are all truthy as raw strings
    # but normalise to "". An empty code that gets past here is written as
    # <topic code="">, which is well-formed XML, so the damage survives the
    # write and the loader rejects the Codebook on every subsequent load.
    topic_parents = {}
    for proposal in _model_array(proposals):
        if not isinstance(proposal, dict):
            continue
        raw_code, raw_parent = proposal.get("code"), proposal.get("parent")
        if not isinstance(raw_code, str) or not isinstance(raw_parent, str):
            continue
        code = norm_code(raw_code)
        if not code:
            continue
        topic_parents[code] = norm_code(raw_parent)
    for topic_code, parent_code in topic_parents.items():
        vocabulary.propose_topic(
            topic_code,
            parent_code,
            session["id"],
            session.get("bucket", ""),
        )
    return topic_parents


def _resolve_fact_topics(
    vocabulary: Vocab,
    raw_topics,
    proposed_parents: dict[str, str],
    session: dict,
) -> list[str]:
    """Resolve a Fact's topics, admitting only proposals with known parents."""
    topic_codes = []
    for topic_name in _model_array(raw_topics)[:3]:
        if not isinstance(topic_name, str):
            continue
        topic_code = vocabulary.resolve_topic(topic_name)
        if not topic_code:
            normalized_topic = norm_code(topic_name)
            parent_code = proposed_parents.get(normalized_topic, "")
            if parent_code:
                topic_code = vocabulary.propose_topic(
                    normalized_topic,
                    parent_code,
                    session["id"],
                    session.get("bucket", ""),
                )
            if not topic_code and parent_code:
                topic_code = vocabulary.resolve_topic(parent_code)
        if topic_code:
            vocabulary.touch_topic(topic_code, session["id"], session.get("bucket", ""))
            if topic_code not in topic_codes:
                topic_codes.append(topic_code)
    return topic_codes


def _register_fact_entities(
    vocabulary: Vocab,
    raw_entities,
    entity_types: dict[str, str],
    profile,
) -> list[str]:
    """Apply a profile's entity filter and register the remaining entities."""
    entity_codes = []
    entity_filter = getattr(profile, "entity_filter", None)
    for entity_name in _model_array(raw_entities):
        if not isinstance(entity_name, str) or not norm_code(entity_name):
            continue
        if entity_filter and not entity_filter(entity_name):
            continue
        entity_code = vocabulary.register_entity(
            entity_name, entity_types.get(norm_code(entity_name), "")
        )
        if entity_code and entity_code not in entity_codes:
            entity_codes.append(entity_code)
    return entity_codes


def _normalize_fact_facets(raw_facets, allowed_events: set[str]) -> dict[str, object]:
    """Map public facet names onto the existing internal XML attribute names."""
    if not isinstance(raw_facets, dict):
        return {}
    facets: dict[str, object] = {}
    for facet_name, attribute in (
        ("object", "obj"),
        ("place", "place"),
        ("event", "event"),
    ):
        # Places are proper identifiers: a plural-looking suffix can be part
        # of the name. Keep the existing class/event normalization unchanged.
        values = _normalize_facet_codes(raw_facets.get(facet_name), singularize=facet_name != "place")
        if facet_name == "event":
            values = [value for value in values if value in allowed_events]
        if values:
            facets[attribute] = values
    duration = _normalize_duration(raw_facets.get("duration"))
    if duration:
        facets["dur"] = duration
    return facets


def _normalize_fact_time(value) -> str:
    """Keep only real calendar dates at the precision promised to the model."""
    candidate = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{1,4}-\d{1,2}", candidate):
            year, month = map(int, candidate.split("-"))
            date(year, month, 1)
            return f"{year:04d}-{month:02d}"
        if re.fullmatch(r"\d{1,4}-\d{1,2}-\d{1,2}", candidate):
            return date(*map(int, candidate.split("-"))).isoformat()
    except ValueError:
        pass
    return ""


def _normalize_extracted_fact(
    raw_fact: dict,
    vocabulary: Vocab,
    profile,
    session: dict,
    proposed_parents: dict[str, str],
    entity_types: dict[str, str],
    source_ids: set[str],
    include_facets: bool,
    allowed_events: set[str],
    *,
    fixed_source_id: str | None = None,
) -> dict:
    """Validate one raw model Fact and convert it to the internal XML shape."""
    allowed_kinds = {norm_code(value) for value in getattr(profile, "KINDS", ())}
    raw_kind = raw_fact.get("kind")
    kind = norm_code(raw_kind) if isinstance(raw_kind, str) else "other"
    if kind not in allowed_kinds:
        kind = "other"
    raw_content = raw_fact.get("content")
    raw_who = raw_fact.get("who")
    normalized_fact = {
        "content": raw_content.strip() if isinstance(raw_content, str) else "",
        "kind": kind,
        "who": raw_who.strip().lower() if isinstance(raw_who, str) else "",
        "t": _normalize_fact_time(raw_fact.get("t")),
        "conf": raw_fact.get("conf") if raw_fact.get("conf") in ("high", "med", "low") else "med",
        "topics": _resolve_fact_topics(
            vocabulary, raw_fact.get("topics"), proposed_parents, session
        ),
        "entities": _register_fact_entities(
            vocabulary, raw_fact.get("entities"), entity_types, profile
        ),
        "src": [
            source_id
            for source_id in ([fixed_source_id] if fixed_source_id is not None
                              else _model_array(raw_fact.get("src")))
            if isinstance(source_id, str) and source_id in source_ids
        ],
    }
    if include_facets:
        normalized_fact.update(_normalize_fact_facets(raw_fact.get("facets"), allowed_events))
    return normalized_fact


# ---------------------------------------------------------------------------
# Session extraction


def extract_facts(
    vocabulary: Vocab,
    profile,
    session: dict,
    model: str = "gpt-4.1-mini",
    cache_dir: str | None = None,
    log=print,
    include_facets: bool | None = None,
    *,
    cache_only: bool = False,
    response_format: dict | None = None,
    expected_prompt_sha256: str | None = None,
) -> list[dict]:
    """Extract normalized Facts from one session.

    ``session`` supplies an id, date, speakers, and source ``utterances``. A
    normal session is one utterance group and makes one LLM request. Builders
    may provide multiple ``utterance_groups`` to split a large session; each
    group makes one request and their responses are combined.

    Each returned Fact retains only valid source ids. Topics and entities are
    resolved through ``vocabulary`` before return. Profiles may enable object,
    place, event, and duration facets by default; ``include_facets=False``
    provides the facet-free ablation.

    ``cache_only=True`` replays a completed raw response for this exact input
    or raises before any API call. Builders use it for previously attempted
    sources so corrupt or stale cache entries cannot silently trigger billing.
    """
    if include_facets is None:
        include_facets = bool(getattr(profile, "EXTRACT_FACETS", False))
    if not isinstance(include_facets, bool):
        raise TypeError("include_facets must be a bool or None")
    single_source = bool(getattr(profile, "EXTRACT_SINGLE_SOURCE", False))
    if single_source and (len(session.get("utterances", [])) != 1 or
                          (session.get("utterance_groups") and
                           session["utterance_groups"] != [session["utterances"]])):
        raise ValueError("single-source extraction requires exactly one input line/group")
    extraction_response = _extract_facts_with_cache(
        vocabulary,
        profile,
        session,
        model,
        cache_dir,
        log,
        include_facets,
        cache_only=cache_only,
        response_format=response_format,
        expected_prompt_sha256=expected_prompt_sha256,
    )
    source_ids = {str(utterance[0]) for utterance in session["utterances"]}
    if single_source or getattr(profile, "EXTRACT_STRICT_SOURCES", False):
        for ordinal, fact in enumerate(extraction_response["facts"], 1):
            if not isinstance(fact, dict) or not isinstance(fact.get("content"), str) or not fact["content"].strip():
                raise RuntimeError(f"empty or malformed source fact {ordinal}; refusing silent drop")
            # A sole input line establishes provenance without a model decision.
            # If the model nevertheless supplied a pointer, do not repair it.
            if single_source and "src" not in fact:
                continue
            pointers = fact.get("src")
            if not isinstance(pointers, list) or not pointers or any(
                    not isinstance(x, str) or x not in source_ids for x in pointers):
                raise RuntimeError(f"invalid source pointer in fact {ordinal}; refusing silent drop or rewrite")
    if getattr(profile, "EXTRACT_STRICT_TOPICS", False):
        unresolved_topics = _validate_topic_references(vocabulary, extraction_response)
        if unresolved_topics:
            log("  native topic resolution omits unregistered auxiliary labels: "
                + ", ".join(f"fact {ordinal}: {code!r}" for ordinal, code in unresolved_topics))
    proposed_parents = _register_topic_proposals(
        vocabulary, extraction_response.get("proposals"), session
    )
    raw_entity_types = extraction_response.get("entity_types") or {}
    entity_types = (
        {
            norm_code(name): entity_type
            for name, entity_type in raw_entity_types.items()
            if isinstance(name, str) and isinstance(entity_type, str)
        }
        if isinstance(raw_entity_types, dict)
        else {}
    )
    allowed_events = {norm_code(event_type) for event_type in getattr(profile, "EVENTS", ()) or ()}
    facts = []
    for raw_fact in _model_array(extraction_response.get("facts")):
        if not isinstance(raw_fact, dict):
            continue
        normalized_fact = _normalize_extracted_fact(
            raw_fact,
            vocabulary,
            profile,
            session,
            proposed_parents,
            entity_types,
            source_ids,
            include_facets,
            allowed_events,
            fixed_source_id=next(iter(source_ids)) if single_source else None,
        )
        if normalized_fact["content"] and normalized_fact["src"]:
            facts.append(normalized_fact)
    return facts


def _render_extraction_tree(vocabulary, profile):
    """Keep the bounded preview while exposing every existing root when opted in."""
    tree = vocabulary.render_tree(max_lines=150)
    if not getattr(profile, "EXTRACT_COMPLETE_ROOTS", False):
        return tree
    visible_roots = {line.removesuffix("?") for line in tree.splitlines()
                     if line and not line[0].isspace()}
    missing = [code + ("?" if topic.status == "candidate" else "")
               for code, topic in sorted(vocabulary.topics.items())
               if not topic.parents and topic.status != "deprecated" and code not in visible_roots]
    return "\n".join([tree, *missing]) if missing else tree


def _extract_facts_with_cache(
    vocabulary,
    profile,
    session,
    model,
    cache_dir,
    log,
    include_facets,
    *,
    cache_only=False,
    response_format=None,
    expected_prompt_sha256=None,
):
    """Load or request raw extraction responses for every utterance group."""
    template = (
        profile.EXTRACT_PROMPT
        if include_facets
        else getattr(profile, "EXTRACT_PROMPT_NO_FACETS", profile.EXTRACT_PROMPT)
    )
    utterance_groups = session.get("utterance_groups") or [session["utterances"]]
    prompts = []
    for utterance_group in utterance_groups:
        conversation_text = "\n".join(
            f"{utterance_id}|{speaker}|{time_mark}|{content}"
            for utterance_id, speaker, time_mark, content in utterance_group
        )
        prompts.append(
            template.format(
                tree=_render_extraction_tree(vocabulary, profile),
                entities=", ".join(_entity_preview_codes(
                    vocabulary, session.get("title", "") + "\n" + conversation_text, 80,
                    by_mention=getattr(profile, "ENTITY_PREVIEW_BY_MENTION", False),
                )) or "(none)",
                kinds="|".join(profile.KINDS),
                events="|".join(getattr(profile, "EVENTS", ()) or ()) or "(none; use [])",
                date=session["date"],
                weekday=session.get("weekday", "?"),
                time=session.get("time", "?"),
                title=session.get("title", ""),
                speakers=", ".join(session.get("speakers", [])),
                dialog=conversation_text,
            )
        )
    if expected_prompt_sha256 is not None and (
            len(prompts) != 1 or hashlib.sha256(prompts[0].encode()).hexdigest() != expected_prompt_sha256):
        raise RuntimeError("format recovery requires the exact original prompt before any API call")
    cache_path = None
    effort = getattr(profile, "EXTRACT_REASONING_EFFORT", None)
    call_options = {"reasoning_effort": effort} if effort is not None else {}
    if response_format is not None:
        call_options["response_format"] = response_format
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        supports_facet_ablation = hasattr(profile, "EXTRACT_PROMPT_NO_FACETS")
        variant = (".facets" if include_facets else ".no-facets") if supports_facet_ablation else ""
        # The key is the FULLY RENDERED prompt sequence, plus model and
        # provider. Taxonomy, entities, dates, speakers and dialog are all
        # rendered into the prompt, so hashing the template alone would let
        # the same session id hit under different content, or the same content
        # hit under an evolved vocabulary. Either hit returns Facts for bytes
        # the model never saw, and nothing downstream can detect that the
        # Codebook no longer matches the conversation it claims to describe.
        identity = [model, _provider_cache_identity(), *prompts]
        if effort is not None:
            # Different generation settings must never replay a medium response
            # as non-reasoning extraction. Unconfigured legacy keys stay intact.
            identity.append(json.dumps({"reasoning_effort": effort, "temperature": 0.0},
                                       sort_keys=True))
        if response_format is not None:
            identity.append(json.dumps({"response_format": response_format}, sort_keys=True))
        fingerprint = hashlib.sha256("\x1f".join(identity).encode("utf-8")).hexdigest()[:12]
        cache_path = os.path.join(cache_dir, f"{session['id']}{variant}.{fingerprint}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as stream:
                    cached = json.load(stream)
            except (OSError, json.JSONDecodeError):
                cached = None
            if (
                isinstance(cached, dict)
                and isinstance(cached.get("facts"), list)
                and isinstance(cached.get("proposals"), list)
                and isinstance(cached.get("entity_types"), dict)
            ):
                return cached
    if cache_only:
        raise RuntimeError("exact extraction cache missing or invalid; cache-only mode forbids API calls")
    extraction_response = {"facts": [], "proposals": [], "entity_types": {}}
    for prompt in prompts:
        response = llm_json(prompt, model=model, **call_options)
        if (getattr(profile, "EXTRACT_STRICT_SOURCES", False) or
                getattr(profile, "EXTRACT_SINGLE_SOURCE", False) or
                getattr(profile, "EXTRACT_STRICT_TOPICS", False)) and not (
                    isinstance(response.get("facts"), list) and
                    isinstance(response.get("proposals"), list) and
                    isinstance(response.get("entity_types"), dict)):
            if cache_path:
                _write_extraction_cache(cache_path, response)
            raise RuntimeError("malformed extraction response; refusing an empty-success coercion")
        extraction_response["facts"].extend(_model_array(response.get("facts")))
        extraction_response["proposals"].extend(_model_array(response.get("proposals")))
        raw_types = response.get("entity_types")
        if isinstance(raw_types, dict):
            extraction_response["entity_types"].update(raw_types)
    log(
        f"  extract {session['id']} ({session['date']}): "
        f"{sum(len(group) for group in utterance_groups)} utterances -> "
        f"{len(extraction_response['facts'])} facts, "
        f"{len(extraction_response['proposals'])} topic proposals"
    )
    if cache_path:
        _write_extraction_cache(cache_path, extraction_response)
    return extraction_response


def _write_extraction_cache(cache_path, response):
    """Preserve a complete raw response, including invalid shapes, atomically."""
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".extract-", suffix=".tmp", dir=os.path.dirname(cache_path), text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(response, stream, ensure_ascii=False)
        os.replace(temporary_path, cache_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\uFFFE\uFFFF]")

# ---------------------------------------------------------------------------
# Codebook serialization


def _xml_text(text: str) -> str:
    """Remove characters forbidden by XML 1.0."""
    return _XML_ILLEGAL.sub("", text or "")


def append_session_to_xml(
    timeline: ET.Element,
    profile,
    session: dict,
    facts: list[dict],
) -> ET.Element:
    """Append one conversation session and its extracted Facts to an XML timeline."""
    el = ET.SubElement(
        timeline,
        profile.SESSION_TAG,
        id=session["id"],
        t=session.get("t", ""),
        date=session["date"],
        dur=session.get("dur", ""),
        title=_xml_text(session.get("title", "")),
    )
    for index, fact in enumerate(facts, 1):
        attributes = dict(
            id=f"{session['id']}F{index}",
            kind=fact["kind"],
            who=fact["who"],
            t=fact["t"],
            conf=fact["conf"],
            topics=" ".join(fact["topics"]),
            entities=" ".join(fact["entities"]),
            src=" ".join(fact["src"]),
        )
        for attribute in ("obj", "place", "event"):
            if fact.get(attribute):
                attributes[attribute] = " ".join(fact[attribute])
        if fact.get("dur"):
            attributes["dur"] = fact["dur"]
        fact_element = ET.SubElement(el, "fact", **attributes)
        fact_element.text = _xml_text(fact["content"])
    for utterance_id, speaker, time_mark, content in session["utterances"]:
        utterance_element = ET.SubElement(
            el,
            "line",
            id=str(utterance_id),
            who=str(speaker).lower(),
            t=str(time_mark),
        )
        utterance_element.text = _xml_text(content)
    return el


# ---------------------------------------------------------------------------
# Vocabulary consolidation


def _consolidation_round_votes(data: dict, pairs: list[tuple[str, str]]) -> dict[int, str | None]:
    """Return at most one schema-valid vote per candidate for one model call."""
    round_votes: dict[int, str | None] = {}
    for decision in _model_array(data.get("decisions")):
        if not isinstance(decision, dict):
            continue
        raw_index = decision.get("i")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            continue
        index = raw_index
        if index < 0 or index >= len(pairs) or index in round_votes:
            # A response is one ballot. Repeating an index in that response
            # must not manufacture the two independent votes required to make
            # a permanent merge.
            continue
        raw_winner = decision.get("winner")
        winner = norm_code(raw_winner) if isinstance(raw_winner, str) else ""
        round_votes[index] = (
            winner if decision.get("merge") is True and winner in pairs[index] else None
        )
    return round_votes


def consolidate_topics(
    vocabulary: Vocab,
    memory_roots: list[ET.Element],
    model: str = "gpt-4.1-mini",
    adjudicate: bool = True,
    log=print,
) -> dict:
    """Merge equivalent topic codes and rewrite stored Fact topics."""
    candidates = vocabulary.consolidation_candidates()
    if not candidates:
        return {}
    automatic_merges, review_pairs = [], []
    for first_code, second_code in candidates:
        if re.search(r"\d", first_code) and re.search(r"\d", second_code):
            continue
        if (
            first_code.startswith(second_code)
            or second_code.startswith(first_code)
            or first_code.endswith(second_code)
            or second_code.endswith(first_code)
        ):
            automatic_merges.append((first_code, second_code))
        else:
            review_pairs.append((first_code, second_code))
    topic_rewrites: dict[str, str] = {}
    merged_codes: set[str] = set()

    def merge_topic(loser: str, winner: str):
        if loser in merged_codes or winner in merged_codes:
            return
        if vocabulary.resolve_topic(winner) == winner and vocabulary.resolve_topic(loser) == loser:
            topic_rewrites.update(vocabulary.merge_topics([loser], winner))
            merged_codes.add(loser)
            merged_codes.add(winner)

    for first_code, second_code in automatic_merges:
        winner, loser = (
            (first_code, second_code)
            if len(first_code) <= len(second_code)
            else (second_code, first_code)
        )
        merge_topic(loser, winner)
    if review_pairs and adjudicate:
        votes: dict[int, list] = {index: [] for index in range(len(review_pairs))}
        prompt = TOPIC_CONSOLIDATION_PROMPT.format(
            pairs="\n".join(
                f"{index}: {first_code} | {second_code}"
                for index, (first_code, second_code) in enumerate(review_pairs)
            )
        )
        for _ in range(3):
            try:
                data = llm_json(prompt, model=model)
                round_votes = _consolidation_round_votes(data, review_pairs)
                for index in votes:
                    votes[index].append(round_votes.get(index))
            except RuntimeError as error:
                log(f"  topic consolidation vote skipped: {error}")
        for index, (first_code, second_code) in enumerate(review_pairs):
            winners = [winner for winner in votes[index] if winner]
            if len(winners) >= 2 and len(set(winners)) == 1:
                winner = winners[0]
                merge_topic(second_code if winner == first_code else first_code, winner)
    if topic_rewrites:
        for memory_root in memory_roots:
            _rewrite_fact_topics(memory_root, topic_rewrites)
        log(f"  consolidated {len(topic_rewrites)} codes: {topic_rewrites}")
    return topic_rewrites


def _rewrite_fact_topics(memory_root: ET.Element, topic_rewrites: dict):
    _rewrite_fact_attribute(memory_root, "topics", topic_rewrites)


def _rewrite_fact_attribute(memory_root: ET.Element, attribute: str, rewrites: dict):
    for fact in memory_root.iter("fact"):
        original_codes = (fact.get(attribute) or "").split()
        rewritten_codes = []
        for code in original_codes:
            rewritten_code = rewrites.get(code, code)
            if rewritten_code not in rewritten_codes:
                rewritten_codes.append(rewritten_code)
        fact.set(attribute, " ".join(rewritten_codes))


def consolidate_entities(
    vocabulary: Vocab,
    memory_roots: list[ET.Element],
    model: str = "gpt-4.1-mini",
    batch_size: int = 60,
    log=print,
) -> dict:
    """Merge equivalent entity codes and rewrite stored Fact entities."""
    candidates = vocabulary.entity_merge_candidates()
    if not candidates:
        return {}
    entity_rewrites: dict[str, str] = {}
    merged_codes: set[str] = set()
    for start in range(0, len(candidates), batch_size):
        candidate_batch = candidates[start : start + batch_size]
        votes: dict[int, list] = {index: [] for index in range(len(candidate_batch))}
        prompt = ENTITY_CONSOLIDATION_PROMPT.format(
            pairs="\n".join(
                f"{index}: {first_code} | {second_code}"
                for index, (first_code, second_code) in enumerate(candidate_batch)
            )
        )
        for _ in range(3):
            try:
                data = llm_json(prompt, model=model)
                round_votes = _consolidation_round_votes(data, candidate_batch)
                for index in votes:
                    votes[index].append(round_votes.get(index))
            except RuntimeError as error:
                log(f"  entity consolidation vote skipped: {error}")
        for index, (first_code, second_code) in enumerate(candidate_batch):
            winners = [winner for winner in votes[index] if winner]
            if len(winners) >= 2 and len(set(winners)) == 1:
                winner = winners[0]
                loser = second_code if winner == first_code else first_code
                if loser in merged_codes or winner in merged_codes:
                    continue
                if loser in vocabulary.entities and winner in vocabulary.entities:
                    entity_rewrites.update(vocabulary.merge_entities([loser], winner))
                    merged_codes.add(loser)
                    merged_codes.add(winner)
    if entity_rewrites:
        for memory_root in memory_roots:
            _rewrite_fact_attribute(memory_root, "entities", entity_rewrites)
        log(
            f"  entity-consolidated {len(entity_rewrites)}: "
            f"{dict(list(entity_rewrites.items())[:8])}{'...' if len(entity_rewrites) > 8 else ''}"
        )
    return entity_rewrites


# ---------------------------------------------------------------------------
# Optional completed-Codebook instance alignment


_INSTANCE_SKIP_KINDS = {"preference", "opinion", "emotion", "knowledge"}
_INSTANCE_STOPWORDS = set(
    """about after again all also always another around because been before being
between could every first found gave getting goes going gotten great have having
just know known last like little looking made make many month more most much need
never next only other over really right said same should since some something
still such sure take than that their them then there these they thing think this
those through time today took very want week were what when where which while
will with would year your""".split()
)


def _instance_keys(text: str, document_frequency, max_frequency: int) -> set[str]:
    """Return discriminative stems for one Fact mention."""
    keys = set()
    for word in re.findall(r"[a-zA-Z]{5,}", text.lower()):
        if word in _INSTANCE_STOPWORDS:
            continue
        stem = word[:6]
        if document_frequency(stem) <= max_frequency:
            keys.add(stem)
    return keys


def _candidate_instance_clusters(
    store,
    max_frequency: int = 12,
    max_cluster_size: int = 14,
) -> list[list[str]]:
    """Group cross-session Fact mentions by shared discriminative stems."""
    fact_frequency: dict[str, int] = {}
    for fact in store.facts.values():
        for word in set(re.findall(r"[a-zA-Z]{5,}", fact.text.lower())):
            stem = word[:6]
            fact_frequency[stem] = fact_frequency.get(stem, 0) + 1

    def document_frequency(stem: str) -> int:
        return fact_frequency.get(stem, 0)

    facts_by_key: dict[str, list[str]] = {}
    for fact_id, fact in store.facts.items():
        if fact.kind in _INSTANCE_SKIP_KINDS:
            continue
        for key in _instance_keys(fact.text, document_frequency, max_frequency):
            facts_by_key.setdefault(key, []).append(fact_id)

    # Keep one cluster per discriminative key. Transitive merging can join
    # loosely related mentions into an over-broad candidate set.
    clusters: list[list[str]] = []
    ordered_groups = sorted(
        facts_by_key.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    for _, fact_ids in ordered_groups:
        if not 2 <= len(fact_ids) <= max_cluster_size:
            continue
        if len({store.facts[fact_id].unit for fact_id in fact_ids}) < 2:
            continue
        candidate = set(fact_ids)
        if any(candidate <= set(cluster) for cluster in clusters):
            continue
        clusters.append(sorted(fact_ids))
    return clusters


def _align_instances(
    store,
    model: str,
    batch_size: int = 12,
    log=print,
) -> list[dict]:
    """Resolve candidate clusters into distinct real-world instances."""
    candidate_clusters = _candidate_instance_clusters(store)
    aligned_instances = []
    sequence = 0
    for start in range(0, len(candidate_clusters), batch_size):
        cluster_batch = candidate_clusters[start : start + batch_size]
        cluster_lines = []
        for cluster_index, fact_ids in enumerate(cluster_batch):
            cluster_lines.append(f"CLUSTER {cluster_index}:")
            for fact_id in fact_ids:
                fact = store.facts[fact_id]
                cluster_lines.append(
                    f"  {fact_id} [{fact.unit_date}] ({fact.kind}) {fact.text[:160]}"
                )
        try:
            response = llm_json(
                INSTANCE_ALIGNMENT_PROMPT.format(clusters="\n".join(cluster_lines)),
                model=model,
            )
        except Exception as error:
            log(f"  instance alignment batch {start // batch_size}: {error}")
            continue

        valid_fact_ids = {fact_id for fact_ids in cluster_batch for fact_id in fact_ids}
        for cluster in response.get("clusters", []):
            for instance in cluster.get("instances", []):
                mention_ids = [
                    fact_id for fact_id in (instance.get("ids") or []) if fact_id in valid_fact_ids
                ]
                if not mention_ids:
                    continue
                sequence += 1
                dates = sorted(
                    store.facts[fact_id].t or store.facts[fact_id].unit_date
                    for fact_id in mention_ids
                )
                aligned_instances.append(
                    {
                        "id": f"I{sequence}",
                        "label": (instance.get("label") or "")[:80],
                        "kind": (instance.get("kind") or "other")[:12],
                        "t": dates[0] if dates else "",
                        "mentions": mention_ids,
                    }
                )

    # Candidate clusters may overlap. Prefer the instance with the richer
    # mention set when another instance is wholly contained within it.
    aligned_instances.sort(key=lambda item: -len(item["mentions"]))
    deduplicated = []
    for instance in aligned_instances:
        mention_ids = set(instance["mentions"])
        if any(mention_ids <= set(existing["mentions"]) for existing in deduplicated):
            continue
        deduplicated.append(instance)
    for index, instance in enumerate(deduplicated, 1):
        instance["id"] = f"I{index}"
    return deduplicated


def _write_instance_registry(xml_path: str, instances: list[dict]) -> None:
    """Atomically replace the instance registry in a completed Codebook."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    existing_registry = root.find("instances")
    if existing_registry is not None:
        root.remove(existing_registry)
    registry = ET.SubElement(root, "instances")
    for instance in instances:
        ET.SubElement(
            registry,
            "instance",
            id=instance["id"],
            label=instance["label"],
            kind=instance["kind"],
            t=instance["t"],
            mentions=" ".join(instance["mentions"]),
        )
    ET.indent(root)
    directory = os.path.dirname(os.path.abspath(xml_path))
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".xml",
            prefix=".instances-",
            dir=directory,
            delete=False,
        ) as handle:
            temporary = handle.name
            tree.write(handle, encoding="utf-8", xml_declaration=True)
        # ElementTree serializes XML-forbidden control characters without
        # rejecting them.  Provider-created labels and kinds therefore need a
        # parse of the completed bytes before they can replace the only good
        # Codebook.  The same temporary-file boundary also prevents a partial
        # write from truncating the durable artifact.
        ET.parse(temporary)
        os.replace(temporary, xml_path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def finalize_instance_alignment(
    xml_path: str,
    model: str,
    batch: int = 12,
    log=print,
) -> list[dict]:
    """Add a cross-session instance registry to a completed Codebook."""
    from memoket_kite.core.algebra import Store

    store, _ = Store.load([xml_path])
    instances = _align_instances(
        store,
        model=model,
        batch_size=batch,
        log=log,
    )
    _write_instance_registry(xml_path, instances)
    return instances
