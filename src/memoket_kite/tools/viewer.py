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
from urllib.parse import parse_qs, urlencode

from .context import context_enabled, context_pack
from .contracts import ToolError
from .retrieve import memory_snapshot
from .workspace import Workspace

IDLE_TIMEOUT = 3600


def _search_snapshot(workspace: Workspace, terms: list[str]) -> dict:
    passages = {}
    sources = {}
    evidence = {}
    for term in terms:
        pack = context_pack(workspace, {"terms": [term]}, limit=4, max_bytes=3600, neighbors=1)
        for span in pack["spans"]:
            lines = span["evidence"]
            first, last = lines[0], lines[-1]
            checksum = first["revision_checksum"]
            sources[checksum] = {
                "checksum": checksum,
                "reference": first["source_reference"],
                "title": span["title"],
                "day": span["source_date"],
            }
            ids = tuple(line["source_line_id"] for line in lines)
            for line in lines:
                evidence[line["source_line_id"]] = line
            citation = f"{first['conversation_id']}#{first['segment_id']}"
            if len(lines) > 1:
                citation += f"–{last['segment_id']}"
            passage = passages.setdefault(ids, {"label": citation, "evidence": ids, "terms": []})
            passage["terms"].append(term)
    return {
        "version": workspace.version(),
        "sources": workspace.db.execute("SELECT count(*) FROM revisions WHERE active=1").fetchone()[
            0
        ],
        "topics": [],
        "facts": [],
        "states": {},
        "revisions": list(sources.values()),
        "evidence": evidence,
        "search_available": True,
        "search_terms": terms,
        "passages": list(passages.values()),
    }


def _snapshot(directory: Path, terms: list[str]) -> dict:
    try:
        with Workspace.open(directory, read_only=True) as workspace:
            searchable = context_enabled(workspace.db)
            if terms and searchable:
                return _search_snapshot(workspace, terms)
            return {**memory_snapshot(workspace), "search_available": searchable}
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


def _read_comparison(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        examples = raw["examples"]
        if not isinstance(examples, list) or not 1 <= len(examples) <= 10:
            raise ValueError("invalid comparison size")
        for example in examples:
            if not all(isinstance(example[key], str) for key in ("name", "title", "description")):
                raise ValueError("invalid comparison text")
            for phase in ("before", "after"):
                frame = example[phase]
                if not isinstance(frame["version"], int) or not isinstance(frame["evidence"], dict):
                    raise ValueError("invalid snapshot")
                if not all(
                    isinstance(frame[key], list) for key in ("topics", "passages", "revisions")
                ):
                    raise ValueError("invalid snapshot lists")
        return {"comparison": examples}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ToolError("invalid_input", "could not read graph comparison snapshots") from exc


def _serve(directory: Path, comparison: Path | None = None) -> None:
    assets = Path(__file__).parent
    comparison_data = _read_comparison(comparison) if comparison is not None else None
    template = (assets / "memory_view.html").read_text(encoding="utf-8")
    for marker, asset in (
        ("__KITE_STYLE__", "memory_view.css"),
        ("__KITE_SCENE__", "memory_scene.js"),
    ):
        template = template.replace(marker, (assets / asset).read_text(encoding="utf-8"), 1)
    templates = {
        view: template.replace("__KITE_SCRIPT__", (assets / asset).read_text(encoding="utf-8"), 1)
        for view, asset in (("graph", "memory_view.js"), ("changes", "memory_compare.js"))
        if view == "graph" or comparison_data is not None
    }
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
            path, _, query = self.path.partition("?")
            if path != route or self.headers.get("Host") != host:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            last_access = monotonic()
            try:
                params = parse_qs(query, max_num_fields=12)
                terms = list(dict.fromkeys(params.get("term", ())))
                if len(terms) > 3 or any(len(term) > 128 for term in terms):
                    raise ValueError("invalid terms")
                requested_view = params.get("view", [None])[0]
                if comparison_data is not None and (
                    requested_view == "changes" or (requested_view is None and not terms)
                ):
                    view, snapshot = "changes", comparison_data
                else:
                    view, snapshot = "graph", _snapshot(directory, terms)
                    if comparison_data is not None:
                        snapshot["comparison_available"] = True
            except (ValueError, ToolError) as exc:
                if isinstance(exc, ValueError) or exc.code == "invalid_input":
                    self.send_error(HTTPStatus.BAD_REQUEST, "Use up to three search phrases")
                else:
                    self.send_error(
                        HTTPStatus.SERVICE_UNAVAILABLE, "KITE could not read the workspace"
                    )
                return
            except (OSError, sqlite3.Error):
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "KITE could not read the workspace")
                return
            # Escape transcript text before embedding JSON in a script element.
            data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace(
                "<", "\\u003c"
            )
            nonce = token_urlsafe(18)
            html = (
                templates[view].replace("__KITE_NONCE__", nonce).replace("__KITE_DATA__", data, 1)
            )
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


def view_memory(
    directory: Path,
    *,
    open_browser: bool,
    terms: list[str] | None = None,
    comparison: Path | None = None,
) -> dict:
    if terms and (len(terms) > 3 or any(not term.strip() or len(term) > 128 for term in terms)):
        raise ToolError("invalid_input", "use up to three nonempty search phrases")
    command = [sys.executable, "-m", __name__, str(directory.expanduser().resolve())]
    if comparison is not None:
        comparison = comparison.expanduser().resolve()
        _read_comparison(comparison)
        command.append(str(comparison))
    child = subprocess.Popen(
        command,
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
    if terms:
        url += "?" + urlencode({"term": terms}, doseq=True)

    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except (OSError, webbrowser.Error):
            pass
    return {"url": url, "opened": opened, "idle_timeout_seconds": IDLE_TIMEOUT}


if __name__ == "__main__":
    _serve(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
