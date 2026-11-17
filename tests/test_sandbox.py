"""Sandbox：跨平台 shell、输出上限、超时杀进程树。"""

from __future__ import annotations

import os
import sys

import pytest

from _helpers import ctx_for, new_session, open_store, settings
from harness.models import EventType
from harness.sandbox import LocalSandbox, MicrosandboxSandbox, make_sandbox, truncate_output
from harness.tools import get_tool


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def test_truncate_output_keeps_head_and_tail():
    body = "H" * 500 + "M" * 900 + "T" * 500
    text, truncated = truncate_output(body, 400)
    assert truncated is True
    assert text.startswith("H" * 100)
    assert "truncated" in text
    assert text.endswith("T" * 100)


def test_truncate_output_noop_when_small():
    text, truncated = truncate_output("short", 100)
    assert text == "short" and truncated is False


def test_local_sandbox_echo(tmp_path):
    sandbox = LocalSandbox(tmp_path)
    result = sandbox.exec("echo hello-sandbox", str(tmp_path), 10)
    assert result.rc == 0
    assert "hello-sandbox" in result.stdout


def test_local_sandbox_caps_output(tmp_path):
    sandbox = LocalSandbox(tmp_path, max_output=2_000)
    code = _script(tmp_path, "noise.py", "print('x' * 200000)")
    result = sandbox.exec(code, str(tmp_path), 30)
    assert result.truncated is True
    assert len(result.stdout) < 5_000
    assert "truncated" in result.stdout


def test_local_sandbox_timeout_kills_process(tmp_path):
    sandbox = LocalSandbox(tmp_path)
    code = _script(tmp_path, "sleep.py", "import time\ntime.sleep(30)\n")
    result = sandbox.exec(code, str(tmp_path), 1)
    assert result.timed_out is True
    assert result.duration_ms < 15_000


def test_bash_tool_marks_truncation(tmp_path):
    cfg = settings(tmp_path)
    cfg.sandbox.max_output = 2_000
    store = open_store(tmp_path)
    ctx = ctx_for(store, cfg, new_session(store), sandbox=LocalSandbox(tmp_path, max_output=2_000))
    code = _script(tmp_path, "noise.py", "print('y' * 200000)")
    result = get_tool("bash").run({"command": code}, ctx)
    assert result.ok is True
    assert result.meta["truncated"] is True


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe 只在 Windows 上验证")
def test_local_sandbox_explicit_cmd_shell(tmp_path):
    sandbox = LocalSandbox(tmp_path, shell="cmd")
    result = sandbox.exec("echo cmd-ok", str(tmp_path), 10)
    assert result.rc == 0
    assert "cmd-ok" in result.stdout


def test_msb_missing_falls_back_to_local(tmp_path, monkeypatch):
    """没有 msb 可执行文件时必须降级到 LocalSandbox，并记一条 error 事件。"""
    monkeypatch.setattr("harness.sandbox.shutil.which", lambda name: None)
    cfg = settings(tmp_path, sandbox__kind="msb")
    store = open_store(tmp_path)
    sid = new_session(store)

    sandbox = make_sandbox(cfg, store, sid)

    assert isinstance(sandbox, LocalSandbox)
    errors = [event for event in store.events(sid) if event.type == EventType.ERROR]
    assert errors and "fallback to local" in errors[-1].data["message"]


def test_msb_start_failure_falls_back_and_keeps_local_working(tmp_path, monkeypatch):
    """msb 存在但启动失败（如无 WHP）也要降级，且降级后的 sandbox 能执行命令。"""

    def boom(self):
        raise RuntimeError("WHP not available")

    monkeypatch.setattr("harness.sandbox.shutil.which", lambda name: "msb")
    monkeypatch.setattr(MicrosandboxSandbox, "start", boom)
    cfg = settings(tmp_path, sandbox__kind="msb")
    store = open_store(tmp_path)
    sid = new_session(store)

    sandbox = make_sandbox(cfg, store, sid)

    assert isinstance(sandbox, LocalSandbox)
    sandbox.start()
    try:
        result = sandbox.exec("echo fallback-ok", str(tmp_path), 10)
    finally:
        sandbox.stop()
    assert result.rc == 0
    assert "fallback-ok" in result.stdout
