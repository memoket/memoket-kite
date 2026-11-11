"""Prepare one explicitly selected Memoket transcript from the HTTPS API."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import closing
from http.client import HTTPException, HTTPSConnection
from pathlib import Path
from time import sleep

from .contracts import HASH, TEXT, ToolError, decode_object, encode, validate
from .ingest import prepare_source
from .memoket_transcript import add_recording_metadata, assemble_transcript

_RESPONSE_LIMIT = 1024 * 1024


def _request(connection: HTTPSConnection, token: str, tool: str, args: dict) -> dict:
    try:
        for attempt in range(2):
            connection.request(
                "POST",
                f"/v1/tools/{tool}",
                body=encode(args).encode("utf-8"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with connection.getresponse() as response:
                status = response.status
                if status == 200:
                    body = response.read(_RESPONSE_LIMIT + 1)
                    break
                retry = response.getheader("Retry-After", "")
                delay = (
                    int(retry)
                    if retry.isascii() and retry.isdecimal() and len(retry) <= 10
                    else None
                )
            if status == 429 and attempt == 0 and delay is not None and delay <= 60:
                connection.close()
                sleep(delay)
                continue
            code = {
                401: "permission_denied",
                403: "permission_denied",
                404: "not_found",
                429: "rate_limited",
            }.get(status, "source_error")
            message = f"Memoket {tool} request failed (HTTP {status})"
            if status == 429 and delay is not None:
                message += f". Retry after {delay} seconds"
            raise ToolError(code, message)
    except (OSError, HTTPException) as exc:
        raise ToolError("source_error", "could not read the Memoket API response") from exc
    if len(body) > _RESPONSE_LIMIT:
        raise ToolError("source_error", "Memoket API response exceeds the per-page size limit")
    try:
        return decode_object(body.decode("utf-8"))
    except (ToolError, UnicodeError) as exc:
        raise ToolError("source_error", "Memoket API returned invalid JSON") from exc


def _pages(connection: HTTPSConnection, token: str, conversation_id: str) -> Iterator[dict]:
    offset, text_offset = 0, 0
    while True:
        response = _request(
            connection,
            token,
            "get_transcripts",
            {"conversation_id": conversation_id, "offset": offset, "text_offset": text_offset},
        )
        if (
            (offset, text_offset) == (0, 0)
            and response.get("error") is None
            and response.get("items") == []
        ):
            raise ToolError(
                "not_found", "Memoket returned no transcript for the selected recording"
            )
        yield response
        # Assembly validates the page and its continuation before requesting another page.
        page = response["items"][0]
        if not page["has_more"]:
            return
        offset, text_offset = page["next_offset"], page["next_text_offset"]


def memoket_source(
    root: Path,
    conversation_id: str,
    *,
    include_metadata: bool,
    project: str | None,
    expected_checksum: str | None,
) -> dict:
    validate(TEXT, conversation_id, "Memoket recording ID")
    if project is not None:
        validate(TEXT, project, "project")
    if expected_checksum is not None:
        validate(HASH, expected_checksum, "expected checksum")
    token = os.environ.get("MEMOKET_API_TOKEN", "").strip()
    if not token or not token.isascii() or not token.isprintable() or " " in token:
        raise ToolError("configuration_error", "set MEMOKET_API_TOKEN to a valid API token")

    with closing(HTTPSConnection("api.memoket.ai", timeout=30)) as connection:
        try:
            conversation = assemble_transcript(
                _pages(connection, token, conversation_id), expected_id=conversation_id
            )
            if include_metadata:
                recording = _request(
                    connection, token, "get_conversations", {"conversation_ids": [conversation_id]}
                )
                add_recording_metadata(conversation, recording)
        except (AttributeError, KeyError, TypeError, OverflowError) as exc:
            raise ToolError("invalid_input", "malformed Memoket API response") from exc

    if project is not None:
        conversation["metadata"]["project"] = project
    source = {"format": "conversation", "payload": conversation}
    if expected_checksum is not None:
        source["expected_checksum"] = expected_checksum
    prepared, _ = prepare_source(root, source)
    return {"source": prepared}
