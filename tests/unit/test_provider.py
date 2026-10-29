import os
import subprocess
import sys

import pytest

from memoket_kite.providers.llm import ensure_configured, llm, llm_json


def test_provider_preflight_requires_the_standard_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ensure_configured()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    ensure_configured("gpt-4.1-mini")


def test_provider_reads_the_api_key_without_surrounding_whitespace(monkeypatch):
    from memoket_kite.providers.llm import _resolve_provider

    monkeypatch.setenv("OPENAI_API_KEY", " test-key\r\n")
    assert _resolve_provider("gpt-4.1-mini")[1] == "test-key"


def test_provider_preflight_rejects_a_multi_line_api_key_without_echoing_it(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-one\ntest-key-two")
    with pytest.raises(RuntimeError, match="single line") as raised:
        ensure_configured("gpt-4.1-mini")
    assert "test-key" not in str(raised.value)


def test_model_uses_the_openai_compatible_http_provider(monkeypatch):
    monkeypatch.setattr("memoket_kite.providers.llm._http_llm", lambda *args: "ok")
    assert llm("test prompt", model="gpt-4.1-mini") == "ok"


def test_provider_retries_only_between_attempts(monkeypatch):
    calls = []
    sleeps = []

    def fail(*args):
        calls.append(args)
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr("memoket_kite.providers.llm._http_llm", fail)
    monkeypatch.setattr("memoket_kite.providers.llm.time.sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        llm("test prompt", model="gpt-4.1-mini", retries=2)

    assert len(calls) == 3
    assert sleeps == [2, 4]

    calls.clear()
    sleeps.clear()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        llm("test prompt", model="gpt-4.1-mini", retries=0)
    assert len(calls) == 1
    assert sleeps == []


def test_llm_json_retries_non_object_json(monkeypatch):
    outputs = iter(("[]", "42", '{"answer": "ok"}'))
    monkeypatch.setattr("memoket_kite.providers.llm.llm", lambda *args, **kwargs: next(outputs))

    assert llm_json("prompt", retries=2) == {"answer": "ok"}


def test_llm_json_rejects_a_top_level_array(monkeypatch):
    monkeypatch.setattr("memoket_kite.providers.llm.llm", lambda *args, **kwargs: "[]")

    with pytest.raises(RuntimeError, match="no JSON"):
        llm_json("prompt", retries=0)


def test_import_has_no_output_or_environment_side_effect(tmp_path):
    marker = tmp_path / ".env"
    marker.write_text('export OPENAI_API_KEY="should-not-load"\n', encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, memoket_kite; print(os.environ.get('OPENAI_API_KEY', 'absent'))",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert process.stdout == "absent\n"
    assert process.stderr == ""
