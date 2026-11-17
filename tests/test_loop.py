"""主循环：事件顺序、策略拦截、nudge、max_steps、read 缓存缩短上下文（验收第 2 条）。"""

from __future__ import annotations

from _helpers import ScriptedLLM, completion, ctx_for, new_session, open_store, settings, tool_call
from harness.audit import NoopAudit
from harness.loop import execute, run, steps_used
from harness.models import EventType, ExecResult
from harness.policy import Policy, deny_approver


class _Sandbox:
    def __init__(self):
        self.commands = []

    def start(self):
        return None

    def stop(self):
        return None

    def exec(self, cmd, cwd, timeout):
        self.commands.append(cmd)
        return ExecResult(rc=0, stdout="ran")


def run_session(tmp_path, script, **cfg_overrides):
    cfg = settings(tmp_path, **cfg_overrides)
    store = open_store(tmp_path)
    sid = new_session(store, "干活")
    llm = ScriptedLLM(script)
    status = run(sid, store, cfg, llm=llm, sandbox=_Sandbox(), approver=deny_approver, audit=NoopAudit())
    return store, sid, status, llm


def test_read_then_finish_event_order(tmp_path):
    (tmp_path / "a.py").write_text("print('hi')\n")
    store, sid, status, _ = run_session(
        tmp_path,
        [
            completion("", [tool_call("read", call_id="c1", path="a.py")]),
            completion("", [tool_call("finish", call_id="c2", summary="read it")]),
        ],
    )
    assert status == "done"
    types = [e.type for e in store.events(sid)]
    assert types[-1] == EventType.SESSION_END
    assert store.session(sid).status == "done"
    end = store.events(sid)[-1]
    assert end.data["status"] == "done"
    assert end.data["steps"] == 2

    events = store.events(sid)
    call_seq = next(e.seq for e in events if e.type == EventType.TOOL_CALL)
    decision_seq = next(e.seq for e in events if e.type == EventType.POLICY_DECISION)
    result_seq = next(e.seq for e in events if e.type == EventType.TOOL_RESULT)
    assert call_seq < decision_seq < result_seq


def test_denied_bash_is_not_executed(tmp_path):
    class Sandbox(_Sandbox):
        def exec(self, cmd, cwd, timeout):
            raise AssertionError("denied command must not reach the sandbox")

    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    llm = ScriptedLLM(
        [
            completion("", [tool_call("bash", call_id="b1", command="rm -rf /")]),
            completion("", [tool_call("finish", call_id="f1", summary="blocked")]),
        ]
    )
    status = run(sid, store, cfg, llm=llm, sandbox=Sandbox(), approver=deny_approver, audit=NoopAudit())
    assert status == "done"
    events = store.events(sid)
    decision = next(e for e in events if e.type == EventType.POLICY_DECISION)
    result = next(e for e in events if e.type == EventType.TOOL_RESULT)
    assert decision.data["decision"] == "deny"
    assert result.data["ok"] is False
    assert "denied by cmd.destructive" in result.data["output_preview"]


def test_write_asks_and_deny_in_non_interactive_mode(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    store, sid, status, _ = run_session(
        tmp_path,
        [
            completion("", [tool_call("write", call_id="w1", path="a.txt", content="new")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ],
        policy__approval_mode="deny",
    )
    assert status == "done"
    events = store.events(sid)
    assert (tmp_path / "a.txt").read_text() == "old"
    assert any(e.type == EventType.APPROVAL_REQUEST for e in events)
    approval = next(e for e in events if e.type == EventType.APPROVAL_RESPONSE)
    assert approval.data["decision"] == "deny"
    result = next(e for e in events if e.type == EventType.TOOL_RESULT)
    assert "denied by user" in result.data["output_preview"]


def test_write_allowed_after_approval(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    llm = ScriptedLLM(
        [
            completion("", [tool_call("write", call_id="w1", path="new.txt", content="hello")]),
            completion("", [tool_call("bash", call_id="v1", command="python -c pass")]),
            completion("", [tool_call("finish", call_id="f1", summary="wrote")]),
        ]
    )
    status = run(sid, store, cfg, llm=llm, sandbox=_Sandbox(), approver=lambda c, r: "allow", audit=NoopAudit())
    assert status == "done"
    assert (tmp_path / "new.txt").read_text() == "hello"


def test_nudge_then_finish(tmp_path):
    store, sid, status, _ = run_session(
        tmp_path,
        [
            completion("我会继续"),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ],
    )
    assert status == "done"
    nudges = [e for e in store.events(sid) if e.type == EventType.USER_MESSAGE and e.data.get("nudge")]
    assert len(nudges) == 1


def test_no_tool_calls_after_nudges_ends_session(tmp_path):
    store, sid, status, _ = run_session(
        tmp_path,
        [completion("a"), completion("b"), completion("c")],
        run__max_nudges=1,
    )
    assert status == "no_tool_calls"
    assert store.events(sid)[-1].data["status"] == "no_tool_calls"


def test_max_steps_guard(tmp_path):
    (tmp_path / "a.py").write_text("x\n")
    store, sid, status, _ = run_session(
        tmp_path,
        [completion("", [tool_call("read", call_id="c1", path="a.py")])],
        run__max_steps=1,
    )
    assert status == "max_steps"
    assert steps_used(store.events(sid)) == 1


def test_on_event_streams_events_in_order(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    seen = []
    llm = ScriptedLLM(
        [
            completion("", [tool_call("read", call_id="r1", path="a.py")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ]
    )
    run(sid, store, cfg, llm=llm, sandbox=_Sandbox(), approver=deny_approver, audit=NoopAudit(), on_event=seen.append)

    seqs = [e.seq for e in seen]
    assert seqs == sorted(seqs)
    types = {e.type for e in seen}
    assert EventType.MODEL_RESPONSE in types
    assert EventType.TOOL_RESULT in types
    assert EventType.SESSION_END in types
    assert seen[-1].type == EventType.SESSION_END


def test_verification_hint_after_edit_points_to_finish(tmp_path):
    """B-038：最后一次编辑后的第一次成功 bash，工具结果里必须出现收尾提示。"""
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    ctx = ctx_for(store, cfg, sid, sandbox=_Sandbox())
    policy = Policy(cfg)
    allow = lambda call, ruling: "allow"  # noqa: E731

    before = execute(tool_call("bash", call_id="b0", command="python -c pass"), ctx, policy, allow, NoopAudit())
    assert "[harness]" not in before.output

    execute(tool_call("write", call_id="w1", path="a.py", content="x = 2\n"), ctx, policy, allow, NoopAudit())
    first = execute(tool_call("bash", call_id="b1", command="python -c pass"), ctx, policy, allow, NoopAudit())
    assert "[harness]" in first.output
    assert "finish" in first.output

    second = execute(tool_call("bash", call_id="b2", command="python -c pass"), ctx, policy, allow, NoopAudit())
    assert "[harness]" not in second.output


def test_max_cost_stops_session(tmp_path):
    """B-041：成本上限到达后结束会话，不再继续烧钱。"""
    (tmp_path / "a.py").write_text("x = 1\n")
    store, sid, status, _ = run_session(
        tmp_path,
        [completion("", [tool_call("read", call_id="c1", path="a.py")], cost=0.02)],
        run__max_cost=0.01,
    )
    assert status == "max_cost"
    assert store.session(sid).status == "max_cost"
    assert store.session(sid).cost >= 0.01


def test_keyboard_interrupt_marks_cancelled_and_releases_lock(tmp_path):
    """B-041：Ctrl+C 也要留完整的 session.end，并释放锁。"""

    def boom(messages, tools):
        raise KeyboardInterrupt

    store, sid, status, _ = run_session(tmp_path, [boom])
    assert status == "cancelled"
    assert store.session(sid).status == "cancelled"
    assert store.lock_holder(sid) is None


def test_budget_nudge_fires_once_before_max_steps(tmp_path):
    """B-037：模型容易「干完不 finish」，接近预算时要明确催一次。"""
    (tmp_path / "a.py").write_text("x = 1\n")
    store, sid, _status, _ = run_session(
        tmp_path,
        [completion("", [tool_call("read", call_id=f"r{i}", path="a.py")]) for i in range(6)]
        + [completion("", [tool_call("finish", call_id="f1", summary="done")])],
        run__max_steps=6,
        run__nudge_at=0.5,
    )
    nudges = [
        e for e in store.events(sid) if e.type == EventType.USER_MESSAGE and e.data.get("kind") == "budget"
    ]
    assert len(nudges) == 1
    assert "finish" in nudges[0].data["text"]


def test_auto_compact_happens_when_enabled(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    store, sid, _status, _ = run_session(
        tmp_path,
        [
            completion("", [tool_call("read", call_id="r1", path="a.py")]),
            completion("", [tool_call("read", call_id="r2", path="a.py")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ],
        model__window=400,
        run__compact_at=0.5,
        run__turn_keep=1,
        run__compact_cooldown=0,
    )
    assert any(e.type == EventType.CONTEXT_COMPACT for e in store.events(sid))


def test_compact_cooldown_limits_summary_calls(tmp_path):
    """回归 B-034：小窗口下不能每轮都付一次摘要调用。"""
    (tmp_path / "a.py").write_text("x = 1\n")
    store, sid, _status, _ = run_session(
        tmp_path,
        [completion("", [tool_call("read", call_id=f"r{i}", path="a.py")]) for i in range(6)]
        + [completion("", [tool_call("finish", call_id="f1", summary="done")])],
        model__window=400,
        run__compact_at=0.5,
        run__turn_keep=1,
        run__compact_cooldown=2,
    )
    compacts = [e for e in store.events(sid) if e.type == EventType.CONTEXT_COMPACT]
    assert 1 <= len(compacts) <= 3


def test_auto_compact_can_be_disabled_for_ablation(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    store, sid, _status, _ = run_session(
        tmp_path,
        [
            completion("", [tool_call("read", call_id="r1", path="a.py")]),
            completion("", [tool_call("read", call_id="r2", path="a.py")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ],
        model__window=400,
        run__compact_at=0.5,
        run__turn_keep=1,
        run__compact_enabled=False,
    )
    assert not any(e.type == EventType.CONTEXT_COMPACT for e in store.events(sid))


def test_repeated_read_hits_cache_and_shrinks_messages(tmp_path):
    body = "\n".join(f"value_{index} = {index}" for index in range(80))
    (tmp_path / "a.py").write_text(body + "\n")
    store, sid, status, _ = run_session(
        tmp_path,
        [
            completion("", [tool_call("read", call_id="r1", path="a.py")]),
            completion("", [tool_call("read", call_id="r2", path="a.py")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ],
    )
    assert status == "done"
    results = [e for e in store.events(sid) if e.type == EventType.TOOL_RESULT]
    assert results[0].data["meta"]["cache_hit"] is False
    assert results[1].data["meta"]["cache_hit"] is True
    assert len(results[1].data["output_preview"]) < len(results[0].data["output_preview"]) / 5
