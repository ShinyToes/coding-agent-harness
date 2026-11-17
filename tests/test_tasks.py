"""Tasks：feature list / progress / git 查询。"""

from __future__ import annotations

import subprocess

import pytest

from _helpers import new_session, open_store
from harness import tasks
from harness.models import EventType


def test_set_feature_writes_file_and_event(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    tasks.set_feature(store, sid, tmp_path, "parse_date tz", True, "test_parse passes")

    features = tasks.load_features(tmp_path)
    assert features == [{"id": "parse_date tz", "passes": True, "evidence": "test_parse passes"}]
    event = store.events(sid)[-1]
    assert event.type == EventType.TASK_UPDATE
    assert event.data["feature_id"] == "parse_date tz"
    assert event.data["passes"] is True


def test_set_feature_updates_existing_entry(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    tasks.set_feature(store, sid, tmp_path, "a", False)
    tasks.set_feature(store, sid, tmp_path, "a", True, "done")
    assert tasks.load_features(tmp_path) == [{"id": "a", "passes": True, "evidence": "done"}]


def test_progress_tail(tmp_path):
    for index in range(50):
        tasks.append_progress(tmp_path, f"step {index}")
    tail = tasks.progress_tail(tmp_path, lines=3)
    assert tail.splitlines() == ["step 47", "step 48", "step 49"]
    assert tasks.progress_tail(tmp_path / "missing", lines=3) == ""


def test_git_head_and_diff(tmp_path):
    assert tasks.head_or_empty(tmp_path) == ""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    head = tasks.head_or_empty(tmp_path)
    assert len(head) == 40

    (tmp_path / "a.txt").write_text("two\n")
    assert "+two" in tasks.diff(head, tmp_path)
    with pytest.raises(RuntimeError):
        tasks.git(["rev-parse", "nope"], tmp_path)
