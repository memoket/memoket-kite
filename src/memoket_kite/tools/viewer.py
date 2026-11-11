"""Serve a read-only memory graph on an ephemeral loopback address."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from time import monotonic

from .contracts import ToolError
from .retrieve import memory_snapshot
from .workspace import Workspace

IDLE_TIMEOUT = 3600


def _snapshot(directory: Path) -> dict:
    try:
        with Workspace.open(directory, read_only=True) as workspace:
            return memory_snapshot(workspace)
    except ToolError as exc:
        if (
            exc.code != "not_found"
            or (directory.exists() and not directory.is_dir())
            or (directory / "kite.sqlite3").exists()
        ):
            raise
        return {
            "version": 0,
            "sources": 0,
            "topics": [],
            "facts": [],
            "states": {},
            "revisions": [],
            "evidence": {},
        }


def _serve(directory: Path) -> None:
    template = Path(__file__).with_name("memory_view.html").read_text(encoding="utf-8")
    route = f"/{token_urlsafe(24)}/"
    last_access = monotonic()

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(5)
            super().setup()

        def log_message(self, format, *args) -> None:
            pass

        def do_GET(self) -> None:
            nonlocal last_access
            if self.path.partition("?")[0] != route or self.headers.get("Host") != host:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            last_access = monotonic()
            try:
                snapshot = _snapshot(directory)
            except (ToolError, OSError, sqlite3.Error):
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "KITE could not read the workspace")
                return
            # Escape transcript text before embedding JSON in a script element.
            data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace(
                "<", "\\u003c"
            )
            nonce = token_urlsafe(18)
            html = template.replace("__KITE_NONCE__", nonce).replace("__KITE_DATA__", data, 1)
            body = html.encode("utf-8")
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        host = f"127.0.0.1:{server.server_port}"
        print(f"http://{host}{route}", flush=True)
        server.timeout = 1
        while monotonic() - last_access < IDLE_TIMEOUT:
            server.handle_request()


def view_memory(directory: Path, *, open_browser: bool) -> dict:
    child = subprocess.Popen(
        [sys.executable, "-m", __name__, str(directory.expanduser().resolve())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    assert child.stdout is not None
    with child.stdout as stream:
        url = stream.readline().strip()
    if not url.startswith("http://127.0.0.1:"):
        child.wait()
        raise ToolError(
            "viewer_unavailable", "could not start the local viewer; check loopback access"
        )

    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except (OSError, webbrowser.Error):
            pass
    return {"url": url, "opened": opened, "idle_timeout_seconds": IDLE_TIMEOUT}


if __name__ == "__main__":
    _serve(Path(sys.argv[1]))
