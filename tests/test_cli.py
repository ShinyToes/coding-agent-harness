"""CLI：trace / approvals / diff 的基本可用性。"""

from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from _helpers import model_response, new_session, open_store, tool_call
from harness import tasks
from harness.cli import app
from harness.models import EventType


def git_repo(tmp_path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tasks.head_or_empty(tmp_path)


def test_help_lists_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "resume", "replay", "trace", "diff", "approvals"):
        assert command in result.stdout


def test_trace_renders_events(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    call = tool_call("read", call_id="c1", path="a.py")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    result = CliRunner().invoke(app, ["trace", sid, "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    for fragment in ("session.start", "user.message", "model.response", "tool.call"):
        assert fragment in result.stdout


def test_approvals_renders_policy_decisions(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    store.append(sid, EventType.POLICY_DECISION, {"call_id": "c1", "decision": "ask", "rule": "fs.overwrite", "reason": "x"})
    store.append(sid, EventType.APPROVAL_RESPONSE, {"call_id": "c1", "actor": "user", "decision": "deny"})
    result = CliRunner().invoke(app, ["approvals", sid, "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    assert "fs.overwrite" in result.stdout
    assert "deny" in result.stdout


def seed_usage(store, sid, prompt=1000, completion=100, spent=0.001):
    store.append(
        sid,
        EventType.MODEL_RESPONSE,
        {
            "text": "ok",
            "tool_calls": [],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
            "model": "fake",
            "latency_ms": 5,
            "raw_ref": "",
            "cost": spent,
        },
    )
    store.add_usage(sid, prompt + completion, spent)


def test_trace_shows_tokens_and_cost(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    seed_usage(store, sid)
    result = CliRunner().invoke(app, ["trace", sid, "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    assert "tokens" in result.stdout
    assert "cost" in result.stdout
    assert "$0.001000" in result.stdout


def test_trace_json_is_machine_readable(tmp_path):
    import json

    store = open_store(tmp_path)
    sid = new_session(store)
    seed_usage(store, sid)
    result = CliRunner().invoke(app, ["trace", sid, "--json", "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["session"] == sid
    assert any(row["type"] == "model.response" and row["tokens"] == 1100 for row in payload["events"])


def test_sessions_lists_all(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store, "第一个任务")
    result = CliRunner().invoke(app, ["sessions", "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    assert sid in result.stdout
    assert "第一个任务" in result.stdout


def test_cost_breaks_down_per_call(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    seed_usage(store, sid, prompt=1000, completion=100, spent=0.001)
    seed_usage(store, sid, prompt=500, completion=50, spent=0.0005)
    result = CliRunner().invoke(app, ["cost", sid, "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    assert "prompt=1500" in result.stdout
    assert "completion=150" in result.stdout
    assert "$0.001500" in result.stdout


def test_diff_shows_session_changes(tmp_path):
    base = git_repo(tmp_path)
    store = open_store(tmp_path)
    sid = store.create_session("task", str(tmp_path), "fake")
    store.append(sid, EventType.SESSION_START, {"base_commit": base})
    (tmp_path / "a.txt").write_text("two\n")
    result = CliRunner().invoke(app, ["diff", sid, "--repo", str(tmp_path), "--db", str(store.path)])
    assert result.exit_code == 0, result.output
    assert "-one" in result.stdout and "+two" in result.stdout


def test_diff_outside_git_reports_error(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    result = CliRunner().invoke(app, ["diff", sid, "--repo", str(tmp_path), "--db", str(store.path)])
    assert result.exit_code == 1
    assert "git diff failed" in result.stdout
