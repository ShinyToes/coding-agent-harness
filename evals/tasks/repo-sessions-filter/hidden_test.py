"""H1：给 `h sessions` 加状态过滤与 last_commit 字段。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from harness.cli import app
from harness.store import Store

RUNNER = CliRunner()


def make_store(tmp_path):
    db = tmp_path / "harness.db"
    store = Store(db)
    done = store.create_session("done task", ".", "fake")
    store.set_status(done, "done")
    store.set_commit(done, "abc123")
    running = store.create_session("running task", ".", "fake")
    return db, done, running


def test_sessions_status_filter(tmp_path):
    db, done, running = make_store(tmp_path)
    result = RUNNER.invoke(app, ["sessions", "--status", "done", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert done in result.stdout
    assert running not in result.stdout


def test_sessions_json_has_last_commit(tmp_path):
    db, done, running = make_store(tmp_path)
    result = RUNNER.invoke(app, ["sessions", "--json", "--db", str(db)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    rows = {row["id"]: row for row in payload}
    assert rows[done]["last_commit"] == "abc123"
    assert rows[running]["last_commit"] in (None, "")
