"""Store：事件只追加、seq 单调、artifact 内容寻址。"""

from __future__ import annotations

import sqlite3

import pytest

from _helpers import new_session, open_store
from harness.models import EventType
from harness.store import SCHEMA_VERSION, Store


def test_old_database_is_migrated(tmp_path):
    """B-012：旧库（sessions 少列）能被新代码打开并补齐列。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, task TEXT, repo TEXT, base_commit TEXT,"
        " model TEXT, status TEXT, parent_id TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES('old1','t','r','','m','running',NULL,'2026-01-01T00:00:00')")
    conn.commit()
    conn.close()

    store = Store(path)
    session = store.session("old1")
    assert session.tokens_total == 0
    assert session.calibration == 3.0
    assert conn_version(store) == SCHEMA_VERSION
    store.set_commit("old1", "abc")
    assert store.session("old1").last_commit == "abc"


def conn_version(store) -> int:
    return int(store._conn.execute("PRAGMA user_version").fetchone()[0])


def test_seq_starts_at_one_and_increments(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    assert [e.seq for e in store.events(sid)] == [1, 2]
    event = store.append(sid, EventType.ERROR, {"where": "t", "message": "boom", "stack_hash": ""})
    assert event.seq == 3
    assert store.last(sid).seq == 3
    assert store.count(sid) == 3


def test_events_after_seq_and_isolation(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    other = new_session(store, task="other")
    store.append(sid, EventType.USER_MESSAGE, {"text": "third"})
    assert [e.seq for e in store.events(sid, after_seq=1)] == [2, 3]
    assert all(e.session_id == other for e in store.events(other))


def test_events_are_append_only_at_the_database_level(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    other = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            other.execute("UPDATE events SET data_json='{}' WHERE session_id=?", (sid,))
        with pytest.raises(sqlite3.DatabaseError):
            other.execute("DELETE FROM events WHERE session_id=?", (sid,))
    finally:
        other.close()
    assert store.count(sid) == 2


def test_artifacts_are_content_addressed_and_roundtrip(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    aid = store.put_artifact(sid, "tool_output", b"hello world")
    assert store.get_artifact(aid) == b"hello world"
    assert store.get_artifact_text(aid) == "hello world"
    assert store.put_artifact(sid, "tool_output", b"hello world") == aid
    assert store.artifact_meta(aid)["bytes"] == 11
    with pytest.raises(KeyError):
        store.get_artifact("missing")


def test_gc_deletes_only_unreferenced_artifacts(tmp_path):

    store = open_store(tmp_path)
    sid = new_session(store)
    referenced = store.put_artifact(sid, "tool_output", b"keep me")
    orphan = store.put_artifact(sid, "tool_output", b"drop me")
    store.append(
        sid,
        EventType.TOOL_RESULT,
        {"call_id": "c1", "ok": True, "output_preview": "", "artifact_id": referenced, "chars": 0},
    )

    dry = store.gc(keep_days=0, dry_run=True)
    assert dry["deleted"] == 1 and dry["dry_run"] is True
    assert store.get_artifact(orphan) == b"drop me"  # dry-run 不删

    stats = store.gc(keep_days=0)
    assert stats["deleted"] == 1
    assert store.get_artifact(referenced) == b"keep me"
    with pytest.raises(KeyError):
        store.artifact_meta(orphan)


def test_gc_keeps_recent_artifacts(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    orphan = store.put_artifact(sid, "tool_output", b"fresh")
    stats = store.gc(keep_days=7)
    assert stats["deleted"] == 0
    assert store.get_artifact(orphan) == b"fresh"


def test_session_usage_and_calibration_persist(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    store.add_usage(sid, 120, 0.5)
    store.add_usage(sid, 80, 0.25)
    store.set_calibration(sid, 2.5)
    session = store.session(sid)
    assert session.tokens_total == 200
    assert session.cost == pytest.approx(0.75)
    assert session.calibration == pytest.approx(2.5)


def test_unknown_session_raises(tmp_path):
    store = open_store(tmp_path)
    with pytest.raises(KeyError):
        store.session("nope")
