"""Transactional source, Codebook, artifact and checkpoint persistence."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager, suppress
from hashlib import sha256
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from .contracts import (
    CURSOR,
    TEXT,
    ToolError,
    decode_object,
    digest,
    encode,
    now,
    page_cursor,
    page_offset,
    validate,
)
from .index import Index, build_index, fact_entries, load_index, revision_id, source_key
from .ingest import source_day, source_identity
from .timing import precedes, source_timing

_DELETION_IMPACT_ID_LIMIT = 100

_DDL = """
CREATE TABLE IF NOT EXISTS memory (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    workspace_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    merges TEXT NOT NULL,
    xml BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_hierarchy (
    topic TEXT PRIMARY KEY,
    parents TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS index_seal (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_seal (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    checksum TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    source TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    active INTEGER NOT NULL,
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS active_source ON revisions(source_key) WHERE active=1;
CREATE INDEX IF NOT EXISTS revisions_by_conversation
    ON revisions(source, conversation_id, active, checksum);
CREATE TABLE IF NOT EXISTS tombstones (scope TEXT NOT NULL, key TEXT NOT NULL,
    PRIMARY KEY (scope, key));
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, definition TEXT NOT NULL, inputs TEXT NOT NULL, checkpoint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL, kind TEXT NOT NULL, digest TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (run_id, stage)
);
CREATE TABLE IF NOT EXISTS deletion_audit (
    operation TEXT PRIMARY KEY, version INTEGER NOT NULL, revision_count INTEGER NOT NULL,
    fact_count INTEGER NOT NULL, committed_at TEXT NOT NULL
);
"""


def _snapshot_checksums(
    xml: bytes | None, merges: str, rows: list[sqlite3.Row], hierarchy: dict
) -> tuple[str | None, str]:
    if not isinstance(merges, str) or (xml is not None and not isinstance(xml, bytes)):
        raise ToolError("integrity_error", "invalid stored Codebook snapshot")
    combined = sha256() if xml is not None else None
    sources = sha256()

    def frame(value: bytes, *checksums) -> None:
        length = len(value).to_bytes(8, "big")
        for checksum in checksums:
            checksum.update(length)
            checksum.update(value)

    # Change the seal format when the Codebook compiler's persisted output changes.
    if combined is not None:
        frame(b"kite-codebook-v1", combined)
        frame(xml, combined)
    frame(b"kite-sources-v1", sources)
    checksums = (combined, sources) if combined is not None else (sources,)
    frame(merges.encode("utf-8"), *checksums)
    if hierarchy:
        frame(b"topic-hierarchy", *checksums)
        frame(encode(hierarchy).encode("utf-8"), *checksums)
    try:
        for row in rows:
            frame(encode(list(row)).encode("utf-8"), *checksums)
    except (TypeError, ValueError) as exc:
        raise ToolError("integrity_error", "invalid stored source record") from exc
    return combined.hexdigest() if combined is not None else None, sources.hexdigest()


def _target_ids(target: dict) -> list[str]:
    if "conversation_ids" in target:
        ids = target["conversation_ids"]
        if "conversation_id" in target or not isinstance(ids, list) or not ids:
            raise ToolError("invalid_input", "select a non-empty list of conversation IDs")
        return sorted(ids)
    if "conversation_id" in target:
        return [target["conversation_id"]]
    return []


def _input_sources(raw: str) -> Iterator[tuple]:
    try:
        supplied = json.loads(raw)
    except (ValueError, RecursionError):
        return
    if isinstance(supplied, dict):
        for value in supplied.values():
            if isinstance(value, dict) and {"format", "payload"} <= value.keys():
                yield source_identity(value)


class Workspace:
    def __init__(
        self,
        db: sqlite3.Connection,
        directory: Path,
        *,
        read_only: bool = False,
        has_source_seal: bool = True,
    ):
        self.db = db
        self.directory = directory
        self.read_only = read_only
        self.has_source_seal = has_source_seal
        self._snapshot_index: Index | None = None

    @classmethod
    @contextmanager
    def open(cls, directory: Path, *, read_only: bool = False):
        directory = directory.expanduser().resolve()
        if read_only:
            if not directory.is_dir():
                raise ToolError("not_found", "workspace directory was not found")
        else:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix" and (directory.stat().st_mode & 0o077):
            raise ToolError(
                "permission_denied", "workspace directory must be owner-only (chmod 700)"
            )
        path = directory / "kite.sqlite3"
        if path.is_symlink():
            raise ToolError("permission_denied", "workspace database cannot be a symbolic link")
        if read_only:
            if not path.is_file():
                raise ToolError("not_found", "workspace database was not found")
        else:
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
        if os.name == "posix" and (path.stat().st_mode & 0o077):
            raise ToolError("permission_denied", "workspace database must be owner-only")
        location = path.as_uri() + "?mode=ro" if read_only else path
        db = sqlite3.connect(location, timeout=15, uri=read_only)
        db.row_factory = sqlite3.Row
        try:
            if read_only:
                db.execute("BEGIN")
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not {"memory", "revisions"} <= tables:
                    raise ToolError("integrity_error", "workspace is missing its memory tables")
                if not db.execute("SELECT 1 FROM memory WHERE singleton=1").fetchone():
                    raise ToolError("integrity_error", "workspace is missing its memory snapshot")
                if not {"topic_hierarchy", "index_seal"} <= tables:
                    raise ToolError(
                        "workspace_upgrade_required",
                        "run kite-tool integrity to upgrade the workspace",
                    )
                yield cls(db, directory, read_only=True, has_source_seal="source_seal" in tables)
                return
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("PRAGMA temp_store=MEMORY")
            db.executescript(_DDL)
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM memory WHERE singleton=1").fetchone():
                db.execute(
                    "INSERT INTO memory VALUES(1, ?, 0, '{}', ?)",
                    (uuid4().hex, build_index([], {}).xml),
                )
            workspace = cls(db, directory)
            if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                workspace._scrub_deleted_runs()
                db.execute("PRAGMA user_version=1")
                db.commit()
                db.execute("BEGIN IMMEDIATE")
            yield workspace
            db.commit()
        finally:
            db.close()

    @classmethod
    @contextmanager
    def inspect(cls, directory: Path, *, initialize: bool = False):
        """Inspect committed data, recovering or upgrading when needed."""
        with ExitStack() as reader:
            try:
                workspace = reader.enter_context(cls.open(directory, read_only=True))
            except ToolError as exc:
                if exc.code != "workspace_upgrade_required" and (
                    exc.code != "not_found" or not initialize
                ):
                    raise
            except sqlite3.OperationalError as exc:
                # Opening a hot rollback journal requires write access. Python
                # 3.10 exposes only the base SQLite diagnostic.
                reason = getattr(exc, "sqlite_errorname", None)
                if reason != "SQLITE_READONLY_ROLLBACK" and (
                    reason is not None or str(exc) != "attempt to write a readonly database"
                ):
                    raise
            else:
                # Version zero may still hold artifacts for sources already deleted.
                if workspace.db.execute("PRAGMA user_version").fetchone()[0] != 0:
                    yield workspace
                    return
        with cls.open(directory) as workspace:
            yield workspace

    @classmethod
    def backup(
        cls,
        directory: Path,
        destination: Path,
        *,
        verify_runs: Callable[[Workspace], dict],
        require_deletions_from: Path | None = None,
    ) -> dict:
        source = directory.expanduser().resolve()
        if not (source / "kite.sqlite3").is_file():
            raise ToolError("not_found", "source workspace database was not found")
        destination = destination.expanduser().absolute()
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ToolError("conflict", "backup destination already exists") from exc

        try:
            with (
                TemporaryDirectory(prefix=".kite-backup-", dir=destination) as temporary,
                ExitStack() as deletion_guard,
            ):
                staging = Path(temporary)
                database = staging / "kite.sqlite3"
                database.touch(mode=0o600)
                with cls.open(source) as original:
                    # Finish any upgrade, then pin the source in a read transaction.
                    original.db.commit()
                    original.db.execute("BEGIN")
                    original.version()
                    checked_deletions = {}
                    if require_deletions_from is not None:
                        # Keep the authority's read transaction open until publication.
                        authority = deletion_guard.enter_context(
                            cls.open(require_deletions_from, read_only=True)
                        )
                        checked_deletions = original._require_deletion_history(authority)
                    with closing(sqlite3.connect(database, timeout=15)) as copy:
                        original.db.backup(copy)
                with cls.open(staging) as snapshot:
                    snapshot.verify_sqlite()
                    index = snapshot.index(verify=True)
                    verified = verify_runs(snapshot)
                    # The copy is an independent workspace. Authorization to
                    # delete from the original does not transfer to the copy.
                    snapshot.db.execute(
                        "UPDATE memory SET workspace_id=? WHERE singleton=1", (uuid4().hex,)
                    )
                    approvals = snapshot.db.execute(
                        """
                        SELECT id, checkpoint FROM runs WHERE id IN (
                            SELECT run_id FROM artifacts WHERE kind='deletion_plan'
                        )
                        """
                    ).fetchall()
                    for row in approvals:
                        snapshot.db.execute("DELETE FROM artifacts WHERE run_id=?", (row["id"],))
                        snapshot._redact_run(row)
                    result = {
                        "backup": str(destination),
                        "memory_version": snapshot.version(),
                        "revisions": len(index.revisions),
                        "invalidated_approvals": len(approvals),
                        **verified,
                        **checked_deletions,
                    }
                try:
                    os.link(database, destination / database.name)
                except FileExistsError as exc:
                    raise ToolError(
                        "conflict", "backup destination was populated during copying"
                    ) from exc
            return result
        except BaseException:
            with suppress(OSError):
                destination.rmdir()
            raise

    def verify_sqlite(self) -> None:
        if [tuple(row) for row in self.db.execute("PRAGMA quick_check")] != [("ok",)]:
            raise ToolError("integrity_error", "SQLite integrity check failed")
        if self.db.execute("PRAGMA foreign_key_check").fetchone():
            raise ToolError("integrity_error", "workspace contains broken references")

    def _require_deletion_history(self, authority: Workspace) -> dict[str, int]:
        if authority.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
            raise ToolError("invalid_input", "deletion authority must use a rollback journal")
        checked = {}
        for table, field in (
            ("tombstones", "checked_tombstones"),
            ("deletion_audit", "checked_deletion_receipts"),
        ):
            expected = {tuple(row) for row in authority.db.execute(f"SELECT * FROM {table}")}
            actual = {tuple(row) for row in self.db.execute(f"SELECT * FROM {table}")}
            if not expected <= actual:
                raise ToolError("stale_backup", "backup is missing required deletion history")
            checked[field] = len(expected)
        return checked

    @contextmanager
    def provider_slot(self, run_id: str, stage_id: str):
        """Reserve one external attempt without locking the memory database."""
        directory = self.directory / ".provider-locks"
        if directory.is_symlink():
            raise ToolError(
                "permission_denied", "provider lock directory cannot be a symbolic link"
            )
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / digest([run_id, stage_id])
        if path.is_symlink():
            raise ToolError("permission_denied", "provider lock cannot be a symbolic link")
        # This separate SQLite file stores no request data. Its lock also
        # releases if the process is interrupted or killed.
        lock = sqlite3.connect(path, timeout=0)
        try:
            try:
                lock.execute("BEGIN EXCLUSIVE")
            except sqlite3.OperationalError as exc:
                if (
                    getattr(exc, "sqlite_errorname", None) == "SQLITE_BUSY"
                    or str(exc) == "database is locked"
                ):
                    raise ToolError(
                        "provider_busy",
                        "an extraction request is already in progress for this stage",
                    ) from exc
                raise
            yield
        finally:
            lock.close()

    @contextmanager
    def unlocked_for_provider(self):
        """End the read phase before a provider call and start a fresh write phase."""
        self.db.commit()
        try:
            yield
        finally:
            self.db.execute("BEGIN IMMEDIATE")

    def _memory(self):
        return self.db.execute("SELECT * FROM memory WHERE singleton=1").fetchone()

    def version(self) -> int:
        return self.db.execute("SELECT version FROM memory WHERE singleton=1").fetchone()[0]

    def snapshot_identity(self) -> tuple[str, int]:
        row = self.db.execute(
            "SELECT workspace_id, version FROM memory WHERE singleton=1"
        ).fetchone()
        return row["workspace_id"], row["version"]

    def merges(self) -> dict:
        row = self.db.execute("SELECT merges FROM memory WHERE singleton=1").fetchone()
        return json.loads(row["merges"])

    def topic_hierarchy(self) -> dict[str, list[str]]:
        try:
            hierarchy = {
                row["topic"]: json.loads(row["parents"])
                for row in self.db.execute("SELECT topic, parents FROM topic_hierarchy")
            }
        except (TypeError, ValueError) as exc:
            raise ToolError("integrity_error", "invalid stored topic hierarchy") from exc
        if any(
            not isinstance(parents, list) or any(not isinstance(parent, str) for parent in parents)
            for parents in hierarchy.values()
        ):
            raise ToolError("integrity_error", "invalid stored topic hierarchy")
        return hierarchy

    def _revision_rows(
        self,
        *,
        source: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
        checksum: str | None = None,
    ) -> list[sqlite3.Row]:
        filters = {
            "source": source,
            "conversation_id": conversation_id,
            "active": 1 if active_only else None,
            "checksum": checksum,
        }
        filters = {key: value for key, value in filters.items() if value is not None}
        query = "SELECT * FROM revisions"
        if filters:
            query += " WHERE " + " AND ".join(f"{key}=?" for key in filters)
        return self.db.execute(query + " ORDER BY checksum", tuple(filters.values())).fetchall()

    def records(
        self,
        *,
        source: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
    ) -> list[dict]:
        rows = self._revision_rows(
            source=source, conversation_id=conversation_id, active_only=active_only
        )
        return self._records(rows)

    def record(self, checksum: str) -> dict:
        rows = self.db.execute("SELECT * FROM revisions WHERE checksum=?", (checksum,)).fetchall()
        if not rows:
            raise ToolError("not_found", "source revision was not found")
        return self._records(rows)[0]

    def source_revision(self, source: str, conversation_id: str, checksum: str | None) -> dict:
        rows = self._revision_rows(
            source=source,
            conversation_id=conversation_id,
            active_only=checksum is None,
            checksum=checksum,
        )
        if not rows:
            raise ToolError("not_found", "no revision matches the requested conversation")
        if len(rows) != 1:
            raise ToolError("integrity_error", "conversation must have one current revision")
        return self._records(rows)[0]

    @staticmethod
    def _decode_revision(row: sqlite3.Row) -> dict:
        try:
            record = decode_object(row["payload"])
            conversation = record["conversation"]
            mismatched = (
                record["checksum"] != row["checksum"]
                or digest(conversation) != row["checksum"]
                or source_key(conversation) != row["source_key"]
                or conversation["source"] != row["source"]
                or conversation["conversation_id"] != row["conversation_id"]
            )
            segments = conversation["segments"]
            fields = (record["facts"], record["taxonomy"], record["entity_aliases"])
            malformed = (
                not isinstance(segments, list)
                or not segments
                or any(
                    not isinstance(segment, dict)
                    or not isinstance(segment.get("segment_id"), str)
                    or not isinstance(segment.get("text"), str)
                    for segment in segments
                )
                or any(
                    not isinstance(items, list) or any(not isinstance(item, dict) for item in items)
                    for items in fields
                )
            )
        except (ToolError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise ToolError("integrity_error", "invalid stored source record") from exc
        if mismatched:
            raise ToolError("integrity_error", "a source record differs from its stored identity")
        if malformed:
            raise ToolError("integrity_error", "invalid stored source record")
        return record

    def _records(self, rows: list[sqlite3.Row]) -> list[dict]:
        return [{**self._decode_revision(row), "active": bool(row["active"])} for row in rows]

    def _index_seal(self, version: int) -> str | None:
        row = self.db.execute(
            "SELECT checksum FROM index_seal WHERE singleton=1 AND version=?", (version,)
        ).fetchone()
        return row["checksum"] if row else None

    def _seal_index(self, version: int, checksum: str, source_checksum: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO index_seal VALUES(1, ?, ?)", (version, checksum))
        self.db.execute(
            "INSERT OR REPLACE INTO source_seal VALUES(1, ?, ?)", (version, source_checksum)
        )
        if self.db.execute("PRAGMA user_version").fetchone()[0] < 2:
            self.db.execute("PRAGMA user_version=2")

    def _check_source_seal(self, version: int, checksum: str) -> bool:
        row = (
            self.db.execute(
                "SELECT version, checksum FROM source_seal WHERE singleton=1"
            ).fetchone()
            if self.has_source_seal
            else None
        )
        if row is None:
            if self.db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                raise ToolError("integrity_error", "workspace is missing its source snapshot seal")
            return False
        if (row["version"], row["checksum"]) != (version, checksum):
            raise ToolError("integrity_error", "stored source snapshot differs from its seal")
        return True

    def _verify_source_snapshot(
        self, current: sqlite3.Row, rows: list[sqlite3.Row], hierarchy: dict
    ) -> bool:
        _, checksum = _snapshot_checksums(None, current["merges"], rows, hierarchy)
        return self._check_source_seal(current["version"], checksum)

    def _checked_snapshot(
        self, current: sqlite3.Row, rows: list[sqlite3.Row], hierarchy: dict
    ) -> tuple[str, str, bool, bool]:
        if not isinstance(current["xml"], bytes):
            raise ToolError("integrity_error", "invalid stored Codebook snapshot")
        checksum, source_checksum = _snapshot_checksums(
            current["xml"], current["merges"], rows, hierarchy
        )
        version = current["version"]
        source_sealed = self._check_source_seal(version, source_checksum)
        seal = self._index_seal(version)
        if seal is not None and seal != checksum:
            raise ToolError("integrity_error", "Codebook snapshot seal differs from its sources")
        if seal is None and self.db.execute("SELECT 1 FROM index_seal").fetchone():
            raise ToolError("integrity_error", "Codebook snapshot seal belongs to another version")
        return checksum, source_checksum, seal is not None, source_sealed

    def _require_current_index(self) -> None:
        _, _, sealed, _ = self._checked_snapshot(
            self._memory(), self._revision_rows(), self.topic_hierarchy()
        )
        if not sealed:
            # Legacy workspaces need a source comparison before their first seal.
            self.index()

    def index(self, *, verify: bool = False) -> Index:
        if not self.read_only or verify:
            return self._load_index(verify=verify)
        # The read-only connection pins one SQLite snapshot until it closes.
        if self._snapshot_index is None:
            self._snapshot_index = self._load_index(verify=False)
        return self._snapshot_index

    def _load_index(self, *, verify: bool) -> Index:
        current = self._memory()
        rows = self._revision_rows()
        xml = current["xml"]
        version = current["version"]
        hierarchy = self.topic_hierarchy()
        checksum, source_checksum, sealed, source_sealed = self._checked_snapshot(
            current, rows, hierarchy
        )
        records = self._records(rows)
        if sealed and not verify:
            return load_index(xml, records)
        index = build_index(records, json.loads(current["merges"]), hierarchy)
        if index.xml != xml:
            raise ToolError("integrity_error", "Codebook snapshot differs from its sources")
        if not self.read_only and (not sealed or not source_sealed):
            self._seal_index(version, checksum, source_checksum)
        return index

    def source_inventory(
        self,
        source: str | None,
        *,
        project: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        if project is not None:
            validate(TEXT, project, "project")
        if limit is not None and not 1 <= limit <= 1000:
            raise ToolError("invalid_input", "source limit must be between 1 and 1000")
        if cursor is not None:
            if limit is None:
                raise ToolError("invalid_input", "a source cursor requires --limit")
            validate(CURSOR, cursor, "source cursor")
        identity, version = self.snapshot_identity()
        scope = [identity, version, "sources-v1", source, limit]
        if project is not None:
            scope.append(project)
        offset = page_offset(scope, cursor, name="source listing")
        source_filter = "WHERE source=?" if source is not None else ""
        source_args = (source,) if source is not None else ()
        with closing(
            self.db.execute(
                f"""
                SELECT source, conversation_id, count(*) AS revisions,
                    max(CASE WHEN active=1 THEN checksum END) AS current_checksum
                FROM revisions
                {source_filter}
                GROUP BY source, conversation_id
                ORDER BY source, conversation_id
                LIMIT ? OFFSET ?
                """,
                (
                    *source_args,
                    -1 if project is not None or limit is None else limit + 1,
                    0 if project is not None else offset,
                ),
            )
        ) as rows:
            if project is None:
                page = rows.fetchall()
            else:
                # Keep source payloads out of SQLite's temporary sort data.
                matches = (
                    row
                    for row in rows
                    if row["current_checksum"] is not None
                    and self.record(row["current_checksum"])["conversation"]["metadata"].get(
                        "project"
                    )
                    == project
                )
                page = list(islice(matches, offset, None if limit is None else offset + limit + 1))
        result = {"sources": [dict(row) for row in page[:limit]], "memory_version": version}
        if limit is not None:
            result["next_cursor"] = (
                page_cursor(scope, offset + limit) if len(page) > limit else None
            )
        return result

    def source_history(self, source: str, conversation_id: str) -> dict:
        records = self.records(source=source, conversation_id=conversation_id)
        if not records:
            raise ToolError("not_found", "no revisions match the requested conversation")
        active = [record for record in records if record["active"]]
        if len(active) != 1:
            raise ToolError("integrity_error", "conversation must have one current revision")
        records.sort(key=lambda record: not record["active"])
        return {
            "source": source,
            "conversation_id": conversation_id,
            "current_checksum": active[0]["checksum"],
            "memory_version": self.version(),
            "revisions": [
                {
                    "revision_id": revision_id(record),
                    "checksum": record["checksum"],
                    "active": record["active"],
                    "source_day": source_day(record["conversation"]),
                    "segments": len(record["conversation"]["segments"]),
                    "facts": len(record["facts"]),
                }
                for record in records
            ],
        }

    def export_codebook(
        self,
        *,
        source: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
    ) -> bytes:
        if conversation_id is not None and source is None:
            raise ToolError("invalid_input", "conversation scope requires a source namespace")
        if source is None and not active_only:
            return self.index().xml
        if not self._verify_source_snapshot(
            self._memory(), self._revision_rows(), self.topic_hierarchy()
        ):
            self.index()
        records = self.records(
            source=source,
            conversation_id=conversation_id,
            active_only=active_only,
        )
        if source is not None and not records:
            raise ToolError("not_found", "no revisions match the requested export scope")
        retained_facts = {fact_id for record in records for fact_id, _ in fact_entries(record)}
        for record in records:
            for fact in record["facts"]:
                if "supersedes" in fact and fact["supersedes"] not in retained_facts:
                    # Correction edges are workspace metadata. Build a detached
                    # index using only links within the exported source set.
                    del fact["supersedes"]
        merges, hierarchy = self._supported_taxonomy(records)
        return build_index(records, merges, hierarchy).xml

    def _supported_taxonomy(self, records: list[dict]) -> tuple[dict, dict]:
        topics = {
            topic for record in records for fact in record["facts"] for topic in fact["topics"]
        }
        for record in records:
            for topic in record["taxonomy"]:
                topics.add(topic["name"])
                topics.update(topic["parents"])
        merges = {old: new for old, new in self.merges().items() if {old, new} <= topics}
        canonical = {merges.get(topic, topic) for topic in topics}
        hierarchy = {
            topic: [parent for parent in parents if parent in canonical]
            for topic, parents in self.topic_hierarchy().items()
            if topic in canonical
        }
        return merges, hierarchy

    def replace_index(
        self,
        merges: dict | None = None,
        *,
        hierarchy: dict | None = None,
        force: bool = False,
        verify_sources: bool = False,
    ) -> dict:
        current = self._memory()
        previous_hierarchy = self.topic_hierarchy()
        hierarchy = previous_hierarchy if hierarchy is None else hierarchy
        rows = self._revision_rows()
        version = current["version"]
        source_sealed = False
        if verify_sources:
            source_sealed = self._verify_source_snapshot(current, rows, previous_hierarchy)
        merges = json.loads(current["merges"]) if merges is None else merges
        encoded_merges = encode(merges)
        index = build_index(self._records(rows), merges, hierarchy)
        checksum, source_checksum = _snapshot_checksums(index.xml, encoded_merges, rows, hierarchy)
        seal = self._index_seal(version)
        unchanged = (
            not force
            and index.xml == current["xml"]
            and encoded_merges == current["merges"]
            and hierarchy == previous_hierarchy
            and (seal is None or seal == checksum)
        )
        if unchanged:
            if seal is None or (verify_sources and not source_sealed):
                self._seal_index(version, checksum, source_checksum)
            return {"changed": False, "memory_version": version}
        self.db.execute(
            "UPDATE memory SET version=version+1, merges=?, xml=? WHERE singleton=1",
            (encoded_merges, index.xml),
        )
        if hierarchy != previous_hierarchy:
            self.db.execute("DELETE FROM topic_hierarchy")
            self.db.executemany(
                "INSERT INTO topic_hierarchy VALUES(?, ?)",
                ((topic, encode(parents)) for topic, parents in hierarchy.items()),
            )
        self._seal_index(version + 1, checksum, source_checksum)
        return {"changed": True, "memory_version": version + 1}

    def require_source_allowed(self, source, conversation_id, reference) -> None:
        if not isinstance(source, str):
            return
        for scope, value in (("key", reference), ("conversation", conversation_id)):
            if (
                isinstance(value, str)
                and self.db.execute(
                    "SELECT 1 FROM tombstones WHERE scope=? AND key=?",
                    (scope, digest([source, value])),
                ).fetchone()
            ):
                raise ToolError("deleted_source", "this conversation was permanently deleted")

    def commit(self, record: dict, *, append_annotations: bool = False) -> dict:
        conversation = record["conversation"]
        key = source_key(conversation)
        uid = conversation["conversation_id"]
        self.require_source_allowed(
            conversation["source"], uid, conversation["metadata"]["source_reference"]
        )
        self._require_current_index()
        prior = self.db.execute(
            "SELECT * FROM revisions WHERE source_key=? AND active=1", (key,)
        ).fetchone()
        checksum = record["checksum"]
        receipt = {"revision_id": revision_id(record), "checksum": checksum}
        if prior and prior["conversation_id"] != uid:
            raise ToolError(
                "conflict", "source reference is already bound to another conversation ID"
            )
        if prior and prior["checksum"] == checksum:
            if append_annotations:
                return {**receipt, **self._append_annotations(prior, record)}
            return {**receipt, "changed": False, "memory_version": self.version()}
        collision = self.db.execute(
            "SELECT 1 FROM revisions WHERE source=? AND conversation_id=? AND source_key<>?",
            (conversation["source"], uid, key),
        ).fetchone()
        if collision:
            raise ToolError(
                "conflict", "conversation ID is already bound to another source reference"
            )
        if (prior["checksum"] if prior else None) != record["expected_checksum"]:
            raise ToolError("conflict", "changed source requires the current expected_checksum")
        if self.db.execute("SELECT 1 FROM revisions WHERE checksum=?", (checksum,)).fetchone():
            raise ToolError("conflict", "an older source revision cannot be replayed as new")
        self._validate_supersession(record)
        self.db.execute("UPDATE revisions SET active=0 WHERE source_key=? AND active=1", (key,))
        payload = {name: value for name, value in record.items() if name != "expected_checksum"}
        self.db.execute(
            "INSERT INTO revisions VALUES(?, ?, ?, ?, 1, ?)",
            (checksum, key, conversation["source"], uid, encode(payload)),
        )
        return {**receipt, **self.replace_index()}

    def _append_annotations(self, prior: sqlite3.Row, record: dict) -> dict:
        stored = self._decode_revision(prior)
        additions = {}
        for field in ("facts", "taxonomy", "entity_aliases"):
            seen = {digest(item) for item in stored[field]}
            additions[field] = []
            for item in record[field]:
                fingerprint = digest(item)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    additions[field].append(item)
        if not any(additions.values()):
            return {"changed": False, "memory_version": self.version()}
        if record["expected_checksum"] != prior["checksum"]:
            raise ToolError("conflict", "new annotations require the current expected_checksum")
        self._validate_supersession({**record, "facts": additions["facts"]})
        for field, items in additions.items():
            stored[field].extend(items)
        self.db.execute(
            "UPDATE revisions SET payload=? WHERE checksum=?",
            (encode(stored), prior["checksum"]),
        )
        return self.replace_index(force=True)

    def _validate_supersession(self, record: dict) -> None:
        replacements = [fact for fact in record["facts"] if "supersedes" in fact]
        if not replacements:
            return
        index = self.index()
        used = {fact["supersedes"] for fact in index.facts.values() if "supersedes" in fact}
        current = record["conversation"]
        current_day = source_day(current)
        segments = {segment["segment_id"]: segment for segment in current["segments"]}
        for fact in replacements:
            target = fact["supersedes"]
            prior = index.facts.get(target)
            if (
                prior is None
                or prior.get("status") != "confirmed"
                or target in used
                or not fact.get("state_key")
                or fact["state_key"] != prior.get("state_key")
            ):
                raise ToolError(
                    "conflict",
                    "supersession requires an unclaimed confirmed fact with the same state key",
                )
            previous_fact = index.store.facts[target]
            previous = index.revisions[previous_fact.unit]
            previous_conversation = previous["conversation"]
            corrected_time = source_timing(
                record["checksum"],
                current_day,
                current.get("started_at"),
                [segments[sid] for sid in fact["segment_ids"]],
            )
            previous_time = source_timing(
                previous["checksum"],
                source_day(previous_conversation),
                previous_conversation.get("started_at"),
                [index.segments[sid] for sid in previous_fact.src],
            )
            if precedes(corrected_time, previous_time):
                raise ToolError("conflict", "supersession evidence predates the target evidence")
            used.add(target)

    def _redact_run(self, row: sqlite3.Row) -> None:
        checkpoint = json.loads(row["checkpoint"])
        checkpoint.update(
            state="invalidated", current_stage=None, input_digest=digest({}), updated_at=now()
        )
        for state in checkpoint["stages"].values():
            state.pop("artifact", None)
            state.update(state="invalidated", error=None)
        self.db.execute(
            "UPDATE runs SET inputs='{}', definition='', checkpoint=? WHERE id=?",
            (encode(checkpoint), row["id"]),
        )

    def _scrub_deleted_runs(self) -> None:
        if not self.db.execute("SELECT 1 FROM tombstones LIMIT 1").fetchone():
            return
        rows = self.db.execute("SELECT id, inputs, checkpoint FROM runs WHERE inputs!='{}'")
        affected = []
        for row in rows:
            try:
                for identity in _input_sources(row["inputs"]):
                    self.require_source_allowed(*identity)
            except ToolError as exc:
                if exc.code != "deleted_source":
                    raise
                affected.append(row)
        for row in affected:
            self.db.execute("DELETE FROM artifacts WHERE run_id=?", (row["id"],))
            self._redact_run(row)

    def deletion_plan(
        self, target: dict, *, run_id: str
    ) -> tuple[dict, list[dict], list[tuple[str, str]]]:
        self._require_current_index()
        ids = set(_target_ids(target))
        source = target["source"]
        query = "SELECT * FROM revisions WHERE source=?"
        if ids:
            placeholders = ", ".join("?" for _ in ids)
            query += f" AND conversation_id IN ({placeholders})"
        rows = self.db.execute(query, (source, *sorted(ids))).fetchall()
        records = [self._decode_revision(row) for row in rows]
        fact_count = sum(len(record["facts"]) for record in records)
        stored = {row["conversation_id"] for row in rows}
        extras = {}
        if "conversation_ids" in target:
            target = {**target, "conversation_ids": sorted(ids)}
            extras["conversation_ids_without_revisions"] = sorted(ids - stored)
        # The selector stays local to the preview. The audit retains its
        # operation digest and aggregate counts.
        workspace_id, version = self.snapshot_identity()
        # Preparing and retrying the approval updates its own checkpoint.
        # Bind the other run inputs and the artifacts affected by the purge.
        runs = []
        staged = set()
        keys = {row["source_key"] for row in rows}
        for row in self.db.execute("SELECT id, inputs FROM runs WHERE definition<>'' ORDER BY id"):
            if row["id"] != run_id:
                runs.append((row["id"], digest(row["inputs"])))
            for name, identity, reference in _input_sources(row["inputs"]):
                if name != source or not isinstance(identity, str) or (ids and identity not in ids):
                    continue
                if identity.strip():
                    staged.add(identity)
                if isinstance(reference, str) and reference.strip():
                    keys.add(digest([source, reference]))
        artifacts = [
            tuple(row)
            for row in self.db.execute(
                """
                SELECT run_id, stage, kind, digest FROM artifacts
                WHERE run_id<>? OR kind<>'deletion_plan' ORDER BY run_id, stage
                """,
                (run_id,),
            )
        ]
        impact = {
            "target": target,
            "memory_version": version,
            "revisions": len(rows),
            "facts": fact_count,
            "staged_conversation_ids": sorted(staged - stored),
            "runs_to_invalidate": len(runs),
            "run_artifacts_to_purge": len(artifacts),
            **extras,
        }
        tombstones = sorted(
            {("key", key) for key in keys}
            | {("conversation", digest([source, item])) for item in stored | staged | ids}
        )
        approval = digest([workspace_id, run_id, impact, runs, artifacts, tombstones])
        return {**impact, "approval_token": approval}, records, tombstones

    def _deletion_scope(
        self, plan: dict, *, run_id: str
    ) -> tuple[list[dict], list[tuple[str, str]]]:
        current, records, tombstones = self.deletion_plan(plan["target"], run_id=run_id)
        if current["approval_token"] != plan["approval_token"]:
            raise ToolError("stale_preview", "deletion impact changed; start a new deletion run")
        return records, tombstones

    def deletion_impact(self, plan: dict, *, run_id: str) -> dict:
        """Describe the same snapshot and scope that the approval token binds."""
        records, _ = self._deletion_scope(plan, run_id=run_id)
        committed_ids = sorted({record["conversation"]["conversation_id"] for record in records})
        return {
            **{key: value for key, value in plan.items() if key != "approval_token"},
            "committed_conversations": len(committed_ids),
            "committed_conversation_ids": committed_ids[:_DELETION_IMPACT_ID_LIMIT],
            "source_lines": sum(len(record["conversation"]["segments"]) for record in records),
        }

    def delete(self, plan: dict, approval: str | None, *, run_id: str) -> dict:
        if approval is None:
            raise ToolError("awaiting_approval", "delete requires the user-approved preview token")
        if approval != plan["approval_token"]:
            raise ToolError("permission_denied", "approval does not match the deletion preview")
        records, tombstones = self._deletion_scope(plan, run_id=run_id)
        self.db.executemany("INSERT OR IGNORE INTO tombstones VALUES(?, ?)", tombstones)
        deleted = {}
        for record in records:
            self.db.execute("DELETE FROM revisions WHERE checksum=?", (record["checksum"],))
            deleted.update(
                (fact_id, fact.get("supersedes")) for fact_id, fact in fact_entries(record)
            )
        retained = self.records()
        for record in retained:
            changed = False
            for fact in record["facts"]:
                target = fact.get("supersedes")
                if target not in deleted:
                    continue
                seen = set()
                while target in deleted:
                    if target in seen:
                        raise ToolError(
                            "integrity_error", "deleted correction references form a cycle"
                        )
                    seen.add(target)
                    target = deleted[target]
                # Retain the transitive relationship between surviving facts.
                # No deleted ID is needed once the chain has been shortened.
                if target is None:
                    del fact["supersedes"]
                else:
                    fact["supersedes"] = target
                changed = True
            if changed:
                payload = {key: value for key, value in record.items() if key != "active"}
                self.db.execute(
                    "UPDATE revisions SET payload=? WHERE checksum=?",
                    (encode(payload), record["checksum"]),
                )
        # A global report can depend on several sources, including a source not
        # named in its request. Invalidate all prior artifacts conservatively.
        self.db.execute("DELETE FROM artifacts")
        for row in self.db.execute("SELECT id, checkpoint FROM runs").fetchall():
            self._redact_run(row)
        merges, hierarchy = self._supported_taxonomy(retained)
        receipt = self.replace_index(merges, hierarchy=hierarchy, force=True)
        self.db.execute(
            "INSERT INTO deletion_audit VALUES(?, ?, ?, ?, ?)",
            (approval, receipt["memory_version"], len(records), plan["facts"], now()),
        )
        return {**receipt, "deleted_revisions": len(records), "operation_id": approval}
