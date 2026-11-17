"""Resume：三种计划、崩溃不重复副作用、纯读可重跑、git 漂移检查。"""

from __future__ import annotations

import pytest

from _helpers import (
    FakeSandbox,
    ScriptedLLM,
    completion,
    ctx_for,
    model_response,
    new_session,
    open_store,
    settings,
    tool_call,
)
from harness.audit import NoopAudit
from harness.journal import rebuild_read_cache
from harness.loop import check_drift, execute, plan_resume, recover_pending, run, target_hashes
from harness.models import EventType
from harness.policy import Policy, deny_approver


def test_plan_resume_done(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    store.append(sid, EventType.SESSION_END, {"status": "finished", "reason": "", "steps": 1, "tokens_total": 0})
    assert plan_resume(store.events(sid)) == "done"


def test_plan_resume_continue_calls_when_call_has_no_result(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    call = tool_call("read", call_id="c1", path="a.py")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    assert plan_resume(store.events(sid)) == "continue_calls"


def test_plan_resume_call_model_on_fresh_session(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    assert plan_resume(store.events(sid)) == "call_model"


def test_interrupted_write_is_not_replayed_but_records_error(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    target = tmp_path / "out.txt"
    target.write_text("v1")

    call = tool_call("write", call_id="w1", path="out.txt", content="v3")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    target.write_text("v2")  # 崩溃点：文件已落盘，tool.result 没写

    ctx = ctx_for(store, cfg, sid)
    recovered = recover_pending(store, cfg, ctx, Policy(cfg), deny_approver, NoopAudit())

    assert recovered == 1
    assert target.read_text() == "v2"  # 没有重跑（重跑会写成 v3）
    events = store.events(sid)
    assert [e.type for e in events].count(EventType.TOOL_CALL) == 1
    errors = [e for e in events if e.type == EventType.ERROR]
    assert len(errors) == 1
    assert errors[0].data["where"] == "resume"
    results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(results) == 1
    assert results[0].data["ok"] is False
    assert "NOT re-executed" in results[0].data["output_preview"]


def test_interrupted_read_is_replayed_once(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    (tmp_path / "a.py").write_text("print('hi')\n")
    call = tool_call("read", call_id="r1", path="a.py")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())

    ctx = ctx_for(store, cfg, sid)
    recover_pending(store, cfg, ctx, Policy(cfg), deny_approver, NoopAudit())

    events = store.events(sid)
    assert [e.type for e in events].count(EventType.TOOL_CALL) == 1
    results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert len(results) == 1 and results[0].data["ok"] is True
    assert "print('hi')" in results[0].data["output_preview"]


def test_run_continues_after_interrupted_session(tmp_path):
    cfg = settings(tmp_path)
    cfg.run.max_steps = 5
    store = open_store(tmp_path)
    sid = new_session(store)
    (tmp_path / "a.py").write_text("x = 1\n")
    call = tool_call("read", call_id="r1", path="a.py")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())

    llm = ScriptedLLM([completion("", [tool_call("finish", call_id="f1", summary="done")])])
    status = run(sid, store, cfg, llm=llm, sandbox=ctx_for(store, cfg, sid).sandbox, approver=deny_approver, audit=NoopAudit())
    assert status == "done"
    assert store.session(sid).status == "done"


def test_drift_check_appends_error_and_hint(tmp_path, monkeypatch):
    store = open_store(tmp_path)
    sid = new_session(store, base_commit="AAA")
    cfg = settings(tmp_path)
    monkeypatch.setattr("harness.tasks.head_or_empty", lambda cwd: "BBB")

    check_drift(store, sid, cfg)

    errors = [e for e in store.events(sid) if e.type == EventType.ERROR]
    assert len(errors) == 1
    assert errors[0].data["where"] == "resume"
    assert "AAA->BBB" in errors[0].data["message"]
    assert "git diff" in cfg.drift_notice
    assert store.session(sid).last_commit == "BBB"


def test_drift_notice_reaches_system_prompt(tmp_path, monkeypatch):
    """回归 B-001：loop 每轮重写 task_brief，不能把漂移提示冲掉。"""
    store = open_store(tmp_path)
    sid = new_session(store, base_commit="AAA")
    cfg = settings(tmp_path)
    monkeypatch.setattr("harness.tasks.head_or_empty", lambda cwd: "BBB")
    llm = ScriptedLLM([completion("", [tool_call("finish", call_id="f1", summary="done")])])

    run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=NoopAudit())

    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "## 漂移警告" in system_prompt
    assert "git diff" in system_prompt


def test_drift_check_skips_non_git_workspace(tmp_path, monkeypatch):
    store = open_store(tmp_path)
    sid = new_session(store, base_commit="AAA")
    cfg = settings(tmp_path)
    monkeypatch.setattr("harness.tasks.head_or_empty", lambda cwd: "")
    check_drift(store, sid, cfg)
    assert not [e for e in store.events(sid) if e.type == EventType.ERROR]


@pytest.mark.parametrize("tool_effect", ["write", "exec"])
def test_recover_does_not_rerun_side_effect_tools(tmp_path, tool_effect):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    name = "write" if tool_effect == "write" else "bash"
    args = {"path": "x.txt", "content": "y"} if tool_effect == "write" else {"command": "touch x.txt"}
    call = tool_call(name, call_id="z1", **args)
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())

    sandbox = ctx_for(store, cfg, sid).sandbox
    recover_pending(store, cfg, ctx_for(store, cfg, sid, sandbox=sandbox), Policy(cfg), deny_approver, NoopAudit())
    assert sandbox.commands == []
    assert not (tmp_path / "x.txt").exists()


def test_read_cache_rebuilt_after_resume(tmp_path):
    """B-014：resume 后同一文件仍命中缓存，而不是全量重读。"""
    body = "\n".join(f"value_{index} = {index}" for index in range(80))
    (tmp_path / "a.py").write_text(body + "\n")
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    call = tool_call("read", call_id="r1", path="a.py")

    execute(call, ctx_for(store, cfg, sid), Policy(cfg), deny_approver, NoopAudit())

    fresh_ctx = ctx_for(store, cfg, sid)
    assert execute(call, fresh_ctx, Policy(cfg), deny_approver, NoopAudit()).meta["cache_hit"] is False

    resumed_ctx = ctx_for(store, cfg, sid)
    assert rebuild_read_cache(store, resumed_ctx) >= 1
    result = execute(call, resumed_ctx, Policy(cfg), deny_approver, NoopAudit())
    assert result.meta["cache_hit"] is True


def test_read_cache_not_rebuilt_for_changed_file(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n")
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    call = tool_call("read", call_id="r1", path="a.py")
    execute(call, ctx_for(store, cfg, sid), Policy(cfg), deny_approver, NoopAudit())

    (tmp_path / "a.py").write_text("one\ntwo\nCHANGED\n")
    resumed_ctx = ctx_for(store, cfg, sid)
    rebuild_read_cache(store, resumed_ctx)
    result = execute(call, resumed_ctx, Policy(cfg), deny_approver, NoopAudit())
    assert result.meta["cache_hit"] is False
    assert "CHANGED" in result.output


def _seed_pending_write(tmp_path, content: str, existing: str = "v1"):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    target = tmp_path / "out.txt"
    target.write_text(existing)
    call = tool_call("write", call_id="w1", path="out.txt", content=content)
    ctx = ctx_for(store, cfg, sid)
    hashes = target_hashes(call, ctx)
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, {**call.model_dump(), **hashes})
    return cfg, store, sid, target


def test_resume_detects_write_already_applied(tmp_path):
    """文件内容已等于 sha_after：崩溃前写成功，恢复时补一条成功结果，不重写。"""
    cfg, store, sid, target = _seed_pending_write(tmp_path, "v3")
    target.write_text("v3")

    ctx = ctx_for(store, cfg, sid)
    recover_pending(store, cfg, ctx, Policy(cfg), deny_approver, NoopAudit())

    results = [e for e in store.events(sid) if e.type == EventType.TOOL_RESULT]
    assert len(results) == 1
    assert results[0].data["ok"] is True
    assert "already applied" in results[0].data["output_preview"]
    assert not [e for e in store.events(sid) if e.type == EventType.ERROR]
    assert target.read_text() == "v3"


def test_resume_reruns_write_when_target_untouched(tmp_path):
    """文件仍是 sha_before：可以安全重跑。"""
    cfg, store, sid, target = _seed_pending_write(tmp_path, "v3")

    ctx = ctx_for(store, cfg, sid)
    recover_pending(store, cfg, ctx, Policy(cfg), lambda call, ruling: "allow", NoopAudit())

    assert target.read_text() == "v3"
    results = [e for e in store.events(sid) if e.type == EventType.TOOL_RESULT]
    assert len(results) == 1 and results[0].data["ok"] is True
    assert [e.type for e in store.events(sid)].count(EventType.TOOL_CALL) == 1


def test_resume_reports_unknown_when_file_changed_by_someone_else(tmp_path):
    cfg, store, sid, target = _seed_pending_write(tmp_path, "v3")
    target.write_text("v2")  # 既不是 before 也不是 after

    ctx = ctx_for(store, cfg, sid)
    recover_pending(store, cfg, ctx, Policy(cfg), lambda call, ruling: "allow", NoopAudit())

    assert target.read_text() == "v2"
    errors = [e for e in store.events(sid) if e.type == EventType.ERROR]
    assert len(errors) == 1
    result = next(e for e in store.events(sid) if e.type == EventType.TOOL_RESULT)
    assert result.data["ok"] is False


def test_target_hashes_track_patch_result(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    (tmp_path / "f.py").write_text("a = 1\n")
    call = tool_call("patch", call_id="p1", path="f.py", patch="@@ -1 +1 @@\n-a = 1\n+a = 2\n")
    hashes = target_hashes(call, ctx_for(store, cfg, sid))
    assert hashes["sha_before"]
    assert hashes["sha_after"] and hashes["sha_after"] != hashes["sha_before"]
    assert hashes["target_path"].endswith("f.py")
