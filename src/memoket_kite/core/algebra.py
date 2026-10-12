"""Core symbolic query algebra over a Codebook store.

Execution model: posting lists + candidate-local time filtering. The XML tree
is serialization; queries never walk it linearly. Pipeline per subquery:

  prune(time) -> match(code closure / postings) -> filter -> rank -> refine -> pack

Everything here is deterministic and traced (fan-out, refine steps, timing).
The trace records the symbolic decision procedure for method-level evaluation.
"""

from __future__ import annotations

import math
import re
import time as _time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date as _date

from memoket_kite.core.vocab import Vocab, norm_code

CONF_ORDER = {"low": 0, "med": 1, "high": 2, "": 1}


def _tokens(text: str) -> list[str]:
    """Language-agnostic tokens: lowered word stems (first 6 chars) + CJK bigrams."""
    out = [w[:6] for w in re.findall(r"[a-zA-Z][a-zA-Z']{2,}", text.lower())]
    for run in re.findall(r"[一-鿿]{2,}", text):
        out += [run[i : i + 2] for i in range(len(run) - 1)]
    return out


@dataclass
class FactRecord:
    """Mutable storage record used by the symbolic index."""

    id: str
    unit: str  # conv/session id
    unit_date: str
    t: str  # event time (may be "")
    kind: str
    who: str
    conf: str
    topics: tuple
    entities: tuple
    src: tuple
    text: str
    # Facets provide retrieval axes alongside the topic taxonomy.
    obj: tuple = ()  # object classes rather than named instances
    place: tuple = ()  # normalized location codes
    event: tuple = ()  # normalized event-type codes
    dur: str = ""  # normalized self-reported duration (ISO-8601)

    @property
    def when(self) -> str:
        return self.t[:10] if self.t else self.unit_date


@dataclass
class Line:
    id: str
    unit: str
    unit_date: str
    who: str
    text: str


@dataclass
class Unit:
    id: str
    date: str
    t: str
    title: str
    dur_min: int
    n_lines: int


def _valid_event_time(value: str) -> bool:
    """A Fact's event time is YYYY-MM or YYYY-MM-DD, and must be a real date."""
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            _date(int(value[:4]), int(value[5:7]), 1)
            return True
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            _date.fromisoformat(value)
            return True
    except ValueError:
        return False
    return False


class Store:
    """In-memory index over one codebook (or several shards)."""

    def __init__(self):
        self.facts: dict[str, FactRecord] = {}
        self.lines: dict[str, Line] = {}
        self.units: dict[str, Unit] = {}
        self.by_topic: dict[str, set] = {}
        self.by_entity: dict[str, set] = {}
        self.by_kind: dict[str, set] = {}
        self.by_who: dict[str, set] = {}
        self.by_obj: dict[str, set] = {}
        self.by_place: dict[str, set] = {}
        self.by_event: dict[str, set] = {}
        self.obj_of: dict[str, set] = {}  # entity code -> its class words
        self.by_unit: dict[str, set] = {}
        self.lines_by_unit: dict[str, list] = {}
        # A Unit id identifies the whole serialized session, not merely its
        # header.  Keep the parsed children so loading the same shard twice is
        # idempotent while a different shard cannot silently append records to
        # an existing Unit with a coincidentally identical header.
        self._unit_records: dict[str, tuple[Unit, tuple[FactRecord, ...], tuple[Line, ...]]] = {}
        self.speakers: set[str] = set()
        self.instances: dict[str, dict] = {}  # build-time aligned mentions
        self._df: dict[str, int] = {}
        self._ndocs: int = 0
        self._fact_terms: dict[str, frozenset[str]] = {}
        self._fact_specificity: dict[str, float] = {}
        # Token postings power the deterministic lexical channel without a
        # corpus scan. Keys are ("F", fact_id) / ("L", line_id).
        self.by_token: dict[str, set] = {}
        self._doc_tokens: dict[tuple, int] = {}
        self._total_tokens: int = 0
        self._scoring_fp_memo: tuple | None = None  # compile_plan memoization

    # ---------- deterministic lexical statistics ----------
    def _index_text(self, text: str, key: tuple | None = None):
        self._ndocs += 1
        toks = _tokens(text)
        for tok in set(toks):
            self._df[tok] = self._df.get(tok, 0) + 1
            if key is not None:
                self.by_token.setdefault(tok, set()).add(key)
        if key is not None:
            self._doc_tokens[key] = len(toks)
            self._total_tokens += len(toks)
        # Every added document changes IDF. Specificity is evaluated lazily, so
        # loading remains linear and only facts that reach a ranking pass pay it.
        self._fact_specificity.clear()

    def avg_doc_tokens(self) -> float:
        return self._total_tokens / max(1, len(self._doc_tokens))

    def idf(self, term: str) -> float:

        df = self._df.get(term.lower(), 0)
        return math.log(1 + (self._ndocs + 1) / (df + 1))

    def fact_specificity(self, fact_id: str) -> float:
        """Return an ID-independent, corpus-relative specificity tiebreaker."""
        cached = self._fact_specificity.get(fact_id)
        if cached is not None:
            return cached
        terms = self._fact_terms.get(fact_id)
        if terms is None:
            terms = frozenset(_tokens(self.facts[fact_id].text))
            self._fact_terms[fact_id] = terms
        score = sum(sorted((self.idf(term) for term in terms), reverse=True)[:3])
        self._fact_specificity[fact_id] = score
        return score

    # ---------- loading ----------
    @staticmethod
    def _merge_vocab(base: Vocab, incoming: Vocab) -> None:
        """Merge one shard's vocabulary without silently replacing symbols."""
        for code, topic in incoming.topics.items():
            existing = base.topics.get(code)
            if existing is not None and existing != topic:
                raise ValueError(f"conflicting Topic id: {code}")
        for code, entity in incoming.entities.items():
            existing = base.entities.get(code)
            if existing is not None and existing != entity:
                raise ValueError(f"conflicting Entity id: {code}")
        for surface, code in incoming._alias_t.items():
            existing = base._alias_t.get(surface)
            if existing is not None and existing != code:
                raise ValueError(f"conflicting Topic alias: {surface}")
        for surface, code in incoming._alias_e.items():
            existing = base._alias_e.get(surface)
            if existing is not None and existing != code:
                raise ValueError(f"conflicting Entity alias: {surface}")

        base.topics.update(incoming.topics)
        base.entities.update(incoming.entities)
        base._alias_t.update(incoming._alias_t)
        base._alias_e.update(incoming._alias_e)

    def _validate_references(self, vocab: Vocab, *, strict_symbols: bool = False) -> None:
        """Reject unsafe references after all shards have been merged.

        Topic and entity labels are posting keys as well as governed
        vocabulary symbols, so a label that ``<vocab>`` does not declare still
        indexes Facts and still answers queries; under
        ``strict_symbols=False`` it is accepted for that reason.
        ``strict_symbols=True`` additionally requires every referenced symbol
        to be declared and every event time to be a real date.  Provenance
        references are enforced in both modes: a Fact pointing at a source
        Line or a Fact mention that is not in the store would render a
        citation with nothing behind it.
        """
        for fact_id, fact in self.facts.items():
            if strict_symbols:
                if fact.t and not _valid_event_time(fact.t):
                    raise ValueError(f"Fact {fact_id} has an invalid event time: {fact.t!r}")
                for topic in fact.topics:
                    if topic not in vocab.topics:
                        raise ValueError(f"Fact {fact_id} references unknown Topic: {topic}")
                for entity in fact.entities:
                    if entity not in vocab.entities:
                        raise ValueError(f"Fact {fact_id} references unknown Entity: {entity}")
            for source_id in fact.src:
                if source_id not in self.lines:
                    raise ValueError(f"Fact {fact_id} references unknown source Line: {source_id}")
        for instance_id, instance in self.instances.items():
            for fact_id in instance["mentions"]:
                if fact_id not in self.facts:
                    raise ValueError(
                        f"Instance {instance_id} references unknown Fact mention: {fact_id}"
                    )

    @classmethod
    def load(cls, xml_paths: list[str], *, strict: bool = False) -> tuple["Store", Vocab]:
        """Load Codebook shards into a single queryable Store.

        ``strict`` governs taxonomy consistency only: with it off, a topic or
        entity label that ``<vocab>`` does not declare is still accepted as a
        posting key. Malformed records, IDs that two shards define
        differently, invalid unit dates, and dangling provenance are rejected
        in every mode.
        """
        st = cls()
        vocab = None
        for p in xml_paths:
            root = ET.parse(p).getroot()
            vel = root.find("vocab")
            v = Vocab.from_xml(vel, strict=strict) if vel is not None else Vocab()
            if vocab is None:
                vocab = v
            else:  # vocab is global; only structurally identical definitions are idempotent
                cls._merge_vocab(vocab, v)
            for name in (root.get("speakers") or "").split():
                st.speakers.add(name.lower())
                st.speakers.add(name.lower().split()[0] if " " in name else name.lower())
            for uel in root.find("timeline").iter():
                if uel.tag not in ("conv", "session"):
                    continue
                st.add_session_xml(uel)
            iel = root.find("instances")
            if iel is not None:
                for ie in iel.findall("instance"):
                    instance_id = ie.get("id")
                    if not instance_id or not instance_id.strip():
                        raise ValueError("Instance id must be non-empty")
                    instance = {
                        "id": instance_id,
                        "label": ie.get("label", ""),
                        "kind": ie.get("kind", ""),
                        "t": ie.get("t", ""),
                        "mentions": (ie.get("mentions") or "").split(),
                    }
                    existing_instance = st.instances.get(instance_id)
                    if existing_instance is not None and existing_instance != instance:
                        raise ValueError(f"conflicting Instance id: {instance_id}")
                    st.instances[instance_id] = instance
        merged_vocab = vocab or Vocab()
        if strict:
            problems = merged_vocab.check()
            if problems:
                raise ValueError("invalid merged vocabulary: " + "; ".join(problems))
        st._validate_references(merged_vocab, strict_symbols=strict)
        return st, merged_vocab

    def add_session_xml(self, uel: ET.Element):
        """Load one XML conversation session into the symbolic store."""
        uid = uel.get("id")
        if not uid or not uid.strip():
            raise ValueError("Unit id must be non-empty")
        date = uel.get("date", "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError(f"invalid Unit date: {date!r}")
        try:
            _date.fromisoformat(date)
        except ValueError as exc:
            raise ValueError(f"invalid Unit date: {date!r}") from exc
        dur = uel.get("dur", "0m")
        u = Unit(
            uid,
            date,
            uel.get("t", ""),
            uel.get("title", ""),
            int(re.sub(r"\D", "", dur) or 0),
            len(uel.findall("line")),
        )
        facts = []
        for fe in uel.findall("fact"):
            fact_id = fe.get("id")
            if not fact_id or not fact_id.strip():
                raise ValueError("Fact id must be non-empty")
            facts.append(
                FactRecord(
                    id=fact_id,
                    unit=uid,
                    unit_date=date,
                    t=fe.get("t", ""),
                    kind=fe.get("kind", ""),
                    who=(fe.get("who") or "").lower(),
                    conf=fe.get("conf", ""),
                    topics=tuple((fe.get("topics") or "").split()),
                    entities=tuple((fe.get("entities") or "").split()),
                    src=tuple((fe.get("src") or "").split()),
                    text=fe.text or "",
                    # Facet attributes are optional; an absent one reads back
                    # as an empty tuple rather than failing the load.
                    obj=tuple((fe.get("obj") or "").split()),
                    place=tuple((fe.get("place") or "").split()),
                    event=tuple((fe.get("event") or "").split()),
                    dur=fe.get("dur", ""),
                )
            )
        lines = []
        for line_element in uel.findall("line"):
            line_id = line_element.get("id")
            if not line_id or not line_id.strip():
                raise ValueError("Line id must be non-empty")
            lines.append(
                Line(
                    line_id,
                    uid,
                    date,
                    (line_element.get("who") or "").lower(),
                    line_element.text or "",
                )
            )

        records = (u, tuple(facts), tuple(lines))
        existing_records = self._unit_records.get(uid)
        if existing_records is not None:
            if existing_records != records:
                raise ValueError(f"conflicting Unit id: {uid}")
            return
        self._unit_records[uid] = records
        self.units[uid] = u

        for fact in facts:
            if self.add_fact(fact):
                self._fact_terms[fact.id] = frozenset(_tokens(fact.text))
                self._index_text(fact.text, key=("F", fact.id))
        for line in lines:
            existing_line = self.lines.get(line.id)
            if existing_line is not None:
                if existing_line != line:
                    raise ValueError(f"conflicting Line id: {line.id}")
                continue
            self.lines[line.id] = line
            self.lines_by_unit.setdefault(uid, []).append(line.id)
            self._index_text(line.text, key=("L", line.id))

    def add_fact(self, f: FactRecord) -> bool:
        """Index a Fact, treating exact shard duplicates as an idempotent no-op."""
        existing = self.facts.get(f.id)
        if existing is not None:
            if existing != f:
                raise ValueError(f"conflicting Fact id: {f.id}")
            return False
        self._scoring_fp_memo = None  # plan-scoring fingerprint is now stale
        self.facts[f.id] = f
        for c in f.topics:
            self.by_topic.setdefault(c, set()).add(f.id)
        for c in f.entities:
            self.by_entity.setdefault(c, set()).add(f.id)
        self.by_kind.setdefault(f.kind, set()).add(f.id)
        if f.who:
            self.by_who.setdefault(f.who, set()).add(f.id)
        self.by_unit.setdefault(f.unit, set()).add(f.id)
        for c in f.obj:
            self.by_obj.setdefault(c, set()).add(f.id)
        for c in f.place:
            self.by_place.setdefault(c, set()).add(f.id)
        for c in f.event:
            self.by_event.setdefault(c, set()).add(f.id)
        # Record object classes associated with an entity in the same Fact.
        # This supports entity-to-class retrieval without merging identities.
        if f.obj:
            for e in f.entities:
                self.obj_of.setdefault(e, set()).update(f.obj)
        return True

    def apply_rewrite(self, topic_map: dict[str, str] = None, entity_map: dict[str, str] = None):
        """Posting + fact attribute rewrite after vocab merge ops."""
        self._scoring_fp_memo = None  # plan-scoring fingerprint is now stale
        topic_map, entity_map = topic_map or {}, entity_map or {}
        for old, new in topic_map.items():
            ids = self.by_topic.pop(old, set())
            self.by_topic.setdefault(new, set()).update(ids)
            for fid in ids:
                f = self.facts[fid]
                f.topics = tuple(new if c == old else c for c in f.topics)
        for old, new in entity_map.items():
            ids = self.by_entity.pop(old, set())
            self.by_entity.setdefault(new, set()).update(ids)
            for fid in ids:
                f = self.facts[fid]
                f.entities = tuple(new if c == old else c for c in f.entities)
            # Entity-to-object bridges are an index over the entity codes stored
            # on Facts, so they must follow the same merge rewrite.
            object_classes = self.obj_of.pop(old, set())
            if object_classes:
                self.obj_of.setdefault(new, set()).update(object_classes)


# ---------------- plans ----------------

# Facets are extracted labels, so they relax before structural filters such as
# speaker, entity, and time.
RELAX_ORDER = [
    "grep",
    "event",
    "obj",
    "kind",
    "place",
    "who",
    "entities",
    "time",
    "topics",
]  # weakest first


@dataclass
class Where:
    time: tuple = ("", "")  # (lo, hi) ISO prefixes, "" = unbounded
    topics: tuple = ()  # ((code, closure_bool), ...)
    entities: tuple = ()
    obj: tuple = ()
    place: tuple = ()
    event: tuple = ()
    kind: tuple = ()
    who: tuple = ()
    units: tuple = ()  # restrict to these unit ids (multi-hop binding)
    grep: str = ""
    grep_all: tuple = ()  # every pattern must match
    entity_kind: str = ""  # require an entity of this type
    conf_min: str = "low"


@dataclass
class SubQuery:
    select: str = "facts"  # facts | lines | units
    where: Where = field(default_factory=Where)
    pipe: tuple = ()  # (("sort","t",desc), ("head",n), ("count",))


def parse_plan(obj: dict, vocab: Vocab, speakers: set | None = None) -> list[SubQuery]:
    """Validate + resolve a compile-produced plan JSON against the vocabulary.
    Entity atoms that are actually conversation speakers are converted to `who`
    filters — speaker semantics must never ride the entity channel."""
    speakers = speakers or set()
    subs = []
    for q in (obj.get("queries") or [])[:3]:
        if not isinstance(q, dict):
            continue  # malformed compile output must degrade, not crash
        w = q.get("where") or {}
        if not isinstance(w, dict):
            w = {}
        who_extra = []
        topics = []
        for t in (w.get("topics") or [])[:4]:
            if isinstance(t, str):
                t = {"code": t, "closure": True}
            code = vocab.resolve_topic(t.get("code", ""))
            if code:
                topics.append((code, bool(t.get("closure", True))))
        entities = []
        for e in (w.get("entities") or [])[:4]:
            if str(e).lower() in speakers:
                who_extra.append(str(e).lower())
                continue
            code = vocab.resolve_entity(e)
            if code and code in speakers:
                who_extra.append(code)
            elif code:
                entities.append(code)
        # compile sometimes emits a single-endpoint time (["2023-05"]) or a
        # bare string — normalize to two endpoints instead of crashing
        tm = w.get("time") or ["", ""]
        if isinstance(tm, str):
            tm = [tm]
        tm = (list(tm) + ["", ""])[:2]
        uv = w.get("units") or w.get("unit") or []
        if isinstance(uv, str):
            uv = [uv]
        units = tuple(str(u) for u in uv if u)[:8]
        pipe = []
        for st in (q.get("pipe") or [])[:4]:
            op = st.get("op")
            if op == "sort":
                pipe.append(("sort", st.get("key", "t"), bool(st.get("desc"))))
            elif op in ("head", "tail"):
                pipe.append((op, int(st.get("n", 5))))
            elif op == "count":
                pipe.append(("count",))
        subs.append(
            SubQuery(
                select=q.get("select", "facts"),
                where=Where(
                    time=(str(tm[0] or ""), str(tm[1] or "")),
                    topics=tuple(topics),
                    entities=tuple(entities),
                    obj=tuple(norm_code(x) for x in (w.get("obj") or [])[:3] if norm_code(x)),
                    place=tuple(norm_code(x) for x in (w.get("place") or [])[:3] if norm_code(x)),
                    event=tuple(norm_code(x) for x in (w.get("event") or [])[:3] if norm_code(x)),
                    kind=tuple(norm_code(k) for k in (w.get("kind") or [])[:4]),
                    who=tuple(
                        dict.fromkeys(
                            [str(x).lower() for x in (w.get("who") or [])[:3]] + who_extra
                        )
                    ),
                    units=units,
                    grep=str(w.get("grep") or ""),
                    grep_all=tuple(str(g) for g in (w.get("grep_all") or [])[:3]),
                    entity_kind=str(w.get("entity_kind") or ""),
                    conf_min=w.get("conf_min", "low"),
                ),
                pipe=tuple(pipe),
            )
        )
    return subs


# ---------------- execution ----------------


@dataclass
class Trace:
    stages: list = field(default_factory=list)

    def log(self, stage: str, **kw):
        self.stages.append({"stage": stage, **kw})


def _has_content_constraints(w: Where) -> bool:
    """Whether a query still has a semantic anchor beyond speaker or time."""
    return any(
        (
            w.topics,
            w.entities,
            w.obj,
            w.place,
            w.event,
            w.kind,
            w.units,
            w.grep,
            w.grep_all,
            w.entity_kind,
        )
    )


def _time_ok(when: str, lo: str, hi: str) -> bool:
    if not when:
        return not lo and not hi
    return (not lo or when >= lo[: len(when)] or when[: len(lo)] >= lo) and (
        not hi or when <= hi + "~"
    )


def _entity_targets(vocab: Vocab, entity: str) -> set[str]:
    registered = vocab.entities.get(entity)
    return {entity} | (set(registered.aliases) if registered is not None else set())


def _has_entity_postings(store: Store, vocab: Vocab, entity: str) -> bool:
    """Check entity reachability without materializing its full posting union."""
    return any(store.by_entity.get(code) for code in _entity_targets(vocab, entity)) or any(
        store.by_obj.get(object_class) for object_class in store.obj_of.get(entity, ())
    )


def _entity_postings(
    store: Store, vocab: Vocab, entity: str, trace: Trace | None = None
) -> set[str]:
    """Return direct, alias, and object-class bridge postings for one entity."""
    ids = set()
    for code in _entity_targets(vocab, entity):
        ids.update(store.by_entity.get(code, ()))
    for object_class in store.obj_of.get(entity, ()):
        postings = store.by_obj.get(object_class, set())
        if not postings:
            continue
        ids.update(postings)
        if trace is not None:
            trace.log(
                "expand",
                atom="obj_bridge",
                ent=entity,
                cls=object_class,
                hits=len(postings),
            )
    return ids


def _match_facts(
    store: Store,
    vocab: Vocab,
    w: Where,
    trace: Trace,
    closure_depth: int = 3,
    legal_kinds: set[str] | None = None,
) -> set[str]:
    # ``grep``/``grep_all`` are hard filters applied after the posting-list
    # intersection; ``_grep_score`` contributes to ranking only.
    cand: set[str] | None = None

    def isect(ids: set[str]):
        nonlocal cand
        cand = ids if cand is None else cand & ids

    if w.topics:
        ids = set()
        fanout = 0
        for code, closure in w.topics:
            codes = vocab.downset(
                code, max_depth=closure_depth if closure else 0, include_candidates=True
            ) or {code}
            fanout += len(codes)
            for c in codes:
                ids |= store.by_topic.get(c, set())
        trace.log("expand", atom="topics", fanout=fanout, hits=len(ids))
        isect(ids)
    for attr, index in (("obj", "by_obj"), ("place", "by_place"), ("event", "by_event")):
        vals = getattr(w, attr, ())
        if not vals:
            continue
        idx = getattr(store, index, {}) or {}
        ids = set()
        for c in vals:
            ids |= idx.get(c, set())
        trace.log("expand", atom=attr, hits=len(ids))
        isect(ids)
    if w.entities:
        ids = set()
        for e in w.entities:
            # An entity may also be retrieved through its extracted object class.
            # The union still participates in the query's remaining intersections.
            ids.update(_entity_postings(store, vocab, e, trace))
        trace.log("expand", atom="entities", hits=len(ids))
        isect(ids)
    if w.kind:
        ids = set()
        for k in w.kind:
            ids |= store.by_kind.get(k, set())
        # Facts with an out-of-profile kind remain reachable because the query
        # compiler cannot express those labels.
        if legal_kinds:
            for k, fids in store.by_kind.items():
                if k not in legal_kinds:
                    ids |= fids
        isect(ids)
    if w.who:
        ids = set()
        for s in w.who:
            ids |= (
                store.by_who.get(s, set())
                | store.by_who.get("both", set())
                | store.by_who.get("multi", set())
            )
        isect(ids)
    if w.units:
        ids = set()
        for u in w.units:
            ids |= store.by_unit.get(u, set())
        isect(ids)
    if cand is None:
        cand = set(store.facts)
    lo, hi = w.time
    if lo or hi:
        cand = {i for i in cand if _time_ok(store.facts[i].when, lo, hi)}
    if w.grep:
        try:
            rx = re.compile(w.grep, re.I)
            cand = {i for i in cand if rx.search(store.facts[i].text)}
        except re.error:
            pass
    for g in w.grep_all or ():
        # Every pattern in grep_all must independently match.
        try:
            rx = re.compile(g, re.I)
            cand = {i for i in cand if rx.search(store.facts[i].text)}
        except re.error:
            pass
    if w.entity_kind:
        ek = w.entity_kind.lower()

        def _typed(i):
            f = store.facts[i]
            if any(
                vocab.entities.get(e) is not None and ek in (vocab.entities[e].etype or "").lower()
                for e in f.entities
            ):
                return True
            # Place facets provide a fallback when an entity lacks a usable type.
            return ek == "place" and bool(f.place)

        cand = {i for i in cand if _typed(i)}
    cmin = CONF_ORDER.get(w.conf_min, 0)
    cand = {i for i in cand if CONF_ORDER.get(store.facts[i].conf, 1) >= cmin}
    return cand


def _grep_score(store: Store, pattern: str, text: str) -> float:
    """Score simple grep alternatives by corpus IDF.

    Complex regular expressions receive a flat content-match score because
    their individual semantic terms cannot be separated reliably.
    """
    try:
        if not re.search(pattern, text, re.I):
            return 0.0
    except re.error:
        return 0.0
    if re.search(r"[()\[\]?*+{\\]", pattern.replace("\\|", "")):
        return 2.5  # non-trivial regex: flat fallback
    s = 0.0
    for term in pattern.split("|"):
        term = term.strip()
        if term and re.search(re.escape(term), text, re.I):
            s += store.idf(term.lower()[:6])
    return min(6.0, 0.9 * s) if s else 2.5


def _score(
    store: Store,
    vocab: Vocab,
    w: Where,
    fid: str,
    topic_closures: dict[str, set[str]] | None = None,
) -> float:
    f = store.facts[fid]
    s = 0.0
    for code, closure in w.topics:
        if code in f.topics:
            s += 3.0  # exact code match
        elif closure:
            if topic_closures is None:
                closure_codes = vocab.downset(code, include_candidates=True)
            else:
                closure_codes = topic_closures.get(code)
                if closure_codes is None:
                    closure_codes = vocab.downset(code, include_candidates=True)
                    topic_closures[code] = closure_codes
            hit = set(f.topics) & closure_codes
            if not hit:
                continue
            # Specific descendants carry more information than root-level hits.
            deep = any(vocab.topics.get(c) and vocab.topics[c].parents for c in hit)
            s += 2.0 if deep else 0.8
    for e in w.entities:
        if e in f.entities:
            s += 2.0
    for attr in ("obj", "place", "event"):
        hit = set(getattr(w, attr, ())) & set(getattr(f, attr, ()))
        if hit:
            s += 1.5
    if w.entity_kind:
        ek = w.entity_kind.lower()
        best = 0.0
        for e in f.entities:
            eo = vocab.entities.get(e)
            if eo is None or ek not in (eo.etype or "").lower():
                continue
            # Rare typed entities contribute more ranking information.
            n_post = len(store.by_entity.get(e, ()))
            best = max(best, min(3.5, 6.0 / (1 + math.log2(1 + n_post))))
        s += best
    if w.kind and f.kind in w.kind:
        s += 1.0
    if w.who and (f.who in w.who or f.who in ("both", "multi")):
        s += 0.5
    if w.grep:
        s += _grep_score(store, w.grep, f.text)
    for g in getattr(w, "grep_all", ()) or ():
        s += _grep_score(store, g, f.text)
    s += 0.2 * CONF_ORDER.get(f.conf, 1)
    return s


def _normalized_rank_text(text: str) -> str:
    """Canonicalize content for deterministic semantic ordering."""
    return " ".join(text.lower().split())


def _canonical_fact_key(fact: FactRecord) -> tuple:
    """Stable evidence identity that does not depend on a generated Fact ID."""
    return (
        _normalized_rank_text(fact.text),
        fact.kind,
        fact.who,
        tuple(sorted(fact.topics)),
        tuple(sorted(fact.entities)),
        tuple(sorted(fact.obj)),
        tuple(sorted(fact.place)),
        tuple(sorted(fact.event)),
        fact.dur,
        fact.t,
        fact.unit,
        tuple(sorted(fact.src)),
    )


def _relax(w: Where, step: str) -> Where:
    # `units` is never relaxed: a bound anchor is the query's raison d'être
    kw = dict(
        time=w.time,
        topics=w.topics,
        entities=w.entities,
        kind=w.kind,
        who=w.who,
        units=w.units,
        grep=w.grep,
        grep_all=w.grep_all,
        entity_kind=w.entity_kind,
        conf_min=w.conf_min,
        obj=w.obj,
        place=w.place,
        event=w.event,
    )
    if step == "grep":
        kw["grep"] = ""
        kw["grep_all"] = ()
    elif step in ("kind", "who", "entities", "obj", "place", "event"):
        kw[step] = ()
    elif step == "time":
        kw["time"] = ("", "")
    elif step == "topics":
        kw["topics"] = tuple((c, True) for c, _ in w.topics)  # force closure on
    return Where(**kw)


def execute(
    store: Store,
    vocab: Vocab,
    subs: list[SubQuery],
    budget: int = 12,
    unit_cap: int = 4,
    legal_kinds: set[str] | None = None,
    pack_v2: bool = False,
    coverage: bool = False,
) -> tuple[list[dict], Trace]:
    """Run subqueries, pool, rank globally, refine, pack with diversity caps."""
    trace = Trace()
    t0 = _time.time()
    pooled: dict[str, float] = {}
    aggregates: list[dict] = []
    subquery_top: list[list[str]] = []  # pack_v2: each subquery's best keys

    for qi, sq in enumerate(subs):
        if sq.select == "units":
            rows = [
                u
                for u in store.units.values()
                if _time_ok(u.date, *sq.where.time)
                and (not sq.where.units or u.id in sq.where.units)
            ]
            if sq.where.grep:
                try:
                    rx = re.compile(sq.where.grep, re.I)
                    branches = _grep_branches(sq.where.grep, rx)
                    scored_u = []
                    for u in rows:
                        hits = sum(
                            1 for f in store.by_unit.get(u.id, ()) if rx.search(store.facts[f].text)
                        )
                        # The title IS the unit's identity, so count DISTINCT
                        # matching terms: a title carrying two of the query's
                        # terms outranks one carrying either alone.
                        title_hit = 10 * sum(1 for b in branches if b.search(u.title))
                        if hits or title_hit:
                            scored_u.append((-(title_hit + min(hits, 8)), u.date, u))
                    # default order = relevance (title weighted), date tiebreak;
                    # an explicit sort pipe overrides this
                    rows = [u for _, _, u in sorted(scored_u, key=lambda x: (x[0], x[1]))]
                except re.error:
                    rows.sort(key=lambda u: u.date)
            else:
                rows.sort(key=lambda u: u.date)
            rows = _apply_pipe_units(rows, sq.pipe, aggregates)
            for u in rows:
                pooled[f"U::{u.id}"] = max(pooled.get(f"U::{u.id}", 0), 1.0)
            continue
        if sq.select == "lines":
            w = sq.where
            if w.units:
                # Bound stages only need lines from their selected sessions.
                ids = {i for unit in w.units for i in store.lines_by_unit.get(unit, ())}
            else:
                ids = set(store.lines)
            if w.who:
                ids = {i for i in ids if store.lines[i].who in w.who}
            lo, hi = w.time
            if lo or hi:
                ids = {i for i in ids if _time_ok(store.lines[i].unit_date, lo, hi)}
            if w.grep:
                try:
                    rx = re.compile(w.grep, re.I)
                    ids = {i for i in ids if rx.search(store.lines[i].text)}
                except re.error:
                    ids = set()
            trace.log("lines", q=qi, hits=len(ids))
            for i in ids:
                pooled[f"L::{i}"] = max(pooled.get(f"L::{i}", 0), 1.0)
            continue

        # facts with deterministic refine loop
        w = sq.where
        # Speaker and time constrain a semantic request but do not define one.
        had_content = _has_content_constraints(w)
        # Remove filters with no postings before relaxing filters that do carry
        # information in this Codebook.
        live_t = tuple(
            t
            for t in w.topics
            if any(
                store.by_topic.get(c)
                for c in vocab.downset(t[0], max_depth=3, max_width=60, include_candidates=True)
            )
        )
        live_k = tuple(k for k in w.kind if store.by_kind.get(k))
        live_e = tuple(e for e in w.entities if _has_entity_postings(store, vocab, e))
        live_obj = tuple(code for code in w.obj if store.by_obj.get(code))
        live_place = tuple(code for code in w.place if store.by_place.get(code))
        live_event = tuple(code for code in w.event if store.by_event.get(code))
        pruned = any(
            (
                len(live_t) < len(w.topics),
                len(live_k) < len(w.kind),
                len(live_e) < len(w.entities),
                len(live_obj) < len(w.obj),
                len(live_place) < len(w.place),
                len(live_event) < len(w.event),
            )
        )
        if pruned:
            # Name only the posting-backed fields here. ``replace`` copies
            # everything else, which is what keeps grep, grep_all, time, who,
            # units, and entity_kind — constraints no posting list can vouch
            # for — alive through dead-value pruning.
            w = replace(
                w,
                topics=live_t,
                entities=live_e,
                kind=live_k,
                obj=live_obj,
                place=live_place,
                event=live_event,
            )
            if had_content and not _has_content_constraints(w):
                trace.log("refine", q=qi, steps=["prune(all-content-dead)"], hits=0)
                continue  # every content value was dead: abstain
        cand = _match_facts(store, vocab, w, trace, legal_kinds=legal_kinds)
        relax_steps = ["prune(dead-values)"] if pruned else []
        ri = 0
        while not cand and ri < len(RELAX_ORDER):
            step = RELAX_ORDER[ri]
            ri += 1
            w2 = _relax(w, step)
            if w2 == w:
                continue
            w = w2
            relax_steps.append(step)
            if not _has_content_constraints(w) and not w.who and w.time == ("", ""):
                break  # fully unconstrained: stop, don't dump corpus
            # If every semantic filter has been relaxed away, abstain instead
            # of turning the remaining speaker/time filters into a corpus dump.
            if had_content and not _has_content_constraints(w):
                relax_steps.append("abstain(content-lost)")
                cand = set()
                break
            cand = _match_facts(store, vocab, w, trace, legal_kinds=legal_kinds)
        # specialize: shrink closure depth while wildly over budget
        depth = 3
        while len(cand) > budget * 6 and depth > 0 and w.topics:
            depth -= 1
            shrunk = _match_facts(
                store,
                vocab,
                w,
                trace,
                closure_depth=depth,
                legal_kinds=legal_kinds,
            )
            if pack_v2 and len(shrunk) < budget:
                # Monotonic guard: a shrink step that undershoots the pack
                # budget drops whole subtrees; keep the pre-shrink set and let
                # ranking decide instead.
                relax_steps.append(f"specialize(stop@depth={depth})")
                break
            cand = shrunk
            relax_steps.append(f"specialize(depth={depth})")
        trace.log("refine", q=qi, steps=relax_steps, hits=len(cand))
        # A closure is a property of the query, not of each candidate. Populate
        # this cache lazily so exact-only and empty candidate sets do no taxonomy
        # traversal during ranking.
        topic_closures: dict[str, set[str]] = {}

        scores = {fid: _score(store, vocab, w, fid, topic_closures) for fid in cand}
        truncates = any(step[0] in ("head", "tail") for step in sq.pipe)
        if truncates:
            # head/tail drop rows here, so what survives must be decided by
            # content: after score and event time, break ties on specificity
            # and a canonical content key, and only then on the generated Fact
            # ID. Without a truncating pipe the whole candidate set reaches
            # the global ranker anyway, so score/time/ID ordering suffices.
            scored = sorted(
                cand,
                key=lambda i: (
                    -scores[i],
                    store.facts[i].when,
                    -store.fact_specificity(i),
                    _canonical_fact_key(store.facts[i]),
                    i,
                ),
            )
        else:
            scored = sorted(
                cand,
                key=lambda i: (-scores[i], store.facts[i].when, i),
            )
        scored = _apply_pipe_facts(store, scored, sq.pipe, aggregates)
        if pack_v2 and scored:
            subquery_top.append([f"F::{fid}" for fid in scored[:2]])
        for rank, fid in enumerate(scored):
            sc = scores[fid] - rank * 1e-4
            key = f"F::{fid}"
            pooled[key] = max(pooled.get(key, 0), sc)

    # global pack with per-unit diversity caps
    order = sorted(pooled, key=lambda k: (-pooled[k], k))
    pack, per_unit = [], {}
    if pack_v2:
        packed: set[str] = set()

        def _key_unit(key: str) -> tuple[str, str, str]:
            typ, _, ident = key.partition("::")
            unit = (
                store.facts[ident].unit
                if typ == "F"
                else store.lines[ident].unit
                if typ == "L"
                else ident
            )
            return typ, ident, unit

        def try_pack(key: str, respect_cap: bool = True) -> None:
            if key in packed or len(pack) >= budget:
                return
            typ, ident, unit = _key_unit(key)
            if respect_cap and per_unit.get(unit, 0) >= unit_cap and typ != "U":
                return
            per_unit[unit] = per_unit.get(unit, 0) + 1
            packed.add(key)
            pack.append(_row(store, typ, ident, pooled[key]))

        if coverage:
            # Enumeration: every matching top-level taxonomy branch gets a seat
            # before global score order fills the remainder. A fact's branch is
            # its ancestor directly below a taxonomy root (or the topic itself).
            def _branch_of(code: str) -> str:
                seen: set[str] = set()
                current = code
                while current in vocab.topics:
                    parents = sorted(p for p in vocab.topics[current].parents if p in vocab.topics)
                    if not parents:  # current IS a root
                        return current
                    if any(not vocab.topics[p].parents for p in parents):
                        return current  # direct child of a root
                    nxt = parents[0]
                    if nxt in seen:
                        return current
                    seen.add(nxt)
                    current = nxt
                return code

            groups: dict[str, list[str]] = {}
            for key in order:
                typ, ident, _unit = _key_unit(key)
                branch = ""
                if typ == "F":
                    f = store.facts[ident]
                    if f.topics:
                        branch = _branch_of(f.topics[0])
                groups.setdefault(branch, []).append(key)
            lanes = [groups[b] for b in sorted(groups)]
            while any(lanes) and len(pack) < budget:
                for lane in lanes:
                    if lane:
                        try_pack(lane.pop(0))
        else:
            # Parallel subqueries are complementary by design: reserve each
            # one's best rows before the global pool competes for the rest.
            for keys in subquery_top:
                for key in keys:
                    try_pack(key)
        for key in order:
            if len(pack) >= budget:
                break
            try_pack(key)
        if len(pack) < budget:
            # Relief pass: every key left over was refused by the per-unit
            # cap, so fill the remaining budget while ignoring the cap rather
            # than returning a short pack.
            for key in order:
                if len(pack) >= budget:
                    break
                try_pack(key, respect_cap=False)
    else:
        for key in order:
            typ, _, ident = key.partition("::")
            unit = (
                store.facts[ident].unit
                if typ == "F"
                else store.lines[ident].unit
                if typ == "L"
                else ident
            )
            if per_unit.get(unit, 0) >= unit_cap and typ != "U":
                continue
            per_unit[unit] = per_unit.get(unit, 0) + 1
            pack.append(_row(store, typ, ident, pooled[key]))
            if len(pack) >= budget:
                break
    pack.sort(key=lambda r: (r.get("date") or "", r.get("id") or ""))
    trace.log("pack", n=len(pack), units=len(per_unit), ms=round(1000 * (_time.time() - t0), 1))
    return (aggregates + pack if aggregates else pack), trace


def _apply_pipe_facts(store, ids: list[str], pipe, aggregates) -> list[str]:
    for st in pipe:
        if st[0] == "sort":
            key, desc = st[1], st[2]
            if key in ("t", "date", "when"):
                ids = sorted(ids, key=lambda i: store.facts[i].when, reverse=desc)
            elif key == "dur":
                ids = sorted(
                    ids, key=lambda i: store.units[store.facts[i].unit].dur_min, reverse=desc
                )
        elif st[0] == "head":
            ids = ids[: st[1]]
        elif st[0] == "tail":
            ids = ids[-st[1] :]
        elif st[0] == "count":
            # counting FACT ROWS = mention count, NOT real-world instances —
            # mark provenance so the answer layer never short-circuits it.
            # members carry the full counted basis (capped) so the answer
            # layer can render an enumerable list instead of a bare number.
            aggregates.append(
                {
                    "type": "count",
                    "n": len(ids),
                    "of": "facts",
                    "text": f"count = {len(ids)} matching FACT "
                    f"ROWS (mentions, not deduplicated "
                    f"instances)",
                    "members": [
                        {
                            "id": i,
                            "date": store.facts[i].when,
                            "text": store.facts[i].text,
                        }
                        for i in ids[:40]
                    ],
                }
            )
    return ids


def _apply_pipe_units(units: list[Unit], pipe, aggregates) -> list[Unit]:
    for st in pipe:
        if st[0] == "sort":
            key, desc = st[1], st[2]
            if key == "dur":
                units = sorted(units, key=lambda u: u.dur_min, reverse=desc)
            else:
                units = sorted(units, key=lambda u: u.date, reverse=desc)
        elif st[0] == "head":
            units = units[: st[1]]
        elif st[0] == "tail":
            units = units[-st[1] :]
        elif st[0] == "count":
            aggregates.append(
                {"type": "count", "n": len(units), "of": "units", "text": f"count = {len(units)}"}
            )
    return units


# ---------------- staged (multi-hop dependency) plans ----------------


# Whitelist of substitutable binding fields. `unit`, `units`, and `date` are
# the references a plan may declare; `t` is the event-time spelling of `date`
# that some plans use. `_stage_binding` also exposes `id` and `n`, which are
# deliberately absent here so a stage cannot substitute on them; any other
# `$name.field` falls through `_subst_refs` as a literal string.
_STAGE_REF = re.compile(r"^\$([^.\s]+)\.(unit|units|date|t)$")


def _grep_branches(pattern: str, whole) -> list:
    """Top-level alternatives of `pattern`, for distinct-term title weighting.

    A `|` inside a group or a character class is not an alternation boundary,
    so the scan tracks parenthesis depth, bracket depth, and escapes instead
    of splitting on every `|`; splitting blindly yields fragments that do not
    compile. A fragment that still fails to compile means the pattern is not
    decomposable, so weighting falls back to `whole`, the already-compiled
    pattern, and the caller keeps a working filter.
    """
    depth = bracket = 0
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in pattern:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif not bracket and char == "(":
            depth += 1
        elif not bracket and char == ")" and depth:
            depth -= 1
        elif char == "|" and not depth and not bracket:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    compiled = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            compiled.append(re.compile(part, re.I))
        except re.error:
            return [whole]
    return compiled or [whole]


def _subst_refs(obj, bindings: dict):
    """Resolve `$stage.field` references against earlier stage bindings.
    Returns (value, ok); ok=False when a reference cannot be resolved."""
    if isinstance(obj, str) and obj.startswith("$"):
        match = _STAGE_REF.fullmatch(obj)
        if match is None:
            # A leading "$" alone does not make a reference: a grep pattern
            # like "$" and a value like "$2,500" start with it too. Requiring
            # the full `$name.field` shape keeps them literal instead of
            # failing to resolve and skipping the stage.
            return obj, True
        name, field = match.groups()
        b = bindings.get(name)
        if b is None and len(bindings) == 1:
            # forgiving resolution: LLMs mix up stage names; with a single
            # prior stage the intent is unambiguous
            b = next(iter(bindings.values()))
        if not b or field not in b or b[field] in (None, "", []):
            return None, False
        return b[field], True
    if isinstance(obj, list):
        out = []
        for x in obj:
            v, ok = _subst_refs(x, bindings)
            if not ok:
                return None, False
            out.extend(v if isinstance(v, list) else [v])
        return out, True
    if isinstance(obj, dict):
        out = {}
        for k, x in obj.items():
            v, ok = _subst_refs(x, bindings)
            if not ok:
                return None, False
            out[k] = v
        return out, True
    return obj, True


def _has_stage_reference(obj) -> bool:
    """Whether this stage body actually references an earlier stage.

    Each string is matched in full against the reference pattern rather than
    searched for a "$": a regex such as grep "anchor$" contains the character
    while binding nothing, and counting it as bound would hand its rows the
    priority `execute_plan` reserves for genuinely anchored stages, displacing
    them once the budget is tight.
    """
    if isinstance(obj, str):
        return _STAGE_REF.fullmatch(obj) is not None
    if isinstance(obj, list):
        return any(_has_stage_reference(item) for item in obj)
    if isinstance(obj, dict):
        return any(_has_stage_reference(value) for value in obj.values())
    return False


def _stage_binding(rows: list[dict]) -> dict:
    """Expose a stage's result to later stages: top row's id/unit/date + all units."""
    data_rows = [r for r in rows if r.get("type") != "count"]
    units = []
    for r in data_rows:
        u = r.get("unit", r.get("id"))
        if u and u not in units:
            units.append(u)
    top = data_rows[0] if data_rows else {}
    counts = [r for r in rows if r.get("type") == "count"]
    return {
        "id": top.get("id", ""),
        "unit": top.get("unit", top.get("id", "")),
        "units": units,
        "date": top.get("date", ""),
        "t": top.get("date", ""),
        "n": counts[0]["n"] if counts else len(data_rows),
    }


def execute_plan(
    store: Store,
    vocab: Vocab,
    plan_obj: dict,
    speakers: set | None = None,
    budget: int = 12,
    unit_cap: int = 4,
    legal_kinds: set[str] | None = None,
    pack_v2: bool = False,
    coverage: bool = False,
) -> tuple[list[dict], Trace]:
    """Entry point for compile output. Two shapes:
    - {"queries": [...]} : parallel subqueries, pooled and ranked in one pass
    - {"stages": [{"name": ..., <subquery>}, ...]} : sequential; later stages may
      reference earlier results via "$name.unit" / "$name.units" / "$name.date".
      Reference resolution is deterministic — the LLM only writes the template.
    """
    stages = plan_obj.get("stages") or []
    if not stages:
        subs = parse_plan(plan_obj, vocab, speakers=speakers)
        return execute(
            store,
            vocab,
            subs,
            budget=budget,
            unit_cap=unit_cap,
            legal_kinds=legal_kinds,
            pack_v2=pack_v2,
            coverage=coverage,
        )
    trace = Trace()
    bindings: dict[str, dict] = {}
    bound_rows: list[dict] = []  # stages that consumed a $ref: the refined output
    free_rows: list[dict] = []  # unbound stages (anchors / wide nets): context
    for i, raw in enumerate(stages[:4]):
        if not isinstance(raw, dict):
            continue  # malformed compile output must degrade, not crash
        name = str(raw.get("name") or f"s{i + 1}")
        raw_body = {k: v for k, v in raw.items() if k != "name"}
        uses_ref = _has_stage_reference(raw_body)
        resolved, ok = _subst_refs(raw_body, bindings)
        if not ok:  # upstream stage empty -> this stage cannot run
            trace.log("stage", name=name, skipped="unresolved_ref")
            bindings[name] = _stage_binding([])
            continue
        subs = parse_plan({"queries": [resolved]}, vocab, speakers=speakers)
        rows, st_trace = execute(
            store,
            vocab,
            subs,
            budget=max(budget, 12),
            unit_cap=unit_cap + 2,
            legal_kinds=legal_kinds,
            pack_v2=pack_v2,
            coverage=coverage,
        )
        trace.log("stage", name=name, hits=len(rows), bound=uses_ref)
        trace.stages += [
            {"stage": f"{name}:{s['stage']}", **{k: v for k, v in s.items() if k != "stage"}}
            for s in st_trace.stages
        ]
        bindings[name] = _stage_binding(rows)
        if uses_ref:
            for r in rows:
                r["bound"] = True  # anchored result = the plan's intent
            bound_rows += rows
        else:
            free_rows += rows
    # $ref-bound stages are the plan's intent; unbound stages provide context.
    # Per-stage headroom (max(budget, 12)) lets a bound stage out-rank the
    # context stages; the caller's budget still bounds what comes back.
    out, seen, kept = [], set(), 0
    for r in bound_rows + free_rows:
        key = (r.get("type"), r.get("id"))
        if key in seen:
            continue
        if r.get("type") != "count":  # aggregates are not evidence rows
            if kept >= budget:
                continue
            kept += 1
        seen.add(key)
        out.append(r)
    return out, trace


def fact_row(fact, score: float) -> dict:
    """The one evidence-row rendering of a fact.

    Every retrieval channel, recovery pass, and executor emits rows of this
    exact shape. Building them in one place keeps the key set identical across
    call sites: consumers read these dicts by key, so a row that is missing
    one fails far from where it was constructed.
    """
    return {
        "type": "fact",
        "id": fact.id,
        "unit": fact.unit,
        "date": fact.when,
        "kind": fact.kind,
        "who": fact.who,
        "conf": fact.conf,
        "src": " ".join(fact.src),
        "score": round(score, 2),
        "text": fact.text,
    }


def line_row(line, score: float) -> dict:
    """The one evidence-row rendering of a raw conversation line."""
    return {
        "type": "line",
        "id": line.id,
        "unit": line.unit,
        "date": line.unit_date,
        "who": line.who,
        "src": line.id,
        "score": round(score, 2),
        "text": line.text,
    }


def _row(store: Store, typ: str, ident: str, score: float) -> dict:
    if typ == "F":
        return fact_row(store.facts[ident], score)
    if typ == "L":
        return line_row(store.lines[ident], score)
    u = store.units[ident]
    return {
        "type": "unit",
        "id": u.id,
        "date": u.date,
        "dur": u.dur_min,
        "score": round(score, 2),
        "text": u.title or u.id,
    }
