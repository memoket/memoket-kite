"""Shared evidence ordering for trace reports and correction validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .contracts import utc_timestamp

_CLOCK_ORIGIN = datetime(1, 1, 1, tzinfo=timezone.utc)
_MICROSECOND = timedelta(microseconds=1)
_DAY_MICROS = timedelta(days=1) // _MICROSECOND


def _timestamp_micros(timestamp: str) -> int:
    return (utc_timestamp(timestamp) - _CLOCK_ORIGIN) // _MICROSECOND


def _offset_bounds(segments: list[dict]) -> tuple[int, int] | None:
    if any("start_ms" not in line for line in segments):
        return None
    return (
        min(line["start_ms"] for line in segments),
        max(line.get("end_ms", line["start_ms"]) for line in segments),
    )


def source_timing(
    revision: str, day: str | None, started_at: str | None, segments: list[dict]
) -> tuple:
    offsets = _offset_bounds(segments)
    absolute = None
    if started_at:
        origin = _timestamp_micros(started_at)
        start, end = offsets or (0, 0)
        absolute = origin + start * 1000, origin + end * 1000
    elif day:
        # Without a clock time, the evidence can fall anywhere on its source day.
        start = _timestamp_micros(f"{day}T00:00:00Z")
        absolute = start, start + _DAY_MICROS
    return revision, offsets, absolute


def precedes(first: tuple, second: tuple) -> bool:
    first_revision, first_offsets, first_absolute = first
    second_revision, second_offsets, second_absolute = second
    if first_revision is not None and first_revision == second_revision:
        left, right = first_offsets, second_offsets
    else:
        left, right = first_absolute, second_absolute
    return left is not None and right is not None and left[0] < right[0] and left[1] <= right[0]
