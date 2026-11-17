"""B-019 / B-020 / B-022 / B-023 的回归测试：审批白名单恢复、explorer 预算、日志、会话锁。"""

from __future__ import annotations

import logging

import pytest

from _helpers import ScriptedLLM, completion, ctx_for, new_session, open_store, settings, tool_call
from harness.audit import NoopAudit
from harness.loop import run
from harness.models import EventType
from harness.policy import Policy, deny_approver
from harness.store import Store
from harness.tools import get_tool


def _allow_once(store, sid, call_id="w1", path="out.txt"):
    call = tool_call("write", call_id=call_id, path=path, content="x")
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    store.append(sid, EventType.APPROVAL_RESPONSE, {"call_id": call_id, "actor": "user", "decision": "always"})
    return call


def test_policy_restores_always_allowlist_from_events(tmp_path):
    """B-019：resume 后同一个 call 不再问。"""
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    call = _allow_once(store, sid)

    policy = Policy(cfg, store.events(sid))
    ruling = policy.decide(call, ctx_for(store, cfg, sid))

    assert ruling.decision == "allow"
    assert ruling.rule == "session.allowlist"


def test_policy_without_events_still_asks(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    (tmp_path / "out.txt").write_text("old")
    call = _allow_once(store, sid)

    policy = Policy(cfg)
    assert policy.decide(call, ctx_for(store, cfg, sid)).rule == "fs.overwrite"


def test_explorer_answers_without_tools(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    llm = ScriptedLLM([completion("答案：calc.py:1")])
    ctx = ctx_for(store, cfg, sid, llm=llm)

    result = get_tool("explorer").run({"question": "average 在哪？"}, ctx)

    assert result.ok
    assert "calc.py:1" in result.output


def test_explorer_respects_tool_call_budget(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    (tmp_path / "a.py").write_text("x = 1\n")
    calls = [
        tool_call("read", call_id=f"r{turn}-{index}", path="a.py") for turn in range(3) for index in range(3)
    ]
    llm = ScriptedLLM([completion("", calls[turn * 3 : turn * 3 + 3]) for turn in range(3)])
    ctx = ctx_for(store, cfg, sid, llm=llm)

    result = get_tool("explorer").run({"question": "读 8 次"}, ctx)

    assert result.meta["budget_exceeded"] is True
    assert "budget" in result.output.lower()


def test_explorer_without_llm_fails_cleanly(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    result = get_tool("explorer").run({"question": "?"}, ctx)
    assert result.ok is False
    assert "no LLM" in result.output


def test_session_lock_blocks_second_owner(tmp_path):
    """B-023：同一 session 不能被两个进程同时跑。"""
    store = Store(tmp_path / "lock.db")
    assert store.acquire_lock("s1", "owner-a") is True
    assert store.acquire_lock("s1", "owner-b") is False
    assert store.lock_holder("s1") == "owner-a"
    store.release_lock("s1", "owner-a")
    assert store.acquire_lock("s1", "owner-b") is True


def test_session_lock_expires(tmp_path):
    store = Store(tmp_path / "lock.db")
    assert store.acquire_lock("s1", "owner-a", ttl_seconds=0) is True
    assert store.acquire_lock("s1", "owner-b", ttl_seconds=0) is True


def test_run_refuses_locked_session(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    store.acquire_lock(sid, "other-process")

    with pytest.raises(RuntimeError, match="locked by"):
        run(sid, store, cfg, llm=ScriptedLLM([]), audit=NoopAudit(), approver=deny_approver)


def test_loop_logs_failures(tmp_path, caplog):
    """B-022：异常既要进事件，也要有 stderr 日志。"""
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)

    class BoomLLM:
        def complete(self, messages, tools):
            raise RuntimeError("provider exploded")

    with caplog.at_level(logging.ERROR, logger="harness"), pytest.raises(RuntimeError):
        run(sid, store, cfg, llm=BoomLLM(), audit=NoopAudit(), approver=deny_approver)

    assert any("loop failed" in record.message for record in caplog.records)
    assert any(e.type == EventType.ERROR for e in store.events(sid))
