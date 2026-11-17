"""finish 的证据门槛（B-010）：改过文件就必须有成功的 bash 验证，或 feature list 全绿。"""

from __future__ import annotations

from _helpers import (
    FakeSandbox,
    ScriptedLLM,
    completion,
    ctx_for,
    new_session,
    open_store,
    seed_tool,
    settings,
    tool_call,
)
from harness.audit import NoopAudit
from harness.loop import run
from harness.models import EventType
from harness.tools import get_tool


def make_ctx(tmp_path, **overrides):
    cfg = settings(tmp_path, **overrides)
    store = open_store(tmp_path)
    sid = new_session(store)
    return cfg, store, sid, ctx_for(store, cfg, sid)


def test_finish_rejected_after_edit_without_verification(tmp_path):
    cfg, store, sid, ctx = make_ctx(tmp_path)
    seed_tool(store, sid, "write", path="a.py", content="x = 1\n")

    result = get_tool("finish").run({"summary": "done"}, ctx)

    assert result.ok is False
    assert "rejected" in result.output
    assert "finished" not in ctx.state


def test_finish_ok_after_successful_bash(tmp_path):
    cfg, store, sid, ctx = make_ctx(tmp_path)
    seed_tool(store, sid, "write", path="a.py", content="x = 1\n")
    seed_tool(store, sid, "bash", command="python -m pytest -q")

    result = get_tool("finish").run({"summary": "done"}, ctx)

    assert result.ok is True
    assert ctx.state["finished"]["summary"] == "done"


def test_failed_bash_does_not_count_as_verification(tmp_path):
    cfg, store, sid, ctx = make_ctx(tmp_path)
    seed_tool(store, sid, "patch", path="a.py", patch="...")
    seed_tool(store, sid, "bash", ok=False, command="python -m pytest -q")

    assert get_tool("finish").run({"summary": "done"}, ctx).ok is False


def test_finish_ok_without_edits(tmp_path):
    cfg, store, sid, ctx = make_ctx(tmp_path)
    seed_tool(store, sid, "read", path="a.py")

    assert get_tool("finish").run({"summary": "read only"}, ctx).ok is True


def test_finish_ok_when_feature_list_all_passes(tmp_path):
    from harness import tasks

    cfg, store, sid, ctx = make_ctx(tmp_path)
    seed_tool(store, sid, "write", path="a.py", content="x = 1\n")
    tasks.set_feature(store, sid, tmp_path, "feature-a", True, "covered by test_a")

    assert get_tool("finish").run({"summary": "done"}, ctx).ok is True


def test_finish_gate_can_be_disabled(tmp_path):
    cfg, store, sid, ctx = make_ctx(tmp_path, run__require_verification=False)
    seed_tool(store, sid, "write", path="a.py", content="x = 1\n")

    assert get_tool("finish").run({"summary": "done"}, ctx).ok is True


def test_rejected_finish_does_not_end_the_loop(tmp_path):
    """被拒后模型还有机会补验证：write → finish(拒) → bash → finish(过)。"""
    (tmp_path / "a.py").write_text("x = 1\n")
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    llm = ScriptedLLM(
        [
            completion("", [tool_call("write", call_id="w1", path="a.py", content="x = 2\n")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
            completion("", [tool_call("bash", call_id="b1", command="python -c pass")]),
            completion("", [tool_call("finish", call_id="f2", summary="verified")]),
        ]
    )

    status = run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=lambda c, r: "allow", audit=NoopAudit())

    assert status == "done"
    finishes = [
        e
        for e in store.events(sid)
        if e.type == EventType.TOOL_RESULT and e.data.get("meta", {}).get("status") == "done"
    ]
    rejected = [
        e
        for e in store.events(sid)
        if e.type == EventType.TOOL_RESULT and e.data.get("meta", {}).get("rejected")
    ]
    assert len(rejected) == 1
    assert len(finishes) == 1
