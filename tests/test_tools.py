"""Tools：read 缓存、write、patch 两种格式、list/grep/glob、bash 走 sandbox。"""

from __future__ import annotations

from _helpers import FakeSandbox, ctx_for, new_session, open_store, settings
from harness.models import ExecResult
from harness.tools import get_tool


def test_read_cache_can_be_disabled_for_ablation(tmp_path):
    body = "\n".join(f"value_{index} = {index}" for index in range(80))
    (tmp_path / "a.py").write_text(body + "\n")
    cfg = settings(tmp_path, run__read_cache=False)
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))
    tool = get_tool("read")

    first = tool.run({"path": "a.py"}, ctx)
    second = tool.run({"path": "a.py"}, ctx)

    assert first.meta["cache_hit"] is False
    assert second.meta["cache_hit"] is False
    assert second.output == first.output


def test_read_second_time_hits_cache(tmp_path):
    body = "\n".join(f"value_{index} = {index}" for index in range(80))
    (tmp_path / "a.py").write_text(body + "\n")
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    ctx = ctx_for(store, cfg, sid)
    tool = get_tool("read")

    first = tool.run({"path": "a.py"}, ctx)
    second = tool.run({"path": "a.py"}, ctx)

    assert first.ok and first.meta["cache_hit"] is False
    assert second.ok and second.meta["cache_hit"] is True
    assert second.output.startswith("[unchanged since seq")
    assert "value_0 = 0" in second.output  # 头 3 行摘要
    assert len(second.output) < len(first.output) / 5


def test_read_respects_line_range(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    store, cfg = open_store(tmp_path), settings(tmp_path)
    tool = get_tool("read")
    result = tool.run({"path": "a.txt", "start": 3, "end": 5}, ctx_for(store, cfg, new_session(store)))
    assert "line3" in result.output and "line5" in result.output
    assert "line6" not in result.output


def test_write_creates_parents(tmp_path):
    store, cfg = open_store(tmp_path), settings(tmp_path)
    tool = get_tool("write")
    result = tool.run({"path": "src/deep/new.py", "content": "x = 1\n"}, ctx_for(store, cfg, new_session(store)))
    assert result.ok
    assert (tmp_path / "src" / "deep" / "new.py").read_text() == "x = 1\n"
    assert result.meta["created"] is True


def test_patch_search_block(tmp_path):
    (tmp_path / "f.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n")
    store, cfg = open_store(tmp_path), settings(tmp_path)
    patch_text = "<<<<<<< SEARCH\ndef foo():\n    return 1\n=======\ndef foo():\n    return 42\n>>>>>>> REPLACE"
    result = get_tool("patch").run({"path": "f.py", "patch": patch_text}, ctx_for(store, cfg, new_session(store)))
    assert result.ok, result.output
    text = (tmp_path / "f.py").read_text()
    assert "return 42" in text and "return 1\n" not in text


def test_patch_missing_search_block_fails_without_writing(tmp_path):
    (tmp_path / "f.py").write_text("a = 1\n")
    store, cfg = open_store(tmp_path), settings(tmp_path)
    patch_text = "<<<<<<< SEARCH\nnope\n=======\nyep\n>>>>>>> REPLACE"
    result = get_tool("patch").run({"path": "f.py", "patch": patch_text}, ctx_for(store, cfg, new_session(store)))
    assert result.ok is False
    assert (tmp_path / "f.py").read_text() == "a = 1\n"


def test_patch_unified_diff(tmp_path):
    (tmp_path / "f.txt").write_text("line1\nline2\nline3\n")
    store, cfg = open_store(tmp_path), settings(tmp_path)
    diff = "--- a/f.txt\n+++ b/f.txt\n@@ -1,3 +1,3 @@\n line1\n-line2\n+LINE2\n line3\n"
    result = get_tool("patch").run({"path": "f.txt", "patch": diff}, ctx_for(store, cfg, new_session(store)))
    assert result.ok, result.output
    assert (tmp_path / "f.txt").read_text() == "line1\nLINE2\nline3\n"


def test_list_skips_heavy_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("a")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    store, cfg = open_store(tmp_path), settings(tmp_path)
    result = get_tool("list").run({"path": ".", "depth": 3}, ctx_for(store, cfg, new_session(store)))
    assert "src/" in result.output and "src/a.py" in result.output
    assert ".git" not in result.output


def test_grep_and_glob(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def target():\n    pass\n")
    (tmp_path / "b.txt").write_text("target in text\n")
    store, cfg = open_store(tmp_path), settings(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store))

    grep = get_tool("grep").run({"pattern": "target", "path": ".", "glob": "*.py"}, ctx)
    assert grep.ok
    assert "src/a.py:1:" in grep.output
    assert "b.txt" not in grep.output

    glob = get_tool("glob").run({"pattern": "**/*.py"}, ctx)
    assert glob.ok and "src/a.py" in glob.output


def test_bash_goes_through_sandbox(tmp_path):
    store, cfg = open_store(tmp_path), settings(tmp_path)
    sandbox = FakeSandbox(ExecResult(rc=0, stdout="hello"))
    result = get_tool("bash").run({"command": "echo hello"}, ctx_for(store, cfg, new_session(store), sandbox=sandbox))
    assert result.ok and result.output == "hello"
    assert sandbox.commands and sandbox.commands[0][0] == "echo hello"


def test_bash_failure_is_not_ok(tmp_path):
    store, cfg = open_store(tmp_path), settings(tmp_path)
    sandbox = FakeSandbox(ExecResult(rc=2, stdout="", stderr="boom"))
    result = get_tool("bash").run({"command": "false"}, ctx_for(store, cfg, new_session(store), sandbox=sandbox))
    assert result.ok is False and "boom" in result.output


def test_read_artifact_recovers_truncated_output(tmp_path):
    store, cfg = open_store(tmp_path), settings(tmp_path)
    sid = new_session(store)
    body = "HEAD-" + "x" * 5_000 + "-TAIL"
    aid = store.put_artifact(sid, "tool_output", body.encode())
    tool = get_tool("read_artifact")
    ctx = ctx_for(store, cfg, sid)

    head = tool.run({"artifact_id": aid, "limit": 100}, ctx)
    assert head.ok
    assert "remaining=" in head.output and head.meta["remaining"] > 0

    tail = tool.run({"artifact_id": aid, "offset": len(body) - 10, "limit": 100}, ctx)
    assert tail.ok and "TAIL" in tail.output
    assert tool.run({"artifact_id": "missing"}, ctx).ok is False


def test_repo_map_tool(tmp_path):
    (tmp_path / "m.py").write_text("class Widget:\n    pass\n\ndef helper():\n    pass\n")
    store, cfg = open_store(tmp_path), settings(tmp_path)
    result = get_tool("repo_map").run({}, ctx_for(store, cfg, new_session(store)))
    assert result.ok and "Widget" in result.output and "helper" in result.output
