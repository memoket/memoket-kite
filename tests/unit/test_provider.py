import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from threading import Thread
from urllib.error import HTTPError

import pytest

from memoket_kite.providers.llm import ensure_configured, llm, llm_json


def test_provider_preflight_requires_the_standard_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        ensure_configured()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    ensure_configured("gpt-4.1-mini")


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


@pytest.mark.parametrize(("status", "attempts"), [(400, 1), (401, 1), (403, 1), (429, 3), (503, 3)])
def test_provider_retries_only_retryable_http_failures(monkeypatch, status, attempts):
    calls = []
    sleeps = []

    def fail(*args):
        calls.append(args)
        raise HTTPError("https://example.invalid", status, "private provider diagnostic", {}, None)

    monkeypatch.setattr("memoket_kite.providers.llm._http_llm", fail)
    monkeypatch.setattr("memoket_kite.providers.llm.time.sleep", sleeps.append)
    with pytest.raises(RuntimeError, match=rf"^llm failed: HTTP {status}$"):
        llm("synthetic prompt", retries=2)
    assert len(calls) == attempts
    assert sleeps == [2, 4][: attempts - 1]


@pytest.mark.parametrize("status", [302, 307])
def test_provider_does_not_follow_or_retry_model_redirects(status):
    paths = Queue()

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            paths.put(self.path)
            self.send_response(status)
            self.send_header("Location", "/unexpected-destination")
            self.end_headers()

        def do_GET(self):
            paths.put(self.path)
            self.send_error(404)

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Endpoint) as endpoint:
        worker = Thread(target=endpoint.serve_forever, daemon=True)
        worker.start()
        try:
            environment = {
                **os.environ,
                "OPENAI_API_KEY": "synthetic-test-key",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{endpoint.server_port}/v1",
                "no_proxy": "127.0.0.1",
            }
            process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from memoket_kite.providers.llm import llm; llm('synthetic')",
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            assert process.returncode == 1
            assert f"HTTP {status}. Set OPENAI_BASE_URL" in process.stderr
            assert "unexpected-destination" not in process.stderr
            assert "synthetic-test-key" not in process.stderr
            assert paths.get(timeout=1) == "/v1/chat/completions"
            assert paths.empty()
        finally:
            endpoint.shutdown()
            worker.join(timeout=5)


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
