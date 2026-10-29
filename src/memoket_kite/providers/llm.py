"""OpenAI-compatible HTTP client for KITE's LLM boundaries."""

import json
import os
import re
import time
import urllib.request

from memoket_kite.errors import ProviderError


def ensure_configured(model: str = "gpt-4.1-mini") -> None:
    """Fail early with setup guidance before a public Memory call uses an LLM.

    This check validates local configuration only; credentials are verified
    when the request is made.
    """
    if not str(model or "").strip():
        raise ProviderError("model cannot be empty")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ProviderError("OPENAI_API_KEY is required for this operation")
    if "\r" in key or "\n" in key:
        raise ProviderError("OPENAI_API_KEY must be a single line")


def _resolve_provider(model: str) -> tuple[str, str, str]:
    """Resolve the standard OpenAI-compatible endpoint and model ID."""
    ensure_configured(model)
    return (
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        os.environ["OPENAI_API_KEY"].strip(),
        model,
    )


def _http_llm(
    prompt: str,
    timeout: int,
    temperature: float = 0.0,
    model: str = "gpt-4.1-mini",
) -> str:
    base, key, name = _resolve_provider(model)
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": prompt}],
    }
    if re.match(r"gpt-5|o\d", name):
        # OpenAI reasoning models: max_completion_tokens, fixed temperature
        payload["max_completion_tokens"] = 16000
        payload["reasoning_effort"] = "low"
    else:
        payload["temperature"] = temperature
        payload["max_tokens"] = 16000
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    msg = data["choices"][0]["message"]
    out = (msg.get("content") or "").strip()
    if not out and msg.get("reasoning_content"):
        out = msg["reasoning_content"].strip()
    return out


def llm(
    prompt: str,
    model: str = "gpt-4.1-mini",
    timeout: int = 300,
    retries: int = 2,
    temperature: float = 0.0,
) -> str:
    last = None
    for attempt in range(retries + 1):
        try:
            out = _http_llm(prompt, timeout, temperature, model)
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
) -> dict:
    last = ""
    for _ in range(retries + 1):
        last = llm(prompt, model, temperature=temperature)
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
        if m:
            return {"answer": m.group(1), "evidence": [], "_repaired": True}
    raise ProviderError(f"no JSON in llm output: {last[:300]}")
