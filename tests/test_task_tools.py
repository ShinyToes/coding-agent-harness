"""任务状态工具（B-006）：模型自己维护 feature list 与 progress，而不是靠手改文件。"""

from __future__ import annotations

from _helpers import (
    FakeSandbox,
    ScriptedLLM,
    completion,
    ctx_for,
    new_session,
    open_store,
    settings,
    tool_call,
)
from harness import tasks
from harness.audit import NoopAudit
from harness.loop import run
from harness.models import EventType
from harness.policy import Policy, deny_approver
from harness.tools import get_tool


def test_update_task_writes_feature_list_and_event(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    ctx = ctx_for(store, cfg, sid)

    result = get_tool("update_task").run(
        {"feature_id": "parse-date-tz", "passes": True, "evidence": "test_parse passes"}, ctx
    )

    assert result.ok
    assert tasks.load_features(tmp_path) == [
        {"id": "parse-date-tz", "passes": True, "evidence": "test_parse passes"}
    ]
    assert store.events(sid)[-1].type == EventType.TASK_UPDATE


def test_update_task_requires_feature_id(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    assert get_tool("update_task").run({"passes": True}, ctx).ok is False


def test_progress_appends_and_feeds_the_brief(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    ctx = ctx_for(store, cfg, sid)

    assert get_tool("progress").run({"text": "已改 calc.py，check.py 通过"}, ctx).ok
    assert "check.py 通过" in tasks.progress_tail(tmp_path)


def test_task_state_writes_are_allowed_without_approval(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    policy = Policy(cfg)

    for call in (
        tool_call("update_task", feature_id="a", passes=True),
        tool_call("progress", text="step 1"),
    ):
        ruling = policy.decide(call, ctx)
        assert ruling.decision == "allow"
        assert ruling.rule == "task.state"


def test_model_can_close_task_via_feature_list(tmp_path):
    """update_task 全绿后 finish 放行，不需要跑 bash。"""
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    llm = ScriptedLLM(
        [
            completion("", [tool_call("update_task", call_id="u1", feature_id="docs", passes=True,
                                      evidence="README updated")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ]
    )

    status = run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=NoopAudit())

    assert status == "done"
    assert tasks.load_features(tmp_path)[0]["passes"] is True
