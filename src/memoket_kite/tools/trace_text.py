"""Render the existing trace report for a terminal."""

from __future__ import annotations

import re
from collections.abc import Iterable

_HEADINGS = {
    "topic-tracing": "Topic trace",
    "task-tracing": "Task trace",
    "timeline-reconstruction": "Timeline reconstruction",
    "decision-evolution": "Decision evolution",
}
_STATUS = {
    "confirmed": "Confirmed",
    "proposal": "Proposal",
    "rejected": "Rejected option",
    "open": "Open question",
    "inference": "Inference",
    "statement": "Statement",
}
# Keep terminal controls and directional overrides visible as escapes.
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]")


def _inline(text: str) -> str:
    return _CONTROLS.sub(lambda match: match[0].encode("unicode_escape").decode("ascii"), text)


def _references(numbers: Iterable[int]) -> str:
    return " ".join(f"[{number}]" for number in sorted(set(numbers)))


def render_trace(report: dict, query: dict) -> str:
    timeline = report["timeline"]
    axis = report["time_axis"]
    lines = [_HEADINGS[report["trace"]]]
    follow = query.get("follow_states", False)
    for field in ("topics", "entities", "events"):
        resolved = ", ".join(_inline(value) for value in report[f"resolved_{field}"])
        unresolved = ", ".join(_inline(value) for value in report[f"unresolved_{field}"])
        if resolved:
            label = (
                f"{field.capitalize()} clues"
                if follow and field != "topics"
                else field.capitalize()
            )
            lines.append(f"{label}  {resolved}")
        if unresolved:
            lines.append(f"Unresolved {field}  {unresolved}")
    for field, label in (
        ("projects", "Projects"),
        ("speakers", "Speaker clues" if follow else "Speakers"),
    ):
        if field in query:
            lines.append(f"{label}  {', '.join(_inline(value) for value in query[field])}")
    for field, label in (
        ("source", "Source"),
        ("conversation_id", "Conversation"),
        ("state_key", "State key"),
        ("text", "Phrase clue" if follow else "Contains"),
    ):
        if field in query:
            lines.append(f"{label}  {_inline(query[field])}")
    if keys := report.get("followed_state_keys"):
        lines.append(f"Followed states  {', '.join(_inline(key) for key in keys)}")
    start = f" from {query['start_date']}" if "start_date" in query else ""
    end = f" through {query['end_date']}" if "end_date" in query else ""
    lines.append(
        f"Showing {len(timeline)} of {report['matched_count']} facts by {axis} date{start}{end}"
    )
    for basis in ("unknown", "inferred"):
        count = report[f"omitted_{basis}_time"]
        if count:
            lines.append(f"Excluded by date filter  {count} with {basis} {axis} dates")

    sources = {line["source_line_id"]: line for row in timeline for line in row["evidence"]}
    numbers = {line_id: number for number, line_id in enumerate(sources, 1)}
    references = {
        row["fact_id"]: [numbers[line["source_line_id"]] for line in row["evidence"]]
        for row in timeline
    }

    states = report["current_reported_states"]
    uncertain_times = set(report.get("uncertain_time_state_keys", []))
    if states:
        lines.extend(("", "Reported states"))
        for key, state in states.items():
            refs = _references(
                number for fact_id in state["fact_ids"] for number in references[fact_id]
            )
            status = state["status"].capitalize()
            if key in uncertain_times:
                status += " (evidence date uncertain)"
            value = f"  {_inline(state['value'])}" if state["status"] == "confirmed" else ""
            lines.append(f"  {_inline(key)}  {status}{value}  {refs}")

    if timeline:
        lines.extend(("", "Timeline"))
    for row in timeline:
        date = row["source_time"] if axis == "source" else row["event_date"]
        day = date or "Date unknown"
        if axis == "event" and row["event_date_basis"] == "inferred":
            day += " (inferred)"
        status = _STATUS[row["status"]]
        if not row["effective"]:
            status += " (historical)"
        elif row["supersession_uncertain"]:
            status += " (supersession uncertain)"
        refs = _references(references[row["fact_id"]])
        lines.append(f"  {day}  {status}  {refs}")
        lines.append(f"    {_inline(row['content'])}")

    if sources:
        lines.extend(("", "Original sources"))
    for line_id, line in sources.items():
        label = f"{line['source']}/{line['conversation_id']}#{line['segment_id']}"
        speaker = f"  {_inline(line['speaker_id'])}" if "speaker_id" in line else ""
        offset = f"  {line['start_ms']} ms" if "start_ms" in line else ""
        lines.append(f"  [{numbers[line_id]}] {_inline(label)}{speaker}{offset}")
        lines.append(f"    Source line ID  {line_id}")
        lines.extend(f"    > {_inline(text)}" for text in line["text"].split("\n"))

    if report["next_cursor"]:
        lines.extend(("", f"Next cursor  {report['next_cursor']}"))
    return "\n".join(lines)
