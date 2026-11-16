"""Compile a natural-language question into a symbolic retrieval plan.

The LLM sees the taxonomy tree, entity registry, unit index and emits plan JSON
(schema below). The algebra module validates and resolves the plan before
execution, so model output never reaches the Store unchecked.

Plan JSON schema:
{"queries": [                       # 1-3 complementary subqueries
   {"select": "facts|lines|units",
    "where": {"topics": [{"code": str, "closure": bool}],
              "entities": [str], "kind": [str], "who": [str],
              "time": [lo, hi], "grep": str},
    "pipe": [{"op": "sort", "key": "t|dur", "desc": bool},
             {"op": "head|tail", "n": int}, {"op": "count"}]}],
 "intent": "factual|speculative|aggregate|enumeration"}
"""

import hashlib
import json
import os
import tempfile

from memoket_kite.providers.llm import llm_json

_CACHE_SCHEMA = "3"


def _hash_record(digest, *values) -> None:
    digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode())
    digest.update(b"\n")


def _provider_cache_identity() -> str:
    """Identify the OpenAI-compatible endpoint without exposing credentials."""
    return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _validated_compiled_plan(value: object) -> dict:
    """Apply the public symbolic schema before scoring or caching model output."""
    # Imported lazily to keep the low-level provider/import path lightweight.
    from memoket_kite.research.errors import InvalidQueryError
    from memoket_kite.research.query import QueryPlan

    try:
        return QueryPlan.from_dict(value).to_dict()
    except (InvalidQueryError, TypeError, ValueError) as exc:
        raise RuntimeError(f"compiler returned an invalid query plan: {exc}") from exc


def _read_cached_plan(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as stream:
            plan = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return _validated_compiled_plan(plan)
    except RuntimeError:
        return None


def _write_cached_plan(path: str, plan: dict) -> None:
    """Publish a complete cache entry atomically for concurrent readers."""
    directory = os.path.dirname(path)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".plan-",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(plan, stream, ensure_ascii=False)
        os.replace(temporary_path, path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def _scoring_context_fingerprint(store, vocab) -> str:
    """Hash the symbolic state that can change deterministic plan scoring.

    The full hash walks every fact, line, unit, and df token — O(corpus) — so
    it is memoized on the Store. Codebooks are read-only after load; the memo
    key still tracks cheap mutation counters so post-load writes invalidate."""
    memo_key = (
        len(store.facts),
        len(store.lines),
        len(store.units),
        store._ndocs,
        len(vocab.topics),
        len(vocab.entities),
        len(vocab.oplog),
        len(vocab._alias_t),
        len(vocab._alias_e),
    )
    memo = getattr(store, "_scoring_fp_memo", None)
    if memo is not None and memo[0] == memo_key:
        return memo[1]
    digest = hashlib.sha256()
    for fact_id in sorted(store.facts):
        fact = store.facts[fact_id]
        _hash_record(
            digest,
            "fact",
            fact.id,
            fact.unit,
            fact.unit_date,
            fact.t,
            fact.kind,
            fact.who,
            fact.conf,
            fact.topics,
            fact.entities,
            fact.src,
            fact.text,
            fact.obj,
            fact.place,
            fact.event,
            fact.dur,
        )
    for line_id in sorted(store.lines):
        line = store.lines[line_id]
        _hash_record(digest, "line", line.id, line.unit, line.unit_date, line.who, line.text)
    for unit_id in sorted(store.units):
        unit = store.units[unit_id]
        _hash_record(
            digest,
            "unit",
            unit.id,
            unit.date,
            unit.t,
            unit.title,
            unit.dur_min,
            unit.n_lines,
        )
    _hash_record(digest, "speakers", tuple(sorted(store.speakers)))
    _hash_record(digest, "lexical_documents", store._ndocs)
    for token, document_frequency in sorted(store._df.items()):
        _hash_record(digest, "document_frequency", token, document_frequency)
    for entity_code, object_classes in sorted(store.obj_of.items()):
        _hash_record(digest, "entity_object_classes", entity_code, tuple(sorted(object_classes)))
    for code in sorted(vocab.topics):
        topic = vocab.topics[code]
        _hash_record(
            digest,
            "topic",
            code,
            topic.status,
            tuple(sorted(topic.parents)),
            tuple(sorted(topic.aliases)),
        )
    for code in sorted(vocab.entities):
        entity = vocab.entities[code]
        _hash_record(
            digest,
            "entity",
            code,
            entity.etype,
            entity.name,
            tuple(sorted(entity.aliases)),
            tuple(sorted(entity.rels)),
        )
    _hash_record(digest, "topic_aliases", tuple(sorted(vocab._alias_t.items())))
    _hash_record(digest, "entity_aliases", tuple(sorted(vocab._alias_e.items())))
    result = digest.hexdigest()
    store._scoring_fp_memo = (memo_key, result)
    return result


# ---------------------------------------------------------------------------
# Public compilation flow


def compile_plan(
    question: str,
    vocab,
    store,
    profile,
    model: str = "gpt-4.1-mini",
    scorer=None,
    n_candidates: int = 3,
    today: str = "",
    cache_dir: str | None = None,
    scorer_cache_key: str | None = None,
) -> dict:
    """NL -> plan. With a deterministic plan-quality scorer, samples
    up to n_candidates plans and caches the WINNER — converting compile
    variance into quality instead of freezing a random draw. The cache key
    fingerprints the exact model prompt and compilation settings. Scored-mode
    caching additionally requires ``scorer_cache_key`` and fingerprints the
    symbolic scoring context, so changed Facts or scorer semantics cannot reuse
    a stale winner."""
    prompt = render_compile_prompt(question, vocab, store, profile, today=today)
    cache_path = None
    # Arbitrary scorers may close over state that cannot be fingerprinted.
    # Cache scored winners only when the caller supplies a stable semantic key.
    cache_enabled = bool(cache_dir) and (scorer is None or bool(scorer_cache_key))
    if cache_enabled:
        os.makedirs(cache_dir, exist_ok=True)
        fingerprint = json.dumps(
            {
                "schema": _CACHE_SCHEMA,
                "prompt": prompt,
                "model": model,
                "provider": _provider_cache_identity(),
                "mode": "scored" if scorer is not None else "single",
                "n_candidates": n_candidates,
                "scorer": scorer_cache_key or "",
                "scoring_context": (
                    _scoring_context_fingerprint(store, vocab) if scorer is not None else ""
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        key = hashlib.sha256(fingerprint.encode()).hexdigest()
        cache_path = os.path.join(cache_dir, key + ".json")
        if os.path.exists(cache_path):
            cached_plan = _read_cached_plan(cache_path)
            if cached_plan is not None:
                return cached_plan
    if scorer is None:
        plan = _compile_uncached(prompt, model)
    else:
        # candidate 1 = greedy (t=0) anchor; the rest explore at high temperature.
        # A deterministic scorer picks the winner, the cache pins it.
        best, best_s = None, float("-inf")
        for k in range(n_candidates):
            try:
                cand = _compile_uncached(prompt, model, temperature=0.0 if k == 0 else 0.8)
            except RuntimeError:
                continue
            s = scorer(cand)
            # A scorer may signal an executor crash with -inf; a crashing plan
            # must never win selection nor be pinned by the cache.
            if s > best_s and s != float("-inf"):
                best, best_s = cand, s
        if best is None:
            # No candidate scored: retrieval can still recover through its
            # lexical channel, but the plan crosses the public Research API
            # boundary on the way there, and an empty query list is not a
            # valid QueryPlan — returning one fails schema normalization
            # before the lexical channel is ever reached.  ``(?!)`` is a valid
            # regular expression that can never match, so this plan is
            # schema-valid and inspectable, selects nothing symbolically, and
            # cannot degenerate into selecting the whole corpus.
            return {
                "queries": [{"select": "facts", "where": {"grep": "(?!)"}}],
                "intent": "factual",
            }  # nothing usable: do not cache
        plan = best
    if cache_path:
        _write_cached_plan(cache_path, plan)
    return plan


# ---------------------------------------------------------------------------
# Prompt context and model call


def render_compile_prompt(question: str, vocab, store, profile, *, today: str = "") -> str:
    """Render the complete context that determines a compiled plan."""
    units = sorted(store.units.values(), key=lambda u: (u.date, u.t, u.id))
    format_units = getattr(profile, "compile_unit_index", None)
    unit_index = (
        format_units(units) if format_units else "; ".join(f"{u.id}={u.date}" for u in units)
    )
    today = today or max((u.date for u in units), default="")
    format_facets = getattr(profile, "compile_facet_index", None)
    format_labels = getattr(profile, "compile_label_index", None)
    if format_labels:
        tree, entities_text = format_labels(question, vocab, store)
    else:
        tree = vocab.render_tree(max_lines=200)
        entities = []
        for code, entity in sorted(vocab.entities.items()):
            aliases = f" (aka {', '.join(sorted(entity.aliases))})" if entity.aliases else ""
            entities.append(code + aliases)
        entities_text = ", ".join(entities[:120]) or "(none)"
    prompt = profile.COMPILE_PROMPT.format(
        tree=tree,
        entities=entities_text,
        kinds="|".join(profile.KINDS),
        units=unit_index,
        facets=format_facets(question, store) if format_facets else "",
        today=today,
        question=question,
    )
    if scope := getattr(profile, "alias_scope", None):
        prompt += (
            "Project alias metadata (data): "
            + json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
            + "\nWhen aliases refer to multiple projects, keep the search open unless the "
            "question independently establishes a narrower scope.\n"
        )
    return prompt


def _compile_uncached(prompt: str, model: str, temperature: float = 0.0) -> dict:
    return _validated_compiled_plan(llm_json(prompt, model=model, temperature=temperature))
