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

from .activity import memory_activity
from .context import _tokens, context_enabled, context_pack, question_pack
from .contracts import ToolError
from .ingest import RECORDING_CLOCK_FIELDS
from .retrieve import compare_revisions, memory_snapshot
from .workflow import saved_context_search, source_revision_page
from .workspace import Workspace

IDLE_TIMEOUT = 3600


def _search_snapshot(
    workspace: Workspace,
    terms: list[str],
    question: str | None,
    scope: dict,
    *,
    saved: dict | None = None,
) -> dict:
    passages = {}
    sources = {}
    evidence = {}
    quote_occurrences = 0
    comparison = []
    if question is not None:
        result = (
            saved["value"]
            if saved is not None
            else question_pack(workspace, {"question": question, **scope})
        )
        if "parts" in result:
            packs = [(f"question-{i}", part) for i, part in enumerate(result["parts"], 1)]
            comparison = [
                {
                    "key": key,
                    **part["compilation"],
                    "passages": len(part["spans"]),
                    "quote_bytes": part["quote_bytes"],
                    "quote_budget_bytes": part["quote_budget_bytes"],
                }
                for key, part in packs
            ]
        else:
            packs = [(question, result)]
        terms = [key for key, _ in packs]
    else:
        packs = [
            (
                term,
                context_pack(
                    workspace,
                    {"terms": [term], **scope},
                    limit=4,
                    max_bytes=3600,
                    neighbors=0 if scope.get("matching_lines") else 1,
                ),
            )
            for term in terms
        ]
    for term, pack in packs:
        compiled = [
            (phrase, f" {tokens} ")
            for phrase in pack["plan"]["terms"]
            if (tokens := _tokens(phrase))
        ]
        for span in pack["spans"]:
            lines = span["evidence"]
            quote_occurrences += len(lines)
            first, last = lines[0], lines[-1]
            checksum = first["revision_checksum"]
            sources[checksum] = {
                "checksum": checksum,
                "reference": first["source_reference"],
                "title": span["title"],
                "day": span["source_date"],
                **{key: span[key] for key in RECORDING_CLOCK_FIELDS if key in span},
                "active": span.get("source_revision_active", True),
            }
            ids = tuple(line["source_line_id"] for line in lines)
            for line in lines:
                quote = evidence.setdefault(line["source_line_id"], {**line, "matched_terms": []})
                text = f" {_tokens(line['text'])} "
                quote["matched_terms"].extend(
                    phrase
                    for phrase, tokens in compiled
                    if tokens in text and phrase not in quote["matched_terms"]
                )
            citation = f"{first['conversation_id']}#{first['segment_id']}"
            if len(lines) > 1:
                citation += f"–{last['segment_id']}"
            passage = passages.setdefault(ids, {"label": citation, "evidence": ids, "terms": []})
            passage["terms"].append(term)
    history = packs[0][1].get("history")
    if saved is not None and sources:
        retained = {
            row["checksum"]: bool(row["active"])
            for row in workspace.db.execute(
                f"SELECT checksum, active FROM revisions WHERE checksum IN ({','.join('?' for _ in sources)})",
                tuple(sources),
            )
        }
        if retained.keys() != sources.keys():
            raise ToolError("integrity_error", "saved search cites an unavailable source revision")
        for checksum, active in retained.items():
            sources[checksum]["active"] = active
    if history is not None:
        source_count = history["retained_sources"]
    elif saved is not None:
        version = saved["manifest"]["memory_version"]
        source_count = workspace.db.execute(
            "SELECT count(*) FROM revision_visibility WHERE opened_version<=? "
            "AND (closed_version IS NULL OR closed_version>?)",
            (version, version),
        ).fetchone()[0]
    else:
        source_count = workspace.db.execute(
            "SELECT count(*) FROM revisions WHERE active=1"
        ).fetchone()[0]
    return {
        "version": workspace.version(),
        "sources": source_count,
        "topics": [],
        "facts": [],
        "states": {},
        "revisions": list(sources.values()),
        "evidence": evidence,
        "search_available": True,
        "search_terms": terms,
        "search_question": question,
        "search_comparison": comparison,
        "search_history": history,
        "search_matching_lines": packs[0][1]["plan"].get("matching_lines", False),
        "search_recording_bounds": {
            key: packs[0][1]["plan"][key]
            for key in ("start_date", "end_date")
            if key in packs[0][1]["plan"]
        },
        "search_trace": {
            "terms": packs[0][1]["compilation"]["terms"] if question is not None else terms,
            "candidate_spans": sum(pack["candidate_spans"] for _, pack in packs),
            "candidate_sources": packs[0][1]["candidate_sources"]
            if question and not comparison
            else None,
            "anchor_sources": packs[0][1].get("anchor_sources", 0)
            if question and not comparison
            else 0,
            "quote_bytes": sum(pack["quote_bytes"] for _, pack in packs),
            "sources": len(sources),
            "quotes": len(evidence),
            **({"shared_quotes": quote_occurrences - len(evidence)} if comparison else {}),
        },
        "passages": list(passages.values()),
    }


def _snapshot(
    directory: Path,
    terms: list[str],
    question: str | None = None,
    at_version: int | None = None,
    *,
    recording_bounds: dict | None = None,
    compare_with: str | None = None,
    matching_lines: bool = False,
    run_id: str | None = None,
) -> dict:
    try:
        with Workspace.open(directory, read_only=True) as workspace:
            searchable = context_enabled(workspace.db)
            tracked = workspace.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='memory_versions'"
            ).fetchone()
            origin = (
                workspace.db.execute("SELECT min(version) FROM memory_versions").fetchone()[0]
                if tracked
                else None
            )
            if run_id is not None:
                saved = saved_context_search(workspace, run_id)
                pack = saved["value"]
                first = pack["parts"][0] if "parts" in pack else pack
                return {
                    **_search_snapshot(
                        workspace, [], first["compilation"]["question"], {}, saved=saved
                    ),
                    "history_origin": origin,
                    "saved_search": {
                        "version": saved["manifest"]["memory_version"],
                        "searched_at": saved["manifest"]["created_at"],
                    },
                }
            historical_search = at_version is not None and origin is not None
            if (terms or question is not None) and (searchable or at_version is not None):
                scope = {**(recording_bounds or {})}
                if at_version is not None:
                    scope["at_version"] = at_version
                if compare_with is not None:
                    scope["compare_with"] = compare_with
                if matching_lines:
                    scope["matching_lines"] = True
                return {
                    **_search_snapshot(workspace, terms, question, scope),
                    "history_origin": origin,
                }
            return {
                **memory_snapshot(workspace),
                "search_available": searchable or historical_search,
                "history_origin": origin,
            }
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


def _activity_snapshot(directory: Path, event_version: int | None = None, offset: int = 0) -> dict:
    try:
        with Workspace.open(directory, read_only=True) as workspace:
            return memory_activity(workspace, event_version=event_version, offset=offset)
    except ToolError as exc:
        if (
            exc.code != "not_found"
            or (directory.exists() and not directory.is_dir())
            or (directory / "kite.sqlite3").exists()
        ):
            raise
        return {"version": 0, "events": [], "runs": []}


def _version_parameter(params: dict, name: str) -> int | None:
    value = params.get(name, [None])[0]
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 19 or int(value) >= 2**63:
        raise ValueError("invalid workspace version")
    return int(value)


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
        for view, asset in (
            ("graph", "memory_view.js"),
            ("activity", "memory_activity.js"),
            ("changes", "memory_compare.js"),
            ("revision", "memory_revision.js"),
            ("source", "memory_source.js"),
        )
        if view != "changes" or comparison_data is not None
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
                question = params.get("q", [None])[0]
                compare_with = params.get("q2", [None])[0]
                run_id = params.get("run", [None])[0]
                matching_lines = params.get("matching_lines", ["0"])
                if (
                    len(terms) > 3
                    or matching_lines not in (["0"], ["1"])
                    or any(len(term) > 128 for term in terms)
                    or (question is not None and (len(question) > 512 or terms))
                    or (compare_with is not None and (question is None or len(compare_with) > 512))
                    or (
                        run_id is not None
                        and (
                            terms
                            or question is not None
                            or any(
                                key in params
                                for key in ("at", "start_date", "end_date", "matching_lines")
                            )
                        )
                    )
                ):
                    raise ValueError("invalid terms")
                requested_view = params.get("view", [None])[0]
                if requested_view == "activity":
                    view, snapshot = (
                        "activity",
                        _activity_snapshot(
                            directory,
                            _version_parameter(params, "event"),
                            _version_parameter(params, "offset") or 0,
                        ),
                    )
                elif requested_view == "revision":
                    request = {"before": params.get("before", [""])[0]}
                    if after := params.get("after"):
                        request["after"] = after[0]
                    if cursor := params.get("cursor"):
                        request["cursor"] = cursor[0]
                    with Workspace.open(directory, read_only=True) as workspace:
                        view, snapshot = "revision", compare_revisions(workspace, request)
                elif requested_view == "source":
                    with Workspace.open(directory, read_only=True) as workspace:
                        view, snapshot = (
                            "source",
                            source_revision_page(
                                workspace,
                                params.get("checksum", [""])[0],
                                offset=_version_parameter(params, "offset") or 0,
                            ),
                        )
                elif comparison_data is not None and (
                    requested_view == "changes"
                    or (requested_view is None and not terms and not question and run_id is None)
                ):
                    view, snapshot = "changes", comparison_data
                else:
                    at_version = _version_parameter(params, "at")
                    bounds = {
                        key: params[key][0] for key in ("start_date", "end_date") if key in params
                    }
                    view, snapshot = (
                        "graph",
                        _snapshot(
                            directory,
                            terms,
                            question,
                            at_version,
                            recording_bounds=bounds,
                            compare_with=compare_with,
                            matching_lines=matching_lines == ["1"],
                            run_id=run_id,
                        ),
                    )
                if comparison_data is not None:
                    snapshot = {**snapshot, "comparison_available": True}
            except (ValueError, ToolError) as exc:
                if isinstance(exc, ValueError):
                    self.send_error(HTTPStatus.BAD_REQUEST, "Invalid memory view parameters")
                elif exc.code == "invalid_input":
                    self.send_error(HTTPStatus.BAD_REQUEST, explain=str(exc))
                elif exc.code == "not_found":
                    self.send_error(
                        HTTPStatus.NOT_FOUND,
                        "The requested search, change or source revision was not found",
                    )
                elif exc.code == "invalidated_run":
                    self.send_error(
                        HTTPStatus.GONE, "A permanent deletion removed this saved search"
                    )
                elif exc.code == "history_unavailable":
                    self.send_error(HTTPStatus.NOT_FOUND, "This workspace version was not tracked")
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
    run_id: str | None = None,
) -> dict:
    if terms and (len(terms) > 3 or any(not term.strip() or len(term) > 128 for term in terms)):
        raise ToolError("invalid_input", "use up to three nonempty search phrases")
    if run_id is not None:
        with Workspace.open(directory, read_only=True) as workspace:
            saved_context_search(workspace, run_id)
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
    if run_id is not None:
        url += "?" + urlencode({"view": "graph", "run": run_id})

    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(url)
        except (OSError, webbrowser.Error):
            pass
    return {"url": url, "opened": opened, "idle_timeout_seconds": IDLE_TIMEOUT}


if __name__ == "__main__":
    _serve(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
