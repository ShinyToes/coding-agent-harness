"""H4：edit_lines 工具的行为、注册与策略。"""

from __future__ import annotations

from harness.config import Settings
from harness.models import ToolCall
from harness.policy import Policy
from harness.store import Store
from harness.tools import TOOLS, ToolCtx, tool_schemas


def make_ctx(tmp_path):
    cfg = Settings().with_workspace(tmp_path)
    store = Store(tmp_path / ".harness" / "h.db")
    session_id = store.create_session("task", str(tmp_path), "fake")
    return ToolCtx(session_id=session_id, workspace=tmp_path, sandbox=None, store=store, cfg=cfg)


def test_edit_lines_tool_is_registered():
    assert "edit_lines" in TOOLS
    names = {schema["function"]["name"] for schema in tool_schemas()}
    assert "edit_lines" in names
    assert TOOLS["edit_lines"].effect == "write"


def test_edit_lines_replaces_inclusive_range(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("1\n2\n3\n4\n5\n")
    result = TOOLS["edit_lines"].run(
        {"path": "a.txt", "start": 2, "end": 3, "content": "X\nY"}, make_ctx(tmp_path)
    )
    assert result.ok, result.output
    assert target.read_text() == "1\nX\nY\n4\n5\n"


def test_edit_lines_keeps_no_trailing_newline(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("1\n2\n3")
    result = TOOLS["edit_lines"].run(
        {"path": "a.txt", "start": 2, "end": 2, "content": "X"}, make_ctx(tmp_path)
    )
    assert result.ok, result.output
    assert target.read_text() == "1\nX\n3"


def test_edit_lines_rejects_bad_input(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("1\n2\n")
    assert TOOLS["edit_lines"].run(
        {"path": "missing.txt", "start": 1, "end": 1, "content": "x"}, ctx
    ).ok is False
    assert TOOLS["edit_lines"].run(
        {"path": "a.txt", "start": 0, "end": 1, "content": "x"}, ctx
    ).ok is False
    assert TOOLS["edit_lines"].run(
        {"path": "a.txt", "start": 3, "end": 2, "content": "x"}, ctx
    ).ok is False


def test_edit_lines_respects_policy(tmp_path):
    (tmp_path / "a.txt").write_text("1\n2\n")
    ctx = make_ctx(tmp_path)
    policy = Policy(ctx.cfg)
    inside = policy.decide(
        ToolCall(call_id="c1", name="edit_lines", args={"path": "a.txt", "start": 1, "end": 1, "content": "x"}), ctx
    )
    outside = policy.decide(
        ToolCall(call_id="c2", name="edit_lines", args={"path": "/etc/passwd", "start": 1, "end": 1, "content": "x"}), ctx
    )
    assert inside.decision == "ask"
    assert outside.decision == "deny"
