"""Transactional source, Codebook, artifact and checkpoint persistence."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .contracts import ToolError, digest, encode, now, utc_timestamp
from .index import build_index, revision_id, source_key
from .ingest import source_day

_DDL = """
CREATE TABLE IF NOT EXISTS memory (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    workspace_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    merges TEXT NOT NULL,
    xml BLOB NOT NULL
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


class Workspace:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

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
            yield cls(db)
            db.commit()
        finally:
            db.close()

    def _memory(self):
        return self.db.execute("SELECT * FROM memory WHERE singleton=1").fetchone()

    def version(self) -> int:
        return self._memory()["version"]

    def merges(self) -> dict:
        return json.loads(self._memory()["merges"])

    def records(self) -> list[dict]:
        records = []
        for row in self.db.execute("SELECT * FROM revisions ORDER BY checksum"):
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

    def index(self):
        index = build_index(self.records(), self.merges())
        if index.xml != self._memory()["xml"]:
            raise ToolError("integrity_error", "Codebook snapshot differs from its sources")
        return index

    def replace_index(self, merges: dict | None = None, *, force: bool = False) -> dict:
        merges = self.merges() if merges is None else merges
        index = build_index(self.records(), merges)
        current = self._memory()
        if not force and index.xml == current["xml"] and encode(merges) == current["merges"]:
            return {"changed": False, "memory_version": self.version()}
        self.db.execute(
            "UPDATE memory SET version=version+1, merges=?, xml=? WHERE singleton=1",
            (encode(merges), index.xml),
        )
        return {"changed": True, "memory_version": self.version()}

    def commit(self, record: dict) -> dict:
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
        deleted = set()
        for row in rows:
            checks = (
                ("key", row["source_key"]),
                ("conversation", digest([row["source"], row["conversation_id"]])),
            )
            self.db.executemany("INSERT OR IGNORE INTO tombstones VALUES(?, ?)", checks)
            self.db.execute("DELETE FROM revisions WHERE checksum=?", (row["checksum"],))
            deleted.add("c-" + row["checksum"])
        for record in self.records():
            changed = False
            for fact in record["facts"]:
                target = fact.get("supersedes", "")
                if target.split("F", 1)[0] in deleted:
                    del fact["supersedes"]
                    changed = True
            if changed:
                record.pop("active")
                self.db.execute(
                    "UPDATE revisions SET payload=? WHERE checksum=?",
                    (encode(record), record["checksum"]),
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
        retained = self.records()
        topics = {
            topic for record in retained for fact in record["facts"] for topic in fact["topics"]
        } | {topic["name"] for record in retained for topic in record["taxonomy"]}
        merges = {old: new for old, new in self.merges().items() if {old, new} <= topics}
        receipt = self.replace_index(merges, force=True)
        self.db.execute(
            "INSERT INTO deletion_audit VALUES(?, ?, ?, ?, ?)",
            (approval, receipt["memory_version"], len(rows), plan["facts"], now()),
        )
        return {**receipt, "deleted_revisions": len(rows), "operation_id": approval}
