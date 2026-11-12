"""OpenAI-compatible HTTP client for KITE's LLM boundaries."""

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

from memoket_kite.errors import ProviderError


def ensure_configured(model: str = "gpt-4.1-mini") -> None:
    """Fail early with setup guidance before a public Memory call uses an LLM.

    This check validates local configuration only; credentials are verified
    when the request is made.
    """
    if not str(model or "").strip():
        raise ProviderError("model cannot be empty")
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ProviderError("OPENAI_API_KEY is required for this operation")


def _resolve_provider(model: str) -> tuple[str, str, str]:
    """Resolve the standard OpenAI-compatible endpoint and model ID."""
    ensure_configured(model)
    return (
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        os.environ["OPENAI_API_KEY"],
        model,
    )


def _http_llm(
    prompt: str,
    timeout: int,
    temperature: float = 0.0,
    model: str = "gpt-4.1-mini",
    *,
    response_format: dict | None = None,
    reasoning_effort: str | None = None,
) -> str:
    base, key, name = _resolve_provider(model)
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": prompt}],
    }
    if re.match(r"gpt-[56]|o\d", name):
        # Per-call override; defaults preserve existing callers, including Compile.
        payload["max_completion_tokens"] = 16000
        effort = reasoning_effort if reasoning_effort is not None else (
            "medium" if name.startswith("gpt-6") else "low")
        payload["reasoning_effort"] = effort
        if effort == "none":
            payload["temperature"] = temperature
    else:
        if reasoning_effort is not None:
            raise ProviderError("reasoning_effort is only supported for reasoning models")
        payload["temperature"] = temperature
        payload["max_tokens"] = 16000
    if response_format is not None:
        payload["response_format"] = response_format
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    call_id = uuid.uuid4().hex
    _record_call("request", call_id, request=payload)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except Exception as error:
        details = {}
        if isinstance(error, urllib.error.HTTPError) and os.environ.get("KITE_LLM_LOG"):
            details = _http_error_details(error, key)
        _record_call("error", call_id, error_type=type(error).__name__, **details)
        raise
    _record_call("response", call_id, response=data)
    if os.environ.get("KITE_LLM_RETRIES") == "0" and data["choices"][0].get("finish_reason") != "stop":
        raise ProviderError("incomplete model output; strict single-call mode")
    msg = data["choices"][0]["message"]
    out = (msg.get("content") or "").strip()
    if not out and msg.get("reasoning_content"):
        out = msg["reasoning_content"].strip()
    return out


def _http_error_details(error, secret: str) -> dict:
    """Keep bounded diagnostic bodies without logging the credential."""
    limit = 8192
    details = {"http_status": error.code}
    try:
        # Padding lets redaction see a credential crossing the log boundary.
        raw = error.read(limit + len(secret.encode("utf-8")) + 1)
        body = raw.decode("utf-8", "replace")
        if secret:
            body = body.replace(secret, "[REDACTED]")
        details.update(error_body=body[:limit],
                       error_body_truncated=len(raw) > limit or len(body) > limit)
    except Exception as read_error:
        details["error_body_read_error"] = type(read_error).__name__
    return details


def _record_call(event, call_id, **values):
    """Opt-in raw receipts; never logs headers/credentials or changes prompts."""
    path = os.environ.get("KITE_LLM_LOG")
    if path:
        record = {"event": event, "call_id": call_id, "item": os.environ.get("KITE_LLM_ITEM", ""),
                  "phase": os.environ.get("KITE_LLM_PHASE", ""), **values}
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def llm(
    prompt: str,
    model: str = "gpt-4.1-mini",
    timeout: int = 300,
    retries: int = 2,
    temperature: float = 0.0,
    *,
    response_format: dict | None = None,
    reasoning_effort: str | None = None,
) -> str:
    retries = int(os.environ.get("KITE_LLM_RETRIES", retries))
    last = None
    for attempt in range(retries + 1):
        try:
            extra = {"response_format": response_format} if response_format is not None else {}
            if reasoning_effort is not None:
                extra["reasoning_effort"] = reasoning_effort
            out = _http_llm(prompt, timeout, temperature, model, **extra)
            if out:
                return out
        except Exception as e:  # HTTP errors, timeouts, rate limits
            last = e
        # No sleep after the final attempt: there is nothing left to wait for,
        # and the caller pays the backoff only to receive the same error.
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise ProviderError(f"llm failed: {last}")


def _balanced_json_candidates(text: str):
    """Yield balanced top-level {...}/[...] substrings, last first (models often
    narrate before emitting the real JSON)."""
    spans = []
    stack = []
    start = None
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            if not stack:
                start = i
            stack.append(ch)
        elif ch in "]}":
            if stack and ((stack[-1] == "[" and ch == "]") or (stack[-1] == "{" and ch == "}")):
                stack.pop()
                if not stack and start is not None:
                    spans.append(text[start : i + 1])
                    start = None
            else:
                stack.clear()
                start = None
    return reversed(spans)


def llm_json(
    prompt: str,
    model: str = "gpt-4.1-mini",
    retries: int = 2,
    temperature: float = 0.0,
    *,
    response_format: dict | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    retries = int(os.environ.get("KITE_LLM_RETRIES", retries))
    last = ""
    for _ in range(retries + 1):
        extra = {"response_format": response_format} if response_format is not None else {}
        if reasoning_effort is not None:
            extra["reasoning_effort"] = reasoning_effort
        last = llm(prompt, model, temperature=temperature, **extra)
        text = re.sub(r"^```(json)?|```$", "", last.strip(), flags=re.M).strip()
        for cand in _balanced_json_candidates(text):
            try:
                parsed = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        # repair fallback: truncated JSON — salvage the "answer" field so one
        # long generation doesn't void the whole question
        m = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)', text)
        if m and retries > 0:
            return {"answer": m.group(1), "evidence": [], "_repaired": True}
    raise ProviderError(f"no JSON in llm output: {last[:300]}")
