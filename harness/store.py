"""SQLite 事件存储 + 内容寻址 artifacts。

不变量：
- events 只追加（DB 触发器拒绝 UPDATE/DELETE）。
- seq 在 BEGIN IMMEDIATE 事务里由 max(seq)+1 得出，防并发写重号。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from harness.models import (
    Event,
    EventType,
    Session,
    canonical_json,
    sha256_hex,
    utcnow,
)

REFERENCE_KEYS = {"artifact_id", "snapshot_artifact", "request_artifact", "raw_ref"}


def _artifact_refs(data: Any) -> set[str]:
    """递归收集事件里指向 artifact 的字段；未知字段一律不碰（宁可不删）。"""
    refs: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in REFERENCE_KEYS and isinstance(value, str) and value:
                refs.add(value)
            refs |= _artifact_refs(value)
    elif isinstance(data, list):
        for item in data:
            refs |= _artifact_refs(item)
    return refs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, task TEXT, repo TEXT, base_commit TEXT,
  model TEXT, status TEXT, parent_id TEXT, created_at TEXT,
  last_commit TEXT, calibration REAL DEFAULT 3.0,
  tokens_total INTEGER DEFAULT 0, cost REAL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
  ts TEXT NOT NULL, data_json TEXT NOT NULL,
  UNIQUE(session_id, seq)
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, session_id TEXT, kind TEXT,
  sha256 TEXT, path TEXT, bytes INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS session_locks (
  session_id TEXT PRIMARY KEY, owner TEXT NOT NULL, locked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only (no UPDATE)'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only (no DELETE)'); END;
"""

SCHEMA_VERSION = 2
SESSION_COLUMNS = {
    "last_commit": "TEXT",
    "calibration": "REAL DEFAULT 3.0",
    "tokens_total": "INTEGER DEFAULT 0",
    "cost": "REAL DEFAULT 0.0",
}


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """旧库升级：CREATE TABLE IF NOT EXISTS 不会补列，这里按 user_version 加。"""
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version >= SCHEMA_VERSION:
            return
        columns = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        for name, ddl in SESSION_COLUMNS.items():
            if name not in columns:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {ddl}")
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- session
    def create_session(
        self,
        task: str,
        repo: str,
        model: str,
        base_commit: str = "",
        parent_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        sid = session_id or uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO sessions(id,task,repo,base_commit,model,status,parent_id,created_at,"
            "last_commit,calibration,tokens_total,cost) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, task, repo, base_commit, model, "running", parent_id,
             utcnow().isoformat(), base_commit, 3.0, 0, 0.0),
        )
        return sid

    def session(self, session_id: str) -> Session:
        row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown session {session_id}")
        return Session.model_validate(dict(row))

    def has_session(self, session_id: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row is not None

    def set_status(self, session_id: str, status: str) -> None:
        self._conn.execute("UPDATE sessions SET status=? WHERE id=?", (status, session_id))

    def sessions(self) -> list[Session]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY created_at").fetchall()
        return [Session.model_validate(dict(row)) for row in rows]

    def set_commit(self, session_id: str, commit: str) -> None:
        self._conn.execute("UPDATE sessions SET last_commit=? WHERE id=?", (commit, session_id))

    def set_calibration(self, session_id: str, ratio: float) -> None:
        self._conn.execute("UPDATE sessions SET calibration=? WHERE id=?", (ratio, session_id))

    def add_usage(self, session_id: str, tokens: int, cost: float) -> None:
        self._conn.execute(
            "UPDATE sessions SET tokens_total=tokens_total+?, cost=cost+? WHERE id=?",
            (tokens, cost, session_id),
        )

    # ------------------------------------------------------------ session lock
    def acquire_lock(self, session_id: str, owner: str, ttl_seconds: int = 3600) -> bool:
        now = utcnow()
        row = self._conn.execute(
            "SELECT owner, locked_at FROM session_locks WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is not None:
            age = (now - datetime.fromisoformat(row["locked_at"])).total_seconds()
            if age < ttl_seconds and row["owner"] != owner:
                return False
            self._conn.execute(
                "UPDATE session_locks SET owner=?, locked_at=? WHERE session_id=?",
                (owner, now.isoformat(), session_id),
            )
            return True
        self._conn.execute(
            "INSERT INTO session_locks(session_id, owner, locked_at) VALUES(?,?,?)",
            (session_id, owner, now.isoformat()),
        )
        return True

    def release_lock(self, session_id: str, owner: str) -> None:
        self._conn.execute(
            "DELETE FROM session_locks WHERE session_id=? AND owner=?", (session_id, owner)
        )

    def lock_holder(self, session_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT owner FROM session_locks WHERE session_id=?", (session_id,)
        ).fetchone()
        return str(row["owner"]) if row else None

    # ------------------------------------------------------------------ events
    def append(self, session_id: str, type: EventType, data: dict[str, Any]) -> Event:
        ts = utcnow()
        payload = canonical_json(data)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq),0) AS m FROM events WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = int(row["m"]) + 1
            cur = self._conn.execute(
                "INSERT INTO events(session_id,seq,type,ts,data_json) VALUES(?,?,?,?,?)",
                (session_id, seq, str(type), ts.isoformat(), payload),
            )
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        return Event(
            id=cur.lastrowid,
            session_id=session_id,
            seq=seq,
            type=type,
            ts=ts,
            data=json.loads(payload),
        )

    def events(self, session_id: str, after_seq: int = 0) -> list[Event]:
        rows = self._conn.execute(
            "SELECT id,session_id,seq,type,ts,data_json FROM events "
            "WHERE session_id=? AND seq>? ORDER BY seq",
            (session_id, after_seq),
        ).fetchall()
        out: list[Event] = []
        for row in rows:
            out.append(
                Event(
                    id=row["id"],
                    session_id=row["session_id"],
                    seq=row["seq"],
                    type=EventType(row["type"]),
                    ts=datetime.fromisoformat(row["ts"]),
                    data=json.loads(row["data_json"]),
                )
            )
        return out

    def last(self, session_id: str) -> Event | None:
        rows = self.events(session_id)
        return rows[-1] if rows else None

    def count(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE session_id=?", (session_id,)
        ).fetchone()
        return int(row["n"])

    # --------------------------------------------------------------- artifacts
    def put_artifact(self, session_id: str, kind: str, content: bytes) -> str:
        aid = sha256_hex(content)
        base = Path(self.path).parent / "artifacts" / aid[:2]
        base.mkdir(parents=True, exist_ok=True)
        target = base / aid
        if not target.exists():
            target.write_bytes(content)
        self._conn.execute(
            "INSERT OR IGNORE INTO artifacts(id,session_id,kind,sha256,path,bytes,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (aid, session_id, kind, aid, str(target), len(content), utcnow().isoformat()),
        )
        return aid

    def artifact_meta(self, artifact_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown artifact {artifact_id}")
        return dict(row)

    def get_artifact(self, artifact_id: str) -> bytes:
        meta = self.artifact_meta(artifact_id)
        path = Path(meta["path"])
        if not path.exists():
            raise FileNotFoundError(f"artifact blob missing: {artifact_id}")
        return path.read_bytes()

    def get_artifact_text(self, artifact_id: str) -> str:
        return self.get_artifact(artifact_id).decode("utf-8", errors="replace")

    # ---------------------------------------------------------------------- gc
    def gc(self, keep_days: int | None = 7, dry_run: bool = False) -> dict[str, Any]:
        """删除事件流不再引用的 artifact（默认只删 7 天前的）；事件本身不动。"""
        refs: set[str] = set()
        for row in self._conn.execute("SELECT data_json FROM events"):
            refs |= _artifact_refs(json.loads(row["data_json"]))

        rows = self._conn.execute("SELECT id,path,bytes,created_at FROM artifacts").fetchall()
        cutoff = utcnow() - timedelta(days=keep_days) if keep_days is not None else None
        deleted: list[str] = []
        freed = 0
        for row in rows:
            aid = str(row["id"])
            if aid in refs:
                continue
            if cutoff is not None and datetime.fromisoformat(row["created_at"]) >= cutoff:
                continue
            deleted.append(aid)
            freed += int(row["bytes"] or 0)
            if not dry_run:
                Path(row["path"]).unlink(missing_ok=True)
                self._conn.execute("DELETE FROM artifacts WHERE id=?", (aid,))
        return {
            "scanned": len(rows),
            "referenced": len(refs),
            "deleted": len(deleted),
            "freed_bytes": freed,
            "dry_run": dry_run,
            "sample": deleted[:10],
        }
