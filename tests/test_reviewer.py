"""B-029 reviewer：只读审查，产出结构化风险清单。"""

from __future__ import annotations

import json

from _helpers import ScriptedLLM, completion, ctx_for, new_session, open_store, settings, tool_call
from harness.tools import get_tool

REVIEW = {
    "verdict": "request_changes",
    "risks": ["calc.py:2 未处理空列表，除零"],
    "missing_tests": ["average([])"],
    "notes": "边界未覆盖",
}


def make_ctx(tmp_path, script=None):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    (tmp_path / "calc.py").write_text("def average(values):\n    return sum(values) / len(values)\n")
    return ctx_for(store, cfg, sid, llm=ScriptedLLM(script or [])), sid


def test_reviewer_returns_structured_review(tmp_path):
    ctx, _ = make_ctx(tmp_path, [completion(json.dumps(REVIEW))])

    result = get_tool("reviewer").run({"diff": "- return sum / len\n+ return sum / (len - 1)"}, ctx)

    assert result.ok
    assert result.meta["parsed"] is True
    assert result.meta["verdict"] == "request_changes"
    assert result.meta["risks"] == [REVIEW["risks"][0]]


def test_reviewer_can_inspect_files_before_reviewing(tmp_path):
    ctx, _ = make_ctx(
        tmp_path,
        [
            completion("", [tool_call("read", call_id="r1", path="calc.py")]),
            completion(json.dumps(REVIEW)),
        ],
    )

    result = get_tool("reviewer").run({"diff": "diff --git a/calc.py b/calc.py", "focus": "空列表"}, ctx)

    assert result.meta["verdict"] == "request_changes"
    assert result.meta["turns"] == 2


def test_reviewer_falls_back_to_git_diff(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e.com", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "a.py").write_text("x = 2\n")
    ctx, _ = make_ctx(tmp_path, [completion(json.dumps(REVIEW))])

    result = get_tool("reviewer").run({}, ctx)

    assert result.ok
    assert "x = 2" in ctx.llm.calls[0]["messages"][1]["content"]


def test_reviewer_without_llm_fails_cleanly(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    result = get_tool("reviewer").run({"diff": "x"}, ctx)
    assert result.ok is False
    assert "no LLM" in result.output


def test_reviewer_handles_non_json_answer(tmp_path):
    ctx, _ = make_ctx(tmp_path, [completion("looks fine to me")])

    result = get_tool("reviewer").run({"diff": "x"}, ctx)

    assert result.ok is True
    assert result.meta["parsed"] is False
    assert "looks fine" in result.output
