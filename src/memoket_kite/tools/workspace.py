"""Transactional source, Codebook, artifact and checkpoint persistence."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .contracts import ToolError, digest, encode, now, utc_timestamp
from .index import build_index, fact_entries, load_index, revision_id, source_key
from .ingest import source_day

_DDL = """
CREATE TABLE IF NOT EXISTS memory (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    workspace_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    merges TEXT NOT NULL,
    xml BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS index_seal (
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


def _snapshot_checksum(xml: bytes, merges: str, rows: list[sqlite3.Row]) -> str:
    if not isinstance(xml, bytes) or not isinstance(merges, str):
        raise ToolError("integrity_error", "invalid stored Codebook snapshot")
    checksum = sha256()

    def frame(value: bytes) -> None:
        checksum.update(len(value).to_bytes(8, "big"))
        checksum.update(value)

    # Change the seal format when the Codebook compiler's persisted output changes.
    frame(b"kite-codebook-v1")
    frame(xml)
    frame(merges.encode("utf-8"))
    try:
        for row in rows:
            frame(encode(list(row)).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ToolError("integrity_error", "invalid stored source record") from exc
    return checksum.hexdigest()


class Workspace:
    def __init__(self, db: sqlite3.Connection, directory: Path):
        self.db = db
        self.directory = directory

    @classmethod
    @contextmanager
    def open(cls, directory: Path):
        directory = directory.expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix" and (directory.stat().st_mode & 0o077):
            raise ToolError(
                "permission_denied", "workspace directory must be owner-only (chmod 700)"
            )
        path = directory / "kite.sqlite3"
        if path.is_symlink():
            raise ToolError("permission_denied", "workspace database cannot be a symbolic link")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if os.name == "posix" and (path.stat().st_mode & 0o077):
            raise ToolError("permission_denied", "workspace database must be owner-only")
        db = sqlite3.connect(path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
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
            yield cls(db, directory)
            db.commit()
        finally:
            db.close()

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

    def merges(self) -> dict:
        row = self.db.execute("SELECT merges FROM memory WHERE singleton=1").fetchone()
        return json.loads(row["merges"])

    def _revision_rows(
        self,
        *,
        source: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = False,
    ) -> list[sqlite3.Row]:
        filters = {
            "source": source,
            "conversation_id": conversation_id,
            "active": 1 if active_only else None,
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

    @staticmethod
    def _records(rows: list[sqlite3.Row]) -> list[dict]:
        records = []
        for row in rows:
            record = json.loads(row["payload"])
            conversation = record["conversation"]
            if (
                record["checksum"] != row["checksum"]
                or digest(conversation) != row["checksum"]
                or source_key(conversation) != row["source_key"]
                or conversation["source"] != row["source"]
                or conversation["conversation_id"] != row["conversation_id"]
            ):
                raise ToolError(
                    "integrity_error", "a source record differs from its stored identity"
                )
            records.append({**record, "active": bool(row["active"])})
        return records

    def _index_seal(self, version: int) -> str | None:
        row = self.db.execute(
            "SELECT checksum FROM index_seal WHERE singleton=1 AND version=?", (version,)
        ).fetchone()
        return row["checksum"] if row else None

    def _seal_index(self, version: int, checksum: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO index_seal VALUES(1, ?, ?)", (version, checksum))

    def index(self, *, verify: bool = False):
        current = self._memory()
        rows = self._revision_rows()
        xml = current["xml"]
        version = current["version"]
        checksum = _snapshot_checksum(xml, current["merges"], rows)
        seal = self._index_seal(version)
        if seal is not None and seal != checksum:
            raise ToolError("integrity_error", "Codebook snapshot seal differs from its sources")
        records = self._records(rows)
        if seal is not None and not verify:
            return load_index(xml, records)
        index = build_index(records, json.loads(current["merges"]))
        if index.xml != xml:
            raise ToolError("integrity_error", "Codebook snapshot differs from its sources")
        if seal is None:
            self._seal_index(version, checksum)
        return index

    def list_sources(self, source: str | None = None) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT source, conversation_id, count(*) AS revisions,
                max(CASE WHEN active=1 THEN checksum END) AS current_checksum
            FROM revisions
            WHERE (? IS NULL OR source=?)
            GROUP BY source, conversation_id
            ORDER BY source, conversation_id
            """,
            (source, source),
        )
        return [dict(row) for row in rows]

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
        return build_index(records, self._supported_merges(records)).xml

    def _supported_merges(self, records: list[dict]) -> dict:
        topics = {
            topic for record in records for fact in record["facts"] for topic in fact["topics"]
        }
        for record in records:
            for topic in record["taxonomy"]:
                topics.add(topic["name"])
                topics.update(topic["parents"])
        return {old: new for old, new in self.merges().items() if {old, new} <= topics}

    def replace_index(self, merges: dict | None = None, *, force: bool = False) -> dict:
        current = self._memory()
        merges = json.loads(current["merges"]) if merges is None else merges
        encoded_merges = encode(merges)
        rows = self._revision_rows()
        index = build_index(self._records(rows), merges)
        version = current["version"]
        checksum = _snapshot_checksum(index.xml, encoded_merges, rows)
        seal = self._index_seal(version)
        unchanged = (
            not force
            and index.xml == current["xml"]
            and encoded_merges == current["merges"]
            and (seal is None or seal == checksum)
        )
        if unchanged:
            if seal is None:
                self._seal_index(version, checksum)
            return {"changed": False, "memory_version": version}
        self.db.execute(
            "UPDATE memory SET version=version+1, merges=?, xml=? WHERE singleton=1",
            (encoded_merges, index.xml),
        )
        self._seal_index(version + 1, checksum)
        return {"changed": True, "memory_version": version + 1}

    def commit(self, record: dict, *, append_annotations: bool = False) -> dict:
        conversation = record["conversation"]
        key = source_key(conversation)
        uid = conversation["conversation_id"]
        checks = (("key", key), ("conversation", digest([conversation["source"], uid])))
        if any(
            self.db.execute("SELECT 1 FROM tombstones WHERE scope=? AND key=?", item).fetchone()
            for item in checks
        ):
            raise ToolError("deleted_source", "this conversation was permanently deleted")
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
        stored = json.loads(prior["payload"])
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
            current = record["conversation"]
            previous = index.revisions[index.store.facts[target].unit]["conversation"]
            current_day, previous_day = source_day(current), source_day(previous)
            current_stamp, previous_stamp = current.get("started_at"), previous.get("started_at")
            if (previous_day and current_day and current_day < previous_day) or (
                previous_stamp
                and current_stamp
                and utc_timestamp(current_stamp) < utc_timestamp(previous_stamp)
            ):
                raise ToolError("conflict", "supersession evidence predates the target evidence")
            used.add(target)

    def deletion_plan(self, target: dict) -> dict:
        field = "conversation_id" if "conversation_id" in target else "source"
        # Identifiers in the selector are for the local preview only. Persist the
        # operation digest and aggregate counts in the audit.
        if field == "conversation_id":
            rows = self.db.execute(
                "SELECT * FROM revisions WHERE source=? AND conversation_id=?",
                (target["source"], target[field]),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM revisions WHERE source=?", (target["source"],)
            ).fetchall()
        fact_count = sum(len(json.loads(row["payload"])["facts"]) for row in rows)
        operation = digest([self._memory()["workspace_id"], self.version(), target])
        return {
            "target": target,
            "approval_token": operation,
            "memory_version": self.version(),
            "revisions": len(rows),
            "facts": fact_count,
            "run_artifacts_to_purge": self.db.execute("SELECT count(*) FROM artifacts").fetchone()[
                0
            ],
        }, rows

    def delete(self, plan: dict, approval: str | None) -> dict:
        if approval is None:
            raise ToolError("awaiting_approval", "delete requires the user-approved preview token")
        if approval != plan["approval_token"]:
            raise ToolError("permission_denied", "approval does not match the deletion preview")
        current, rows = self.deletion_plan(plan["target"])
        if current["approval_token"] != approval:
            raise ToolError("stale_snapshot", "memory changed; preview the deletion again")
        target = plan["target"]
        if "conversation_id" in target:
            self.db.execute(
                "INSERT OR IGNORE INTO tombstones VALUES('conversation', ?)",
                (digest([target["source"], target["conversation_id"]]),),
            )
        deleted = {}
        for row in rows:
            checks = (
                ("key", row["source_key"]),
                ("conversation", digest([row["source"], row["conversation_id"]])),
            )
            self.db.executemany("INSERT OR IGNORE INTO tombstones VALUES(?, ?)", checks)
            self.db.execute("DELETE FROM revisions WHERE checksum=?", (row["checksum"],))
            deleted.update(
                (fact_id, fact.get("supersedes"))
                for fact_id, fact in fact_entries(json.loads(row["payload"]))
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
        receipt = self.replace_index(self._supported_merges(retained), force=True)
        self.db.execute(
            "INSERT INTO deletion_audit VALUES(?, ?, ?, ?, ?)",
            (approval, receipt["memory_version"], len(rows), plan["facts"], now()),
        )
        return {**receipt, "deleted_revisions": len(rows), "operation_id": approval}
