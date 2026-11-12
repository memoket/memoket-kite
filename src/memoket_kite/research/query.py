"""Immutable data contracts for the KITE public API."""

from __future__ import annotations

import copy
import re
from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, Literal, Mapping

from memoket_kite.research.errors import InvalidQueryError

Target = Literal["facts", "lines", "units"]
Confidence = Literal["low", "med", "high"]


def _sequence_items(
    name: str,
    value: object,
    *,
    item_types: tuple[type, ...],
    description: str,
) -> tuple:
    """Materialize a public query sequence without coercing unrelated values."""
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, (bytes, bytearray, Mapping)) or not isinstance(value, Iterable):
        raise InvalidQueryError(f"{name} must be {description}")
    else:
        try:
            items = tuple(value)
        except (TypeError, ValueError) as exc:
            raise InvalidQueryError(f"{name} must be {description}") from exc
    if any(not isinstance(item, item_types) for item in items):
        raise InvalidQueryError(f"{name} must be {description}")
    return items


_WHERE_SEQUENCE_FIELDS = {"entities", "obj", "place", "event", "kind", "who", "grep_all"}
_WHERE_FIELDS = {
    "topics",
    *_WHERE_SEQUENCE_FIELDS,
    "time",
    "units",
    "unit",
    "grep",
    "entity_kind",
    "conf_min",
}
def _valid_stage_name(value: str) -> bool:
    # Validate Unicode identifiers (including combining marks), with the
    # existing hyphen and internal-space separators. Do not normalize the
    # stored label: declaration and reference still require exact equality.
    return bool(value and value == value.strip(" ") and not value.startswith("-")
                and value.replace("-", "_").replace(" ", "_").isidentifier())


_REFERENCE = re.compile(r"^\$([^.\s]+(?: +[^.\s]+)*)\.(unit|units|date)$")
_DATE_REFERENCE = re.compile(r"^\$[^.\s]+(?: +[^.\s]+)*\.date$")


def _stage_reference_parts(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    match = _REFERENCE.fullmatch(value)
    return match.groups() if match is not None else None


def _is_stage_date_reference(value: object) -> bool:
    return isinstance(value, str) and bool(_DATE_REFERENCE.fullmatch(value))


def _partial_iso_bounds(value: object, *, label: str) -> tuple[date, date] | None:
    """Return the inclusive calendar bounds represented by a partial ISO date."""
    if not isinstance(value, str):
        raise InvalidQueryError(f"{label} must be YYYY, YYYY-MM, or YYYY-MM-DD")
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}", value):
            year = int(value)
            return date(year, 1, 1), date(year, 12, 31)
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            return date(year, month, 1), date(year, month, monthrange(year, month)[1])
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = date.fromisoformat(value)
            return parsed, parsed
    except ValueError as exc:
        raise InvalidQueryError(f"{label} must be YYYY, YYYY-MM, or YYYY-MM-DD") from exc
    raise InvalidQueryError(f"{label} must be YYYY, YYYY-MM, or YYYY-MM-DD")


def _validate_time_order(
    start: tuple[date, date] | None,
    end: tuple[date, date] | None,
    *,
    label: str,
) -> None:
    if start is not None and end is not None and start[0] > end[1]:
        raise InvalidQueryError(f"{label} start must not be after end")


def _validate_plan_time(value: object, *, allow_refs: bool) -> None:
    if allow_refs and _is_stage_date_reference(value):
        return
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 2
        or any(not isinstance(endpoint, str) for endpoint in value)
    ):
        raise InvalidQueryError("plan where time must be a one- or two-string list")

    bounds: list[tuple[date, date] | None] = []
    for endpoint in value:
        if allow_refs and _is_stage_date_reference(endpoint):
            bounds.append(None)
        else:
            bounds.append(_partial_iso_bounds(endpoint, label="plan where time endpoint"))
    if len(bounds) == 2 and not any(_is_stage_date_reference(item) for item in value):
        _validate_time_order(bounds[0], bounds[1], label="plan where time")


def _validate_string_sequence(
    name: str,
    value: object,
    *,
    allow_refs: bool,
    reference_fields: frozenset[str] = frozenset(),
) -> None:
    if isinstance(value, str) and value.startswith("$"):
        reference = _stage_reference_parts(value)
        if allow_refs and reference is not None and reference[1] in reference_fields:
            return
        raise InvalidQueryError(f"plan where {name} has an unsupported stage reference")
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise InvalidQueryError(f"plan where {name} must be a list of strings")
    for item in value:
        if not item.startswith("$"):
            continue
        reference = _stage_reference_parts(item)
        if not allow_refs or reference is None or reference[1] not in reference_fields:
            raise InvalidQueryError(f"plan where {name} has an unsupported stage reference")


def _validate_plan_entry(entry: dict[str, Any], *, allow_refs: bool = False) -> None:
    allowed_entry_fields = {"select", "where", "pipe"} | ({"name"} if allow_refs else set())
    unexpected_entry_fields = set(entry) - allowed_entry_fields
    if unexpected_entry_fields:
        raise InvalidQueryError(
            "plan entry has unsupported fields: "
            f"{', '.join(sorted(map(str, unexpected_entry_fields)))}"
        )
    if "name" in entry and (
        not isinstance(entry["name"], str) or not _valid_stage_name(entry["name"])
    ):
        raise InvalidQueryError("plan stage name must be a non-empty identifier")
    target = entry.get("select", "facts")
    if target not in ("facts", "lines", "units"):
        raise InvalidQueryError("plan select must be 'facts', 'lines', or 'units'")

    where = entry["where"] if "where" in entry else {}
    if not isinstance(where, dict):
        raise InvalidQueryError("plan where must be a mapping")
    unexpected_where_fields = set(where) - _WHERE_FIELDS
    if unexpected_where_fields:
        raise InvalidQueryError(
            "plan where has unsupported fields: "
            f"{', '.join(sorted(map(str, unexpected_where_fields)))}"
        )
    target_fields = {
        "facts": _WHERE_FIELDS,
        "lines": {"units", "unit", "who", "time", "grep"},
        "units": {"units", "unit", "time", "grep"},
    }[target]
    ignored_for_target = set(where) - target_fields
    if ignored_for_target:
        raise InvalidQueryError(
            f"plan where fields are unsupported for {target}: "
            f"{', '.join(sorted(map(str, ignored_for_target)))}"
        )
    if "unit" in where and "units" in where:
        raise InvalidQueryError("plan where must not declare both unit and units")
    for name in _WHERE_SEQUENCE_FIELDS & set(where):
        _validate_string_sequence(name, where[name], allow_refs=False)
    if "units" in where:
        _validate_string_sequence(
            "units",
            where["units"],
            allow_refs=allow_refs,
            reference_fields=frozenset({"unit", "units"}),
        )
    if "unit" in where:
        unit = where["unit"]
        if not isinstance(unit, str):
            raise InvalidQueryError("plan where unit must be a string")
        if unit.startswith("$"):
            reference = _stage_reference_parts(unit)
            if not allow_refs or reference is None or reference[1] != "unit":
                raise InvalidQueryError("plan where unit has an unsupported stage reference")
    if "time" in where:
        _validate_plan_time(where["time"], allow_refs=allow_refs)
    for name in ("grep", "entity_kind"):
        if name in where and not isinstance(where[name], str):
            raise InvalidQueryError(f"plan where {name} must be a string")
    if "conf_min" in where and where["conf_min"] not in ("low", "med", "high"):
        raise InvalidQueryError("plan where conf_min must be 'low', 'med', or 'high'")
    pattern = where.get("grep")
    if pattern:
        try:
            re.compile(str(pattern))
        except re.error as exc:
            raise InvalidQueryError(f"invalid plan grep pattern: {exc}") from exc
    for pattern in where.get("grep_all", []):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise InvalidQueryError(f"invalid plan grep_all pattern: {exc}") from exc
    topics = where.get("topics", [])
    if not isinstance(topics, list):
        raise InvalidQueryError("plan where topics must be a list")
    for topic in topics:
        if isinstance(topic, str):
            if not topic.strip():
                raise InvalidQueryError("plan topic code must be a non-empty string")
            continue
        if not isinstance(topic, dict):
            raise InvalidQueryError("plan topics must contain strings or mappings")
        if set(topic) - {"code", "closure"}:
            raise InvalidQueryError("plan topic mappings accept only code and closure")
        code = topic.get("code")
        if not isinstance(code, str) or not code.strip():
            raise InvalidQueryError("plan topic code must be a non-empty string")
        if "closure" in topic and not isinstance(topic["closure"], bool):
            # The executor calls bool(value), so the seemingly false string
            # "false" expands to the full descendant closure.
            raise InvalidQueryError("plan topic closure must be a boolean")

    pipe = entry["pipe"] if "pipe" in entry else []
    if not isinstance(pipe, list):
        raise InvalidQueryError("plan pipe must be a list of mappings")
    if len(pipe) > 4:
        raise InvalidQueryError("plan pipe may contain at most four operations")
    if target == "lines" and pipe:
        raise InvalidQueryError("line plans do not support pipe operations")

    for step in pipe:
        if not isinstance(step, dict):
            raise InvalidQueryError("plan pipe must be a list of mappings")
        op = step.get("op")
        if op not in ("sort", "head", "tail", "count"):
            # The executor skips steps it does not recognise, so a typo like
            # {"op": "haed"} would silently return the unclipped result.
            raise InvalidQueryError("plan pipe op must be 'sort', 'head', 'tail', or 'count'")

        if op == "sort":
            unexpected = set(step) - {"op", "key", "desc"}
            if unexpected:
                raise InvalidQueryError(
                    "plan pipe sort has unsupported fields: "
                    f"{', '.join(sorted(map(str, unexpected)))}"
                )
            if step.get("key") not in ("t", "dur"):
                raise InvalidQueryError("plan pipe sort key must be 't' or 'dur'")
            if "desc" not in step or not isinstance(step["desc"], bool):
                # bool("false") is True in the executor, so coercion would
                # reverse the user's requested ordering.
                raise InvalidQueryError("plan pipe sort desc must be a boolean")
        elif op in ("head", "tail"):
            unexpected = set(step) - {"op", "n"}
            if unexpected:
                raise InvalidQueryError(
                    f"plan pipe {op} has unsupported fields: "
                    f"{', '.join(sorted(map(str, unexpected)))}"
                )
            if "n" not in step:
                raise InvalidQueryError(f"plan pipe {op} n must be a positive integer")
            n = step["n"]
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                raise InvalidQueryError(f"plan pipe {op} n must be a positive integer")
        elif set(step) != {"op"}:
            raise InvalidQueryError("plan pipe count does not accept additional fields")


def _stage_reference_names(entry: dict[str, Any]) -> set[str]:
    where = entry.get("where", {})
    names: set[str] = set()
    for where_field in ("unit", "units", "time"):
        value = where.get(where_field)
        values = value if isinstance(value, list) else [value]
        for item in values:
            reference = _stage_reference_parts(item)
            if reference is not None:
                names.add(reference[0])
    return names


@dataclass(frozen=True)
class TopicMatch:
    """A controlled topic code and whether its descendants should match."""

    code: str
    descendants: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise InvalidQueryError("topic code must be a non-empty string")
        if not isinstance(self.descendants, bool):
            raise InvalidQueryError("topic descendants must be a boolean")


@dataclass(frozen=True)
class TimeRange:
    """Inclusive ISO or partial-ISO time bounds."""

    start: str = ""
    end: str = ""

    def __post_init__(self) -> None:
        start = _partial_iso_bounds(self.start, label="time range start")
        end = _partial_iso_bounds(self.end, label="time range end")
        _validate_time_order(start, end, label="time range")


@dataclass(frozen=True)
class Sort:
    """Sort a query by event time or source-unit duration."""

    key: Literal["time", "duration"] = "time"
    descending: bool = False

    def __post_init__(self) -> None:
        if self.key not in ("time", "duration"):
            raise InvalidQueryError("sort key must be 'time' or 'duration'")
        if not isinstance(self.descending, bool):
            raise InvalidQueryError("sort descending must be a boolean")


@dataclass(frozen=True)
class SymbolicQuery:
    """A deterministic symbolic retrieval request over one Codebook."""

    target: Target = "facts"
    topics: tuple[TopicMatch, ...] = ()
    entities: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    speakers: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    time: TimeRange | None = None
    text_pattern: str = ""
    confidence: Confidence = "low"
    objects: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    sort: Sort | None = None
    limit: int | None = None
    aggregate: Literal["count"] | None = None

    def __post_init__(self) -> None:
        if self.target not in ("facts", "lines", "units"):
            raise InvalidQueryError("target must be 'facts', 'lines', or 'units'")
        if self.confidence not in ("low", "med", "high"):
            raise InvalidQueryError("confidence must be 'low', 'med', or 'high'")
        if self.aggregate not in (None, "count"):
            raise InvalidQueryError("aggregate must be None or 'count'")
        if self.time is not None and not isinstance(self.time, TimeRange):
            raise InvalidQueryError("time must be a TimeRange")
        if self.sort is not None and not isinstance(self.sort, Sort):
            raise InvalidQueryError("sort must be a Sort")
        if self.limit is not None and (
            not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit < 1
        ):
            raise InvalidQueryError("limit must be a positive integer")
        topic_items = _sequence_items(
            "topics",
            self.topics,
            item_types=(str, TopicMatch),
            description="a string or a non-string iterable of strings or TopicMatch values",
        )
        topics = tuple(
            topic if isinstance(topic, TopicMatch) else TopicMatch(topic) for topic in topic_items
        )
        object.__setattr__(self, "topics", topics)
        for name in (
            "entities",
            "kinds",
            "speakers",
            "unit_ids",
            "objects",
            "places",
            "events",
        ):
            values = _sequence_items(
                name,
                getattr(self, name),
                item_types=(str,),
                description="a string or a non-string iterable of strings",
            )
            object.__setattr__(self, name, values)
        if self.text_pattern is None:
            object.__setattr__(self, "text_pattern", "")
        elif not isinstance(self.text_pattern, str):
            raise InvalidQueryError("text_pattern must be a string")
        if self.text_pattern:
            try:
                re.compile(self.text_pattern)
            except re.error as exc:
                raise InvalidQueryError(f"invalid text_pattern: {exc}") from exc
        fact_filters = any(
            (
                self.topics,
                self.entities,
                self.kinds,
                self.objects,
                self.places,
                self.events,
            )
        )
        if self.target == "lines" and (
            fact_filters
            or self.confidence != "low"
            or self.sort is not None
            or self.limit is not None
            or self.aggregate is not None
        ):
            raise InvalidQueryError(
                "line queries support speakers, unit_ids, time, and text_pattern; "
                "result size is controlled by Codebook.query(budget=...)"
            )
        if self.target == "units" and (fact_filters or self.speakers or self.confidence != "low"):
            raise InvalidQueryError(
                "unit queries support unit_ids, time, text_pattern, sort, limit, and count"
            )

    def to_plan(self) -> "QueryPlan":
        """Translate this query to the existing symbolic plan schema."""
        where: dict[str, Any] = {}
        if self.topics:
            where["topics"] = [
                {"code": topic.code, "closure": topic.descendants} for topic in self.topics
            ]
        for public_name, plan_name in (
            ("entities", "entities"),
            ("kinds", "kind"),
            ("speakers", "who"),
            ("unit_ids", "units"),
            ("objects", "obj"),
            ("places", "place"),
            ("events", "event"),
        ):
            values = getattr(self, public_name)
            if values:
                where[plan_name] = list(values)
        if self.time is not None:
            where["time"] = [self.time.start, self.time.end]
        if self.text_pattern:
            where["grep"] = self.text_pattern
        if self.confidence != "low":
            where["conf_min"] = self.confidence

        pipe: list[dict[str, Any]] = []
        if self.sort is not None:
            pipe.append(
                {
                    "op": "sort",
                    "key": "t" if self.sort.key == "time" else "dur",
                    "desc": self.sort.descending,
                }
            )
        if self.limit is not None:
            pipe.append({"op": "head", "n": self.limit})
        if self.aggregate == "count":
            pipe.append({"op": "count"})

        query: dict[str, Any] = {"select": self.target, "where": where}
        if pipe:
            query["pipe"] = pipe
        return QueryPlan.from_dict({"queries": [query], "intent": "factual"})


@dataclass(frozen=True)
class QueryPlan:
    """Validated, copy-isolated symbolic retrieval plan."""

    _data: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        data = copy.deepcopy(dict(self._data))
        unexpected_top_level = set(data) - {"queries", "stages", "intent"}
        if unexpected_top_level:
            raise InvalidQueryError(
                "query plan has unsupported top-level fields: "
                f"{', '.join(sorted(map(str, unexpected_top_level)))}"
            )
        if data.get("intent", "factual") not in (
            "factual",
            "speculative",
            "aggregate",
            "enumeration",
            "attribution",
        ):
            raise InvalidQueryError(
                "query plan intent must be factual, speculative, aggregate, "
                "enumeration, or attribution"
            )
        has_queries = "queries" in data
        has_stages = "stages" in data
        if not has_queries and not has_stages:
            raise InvalidQueryError("query plan must contain 'queries' or 'stages'")
        if has_queries and has_stages:
            raise InvalidQueryError("query plan must not contain both 'queries' and 'stages'")
        if has_queries and not isinstance(data["queries"], list):
            raise InvalidQueryError("'queries' must be a list")
        if has_stages and not isinstance(data["stages"], list):
            raise InvalidQueryError("'stages' must be a list")
        kind = "stages" if has_stages else "queries"
        entries = data[kind]
        if not entries:
            raise InvalidQueryError(f"'{kind}' must contain at least one entry")
        limit = 4 if has_stages else 3
        if len(entries) > limit:
            raise InvalidQueryError(f"'{kind}' may contain at most {limit} entries")
        if any(not isinstance(entry, dict) for entry in entries):
            raise InvalidQueryError("every query or stage must be a mapping")
        explicit_stage_names: set[str] = set()
        effective_stage_names: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            _validate_plan_entry(entry, allow_refs=has_stages)
            if not has_stages:
                continue
            unknown_references = _stage_reference_names(entry) - explicit_stage_names
            if unknown_references:
                raise InvalidQueryError(
                    "plan stage references must target an earlier named stage: "
                    f"{', '.join(sorted(unknown_references))}"
                )
            explicit_name = entry.get("name")
            effective_name = explicit_name or f"s{index}"
            if effective_name in effective_stage_names:
                raise InvalidQueryError(f"plan stage name is duplicated: {effective_name}")
            effective_stage_names.add(effective_name)
            if explicit_name is not None:
                explicit_stage_names.add(explicit_name)
        object.__setattr__(self, "_data", MappingProxyType(data))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QueryPlan":
        if not isinstance(value, Mapping):
            raise InvalidQueryError("query plan must be a mapping")
        return cls(value)

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self._data))


@dataclass(frozen=True)
class SourceLine:
    """Original conversation line supporting retrieved Evidence."""

    id: str
    unit_id: str
    source_time: str
    subject: str
    text: str


@dataclass(frozen=True)
class Evidence:
    """Immutable retrieved item with its supporting sources and metadata.

    A Fact is KITE's structured memory primitive. ``Evidence`` is the research
    result view used to expose a retrieved Fact—or a line, unit, or instance—
    without leaking mutable storage records.
    """

    id: str
    type: Literal["fact", "line", "unit", "instance"]
    text: str
    unit_id: str | None = None
    event_time: str | None = None
    source_time: str | None = None
    kind: str | None = None
    subject: str | None = None
    confidence: str | None = None
    topics: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    sources: tuple[SourceLine, ...] = ()
    score: float | None = None
    cited: bool = False
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "topics", tuple(self.topics))
        object.__setattr__(self, "entities", tuple(self.entities))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(copy.deepcopy(dict(self.attributes))),
        )


@dataclass(frozen=True)
class Aggregate:
    operation: Literal["count"]
    value: int
    target: str
    text: str


@dataclass(frozen=True)
class TraceEvent:
    stage: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "details",
            MappingProxyType(copy.deepcopy(dict(self.details))),
        )


@dataclass(frozen=True)
class ExecutionTrace:
    """Recorded symbolic decision steps for one retrieval request."""

    events: tuple[TraceEvent, ...] = ()


@dataclass(frozen=True)
class QueryResult:
    """Result of deterministic symbolic query evaluation."""

    plan: QueryPlan
    evidence: tuple[Evidence, ...]
    aggregates: tuple[Aggregate, ...]
    trace: ExecutionTrace


@dataclass(frozen=True)
class RetrievalResult:
    """Evidence and trace produced from a natural-language retrieval request."""

    question: str
    plan: QueryPlan
    evidence: tuple[Evidence, ...]
    aggregates: tuple[Aggregate, ...]
    trace: ExecutionTrace
    used_fallback: bool


@dataclass(frozen=True)
class AnswerResult:
    """Evidence-backed answer with citations and symbolic decision trace."""

    question: str
    answer: str
    plan: QueryPlan
    evidence: tuple[Evidence, ...]
    citation_ids: tuple[str, ...]
    trace: ExecutionTrace
    used_fallback: bool


@dataclass(frozen=True)
class TopicInfo:
    code: str
    parents: tuple[str, ...]
    status: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class EntityInfo:
    code: str
    name: str
    type: str
    aliases: tuple[str, ...]
    relations: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CodebookStats:
    shards: int
    units: int
    facts: int
    lines: int
    topics: int
    entities: int
    speakers: tuple[str, ...]
    start_date: str | None
    end_date: str | None
