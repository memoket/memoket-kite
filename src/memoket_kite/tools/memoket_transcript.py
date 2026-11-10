"""Assemble saved Memoket get_transcripts responses without contacting the service."""

from __future__ import annotations

from .contracts import ToolError


def _page(response: dict) -> dict:
    if response.get("error") is not None:
        raise ToolError("invalid_input", "Memoket transcript page reports an API error")
    items = response.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ToolError("invalid_input", "Memoket transcript response must contain one recording")
    page = items[0]
    if (
        response.get("truncated", page["has_more"]) is not page["has_more"]
        or response.get("next_offset", page["next_offset"]) != page["next_offset"]
    ):
        raise ToolError("invalid_input", "inconsistent Memoket transcript page envelope")
    return page


def _segment(line: dict) -> dict:
    segment = {"segment_id": line["id"]}
    for original, field in (
        ("speaker", "speaker_id"),
        ("started_offset_ms", "start_ms"),
        ("ended_offset_ms", "end_ms"),
    ):
        if line.get(original) is not None:
            segment[field] = line[original]
    return segment


def _recording(payload: dict, identity: str) -> dict:
    if payload.get("error") is not None:
        raise ToolError("invalid_input", "Memoket recording metadata reports an API error")
    if "id" in payload:
        if payload["id"] != identity:
            raise ToolError("invalid_input", "Memoket recording metadata has a different ID")
        return payload
    items = payload.get("items")
    if not isinstance(items, list):
        raise ToolError("invalid_input", "expected a successful Memoket get_conversations response")
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == identity]
    if len(matches) != 1:
        raise ToolError("invalid_input", "Memoket metadata must contain one matching recording")
    return matches[0]


def api_transcript(payload: dict) -> dict:
    pages = payload.get("pages", [payload])
    if not isinstance(pages, list) or not pages:
        raise ToolError("invalid_input", "Memoket transcript pages must be a nonempty array")

    identity, total = None, None
    segments = []
    pending = None
    fragments = []
    text_offset = 0
    for page_number, response in enumerate(pages):
        page = _page(response)
        if page_number == 0:
            identity, total = page["conversation_id"], page["total_line_count"]
        if page["conversation_id"] != identity or page["total_line_count"] != total:
            raise ToolError("invalid_input", "Memoket transcript pages disagree on the recording")
        if (
            type(page["offset"]) is not int
            or type(page["text_offset"]) is not int
            or (page["offset"], page["text_offset"]) != (len(segments), text_offset)
        ):
            raise ToolError("invalid_input", "Memoket transcript pages are missing or out of order")

        lines = page["lines"]
        if (
            not isinstance(lines, list)
            or not lines
            or type(page["line_count"]) is not int
            or page["line_count"] != len(lines)
        ):
            raise ToolError("invalid_input", "Memoket transcript page has an invalid line count")
        for line_index, line in enumerate(lines):
            segment, text = _segment(line), line["text"]
            if (
                not isinstance(text, str)
                or type(line["text_offset"]) is not int
                or line["text_offset"] != text_offset
                or type(line["text_truncated"]) is not bool
                or (pending is not None and pending != segment)
            ):
                raise ToolError("invalid_input", "inconsistent Memoket transcript line fragment")
            fragments.append(text)
            if line["text_truncated"]:
                next_text_offset = page["next_text_offset"]
                # Character cursors can count Unicode code points or UTF-16 code units.
                lengths = {len(text), len(text.encode("utf-16-le", errors="surrogatepass")) // 2}
                if (
                    line_index != len(lines) - 1
                    or type(next_text_offset) is not int
                    or next_text_offset <= text_offset
                    or next_text_offset - text_offset not in lengths
                ):
                    raise ToolError("invalid_input", "invalid Memoket transcript continuation")
                pending, text_offset = segment, next_text_offset
            else:
                joined = "".join(fragments)
                if len(fragments) > 1:
                    # A UTF-16 cursor can leave half a character in each JSON page.
                    # Unpaired surrogates still fail conversation validation.
                    joined = joined.encode("utf-16-le", errors="surrogatepass").decode(
                        "utf-16-le", errors="surrogatepass"
                    )
                segments.append({**segment, "text": joined})
                pending, text_offset = None, 0
                fragments.clear()

        more_pages = page_number + 1 < len(pages)
        if page["has_more"] is not more_pages:
            raise ToolError("invalid_input", "supply all Memoket transcript pages in order")
        if more_pages:
            if (
                type(page["next_offset"]) is not int
                or page["next_offset"] != len(segments)
                or type(page["next_text_offset"]) is not int
                or page["next_text_offset"] != text_offset
            ):
                raise ToolError("invalid_input", "invalid Memoket transcript continuation")
        elif (
            pending is not None
            or page["next_offset"] is not None
            or page["next_text_offset"] not in (None, 0)
        ):
            raise ToolError("invalid_input", "Memoket transcript ends with an incomplete page")

    if type(total) is not int or len(segments) != total:
        raise ToolError("invalid_input", "Memoket transcript line total does not match the pages")
    conversation = {
        "conversation_id": identity,
        "source": "memoket",
        "segments": segments,
        "metadata": {"source_reference": identity},
    }
    if "recording" in payload:
        recording = _recording(payload["recording"], identity)
        for original, field in (
            ("title", "title"),
            ("started_at", "started_at"),
            ("language_code", "language"),
        ):
            if recording.get(original) is not None:
                conversation[field] = recording[original]
    return conversation
