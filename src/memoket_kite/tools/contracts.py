"""Validation shared by the stage interface and the durable workspace."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def encode(value) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )


def read_utf8(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeError as exc:
        raise ToolError("invalid_input", "input must be valid UTF-8") from exc


def decode_object(raw: str) -> dict:
    def pairs(items):
        result = dict(items)
        if len(items) != len(result):
            raise ToolError("invalid_input", "duplicate JSON input keys")
        return result

    try:
        result = json.loads(raw, object_pairs_hook=pairs)
        encode(result)
    except (ValueError, RecursionError) as exc:
        raise ToolError("invalid_input", "input must be finite, valid JSON") from exc
    if not isinstance(result, dict):
        raise ToolError("invalid_input", "input must be a JSON object")
    return result


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


CURSOR = {"type": "string", "pattern": r"^[0-9]{1,16}\.[1-9][0-9]{0,15}\.[a-f0-9]{64}$"}


def page_cursor(scope: list, offset: int) -> str:
    return f"{scope[1]}.{offset}.{digest([scope, offset])}"


def page_offset(scope: list, token: str | None, *, name: str = "trace") -> int:
    if token is None:
        return 0
    version, offset, _ = token.split(".")
    if int(version) != scope[1]:
        raise ToolError("stale_snapshot", f"memory changed; restart the {name} without its cursor")
    offset = int(offset)
    if token != page_cursor(scope, offset):
        raise ToolError("invalid_input", f"cursor does not match this {name}; restart without it")
    return offset


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


_TIMESTAMP = re.compile(
    r"(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(?:[.](\d{1,6}))?([Zz]|[+-]\d{2}:\d{2})"
)


def utc_timestamp(value: str) -> datetime:
    match = _TIMESTAMP.fullmatch(value)
    if not match:
        raise ValueError("expected an ISO timestamp with an explicit offset")
    day, clock, fraction, offset = match.groups()
    if offset == "-00:00" or (len(offset) > 1 and int(offset[-2:]) >= 60):
        raise ValueError("invalid timestamp offset")
    offset = "+00:00" if offset.upper() == "Z" else offset
    # Python 3.10 requires fractional seconds to have three or six digits.
    fraction = (fraction or "").ljust(6, "0")
    return datetime.fromisoformat(f"{day}T{clock}.{fraction}{offset}").astimezone(timezone.utc)


def _validation_message(problem, location: str) -> str:
    # Only disclose schema field names and array positions. Instance keys and
    # jsonschema's message may contain private source or annotation data.
    schema_path = iter(list(problem.absolute_schema_path)[:-1])
    instance_path = iter(problem.absolute_path)
    for step in schema_path:
        if step == "properties":
            location += f".{next(schema_path)}"
            next(instance_path, None)
        elif step == "items":
            index = next(instance_path, None)
            location += f"[{index}]" if isinstance(index, int) else "[]"
        elif step == "additionalProperties":
            location += "[*]"
            next(instance_path, None)

    rule = problem.validator
    if rule == "required":
        missing = [field for field in problem.validator_value if field not in problem.instance]
        return f"{location} requires {', '.join(missing)}"
    if rule == "additionalProperties":
        return f"{location} contains unsupported fields"
    if rule == "format":
        rule = f"{problem.validator_value} format"
    return f"{location} fails {rule} validation"


def validate(schema: dict, value, label: str) -> None:
    from jsonschema import Draft202012Validator, FormatChecker

    checker = FormatChecker()

    @checker.checks("date-time", raises=(ValueError, OverflowError))
    def is_timestamp(item):
        if isinstance(item, str):
            utc_timestamp(item)
        return True

    problem = next(Draft202012Validator(schema, format_checker=checker).iter_errors(value), None)
    if problem is not None:
        raise ToolError("invalid_input", _validation_message(problem, label))


def schema_file(root: Path, name: str) -> dict:
    return json.loads((root / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def object_schema(required: dict, optional: dict | None = None) -> dict:
    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": False,
        "properties": {**required, **(optional or {})},
    }


TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
ID = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
DATE = {"type": "string", "format": "date"}
COUNT = {"type": "integer", "minimum": 0}
XML_UNSAFE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def array(items: dict, minimum: int = 0, maximum: int = 10000) -> dict:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}
