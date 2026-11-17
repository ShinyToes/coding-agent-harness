"""Replay：同输入同输出（验收第 6 条）+ 快照重建历史请求（B-004）。"""

from __future__ import annotations

import hashlib

from _helpers import (
    FakeSandbox,
    ScriptedLLM,
    completion,
    model_response,
    new_session,
    open_store,
    settings,
    tool_call,
)
from harness.audit import NoopAudit
from harness.context import build_context
from harness.loop import run
from harness.models import EventType, canonical_json
from harness.policy import deny_approver
from harness.replay import rebuild
from harness.store import Store


def fingerprint(messages: list[dict]) -> str:
    return hashlib.sha256(canonical_json(messages).encode()).hexdigest()


def seed(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store, "回放我")
    for index in range(4):
        call = tool_call("read", call_id=f"c{index}", path="a.py")
        store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
        store.append(sid, EventType.TOOL_CALL, call.model_dump())
        store.append(
            sid,
            EventType.TOOL_RESULT,
            {"call_id": call.call_id, "ok": True, "output_preview": f"line {index}", "tail": "",
             "artifact_id": "a", "chars": 8, "duration_ms": 1, "truncated": False},
        )
    return store, sid


def test_replay_to_seq_is_deterministic(tmp_path):
    store, sid = seed(tmp_path)
    cfg = settings(tmp_path)
    events = [e for e in store.events(sid) if e.seq <= 12]
    first, _ = build_context(events, cfg)
    second, _ = build_context(events, cfg)
    assert fingerprint(first) == fingerprint(second)


def test_replay_prefix_differs_from_full_history(tmp_path):
    store, sid = seed(tmp_path)
    cfg = settings(tmp_path)
    full, _ = build_context(store.events(sid), cfg)
    prefix, _ = build_context([e for e in store.events(sid) if e.seq <= 7], cfg)
    assert fingerprint(full) != fingerprint(prefix)


def test_replay_survives_reopen(tmp_path):
    store, sid = seed(tmp_path)
    cfg = settings(tmp_path)
    first, _ = build_context(store.events(sid), cfg)
    reopened = Store(store.path)
    second, _ = build_context(reopened.events(sid), cfg)
    assert fingerprint(first) == fingerprint(second)


def test_cli_replay_prints_stable_fingerprint(tmp_path):
    from typer.testing import CliRunner

    from harness.cli import app

    store, sid = seed(tmp_path)
    runner = CliRunner()
    args = ["replay", sid, "--dry-run", "--repo", str(tmp_path), "--db", str(store.path)]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first.stdout == second.stdout
    assert "sha256=" in first.stdout


def run_live_style_session(tmp_path):
    """跑一次真实 loop（含 repo_map/progress 注入），留下 model.request 快照。"""
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store, "加一个函数")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    llm = ScriptedLLM(
        [
            completion("", [tool_call("read", call_id="r1", path="a.py")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ]
    )
    status = run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=NoopAudit())
    assert status == "done"
    return store, sid, cfg


def test_rebuild_matches_recorded_request(tmp_path):
    store, sid, cfg = run_live_style_session(tmp_path)
    check = rebuild(store, store.events(sid), cfg)
    assert check.record_seq is not None
    assert check.match is True


def test_snapshot_is_what_makes_replay_faithful(tmp_path):
    """不传快照时（旧行为）repo_map 丢失，重建结果与记录值不同。"""
    store, sid, cfg = run_live_style_session(tmp_path)
    check = rebuild(store, store.events(sid), cfg)
    assert check.match is True

    fresh = settings(tmp_path)  # 新进程里 cfg.repo_map 是空的
    prior = [e for e in store.events(sid) if e.seq < (check.record_seq or 0)]
    without_snapshot, _ = build_context(prior, fresh)
    assert canonical_json(without_snapshot) != canonical_json(check.messages)
