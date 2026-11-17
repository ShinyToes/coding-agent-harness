"""测试共用的假实现与构造器（不调真模型）。"""

from __future__ import annotations

from pathlib import Path

from harness.config import Settings
from harness.models import Completion, EventType, ExecResult, ToolCall
from harness.store import Store
from harness.tools import ToolCtx


class FakeSandbox:
    def __init__(self, result: ExecResult | None = None) -> None:
        self.result = result or ExecResult(rc=0, stdout="ok")
        self.commands: list[tuple[str, str, int]] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def exec(self, cmd: str, cwd: str, timeout: int) -> ExecResult:
        self.commands.append((cmd, cwd, timeout))
        return self.result


def tool_call(name: str, call_id: str | None = None, **args) -> ToolCall:
    return ToolCall(call_id=call_id or f"call_{name}", name=name, args=args)


def completion(
    text: str = "", calls: list[ToolCall] | None = None, prompt: int = 100, output: int = 10, cost: float = 0.0
) -> Completion:
    return Completion(
        text=text,
        tool_calls=list(calls or []),
        usage={"prompt_tokens": prompt, "completion_tokens": output},
        model="fake",
        latency_ms=1,
        cost=cost,
    )


class ScriptedLLM:
    """按脚本顺序返回 Completion；用于验证循环而不是模型能力。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.script:
            return completion("(script exhausted)")
        item = self.script.pop(0)
        return item(messages, tools) if callable(item) else item


def settings(tmp_path: Path, **overrides) -> Settings:
    cfg = Settings().with_workspace(tmp_path)
    for key, value in overrides.items():
        section, _, field = key.partition("__")
        setattr(getattr(cfg, section), field, value)
    return cfg


def open_store(tmp_path: Path) -> Store:
    return Store(tmp_path / ".harness" / "harness.db")


def new_session(store: Store, task: str = "test task", text: str | None = None, base_commit: str = "") -> str:
    sid = store.create_session(task, "repo", "fake-model", base_commit=base_commit)
    store.append(
        sid,
        EventType.SESSION_START,
        {"task": task, "repo": "repo", "base_commit": base_commit, "model": "fake-model", "config_hash": "x"},
    )
    store.append(sid, EventType.USER_MESSAGE, {"text": text or task})
    return sid


def ctx_for(store: Store, cfg, sid: str, llm=None, sandbox=None) -> ToolCtx:
    return ToolCtx(
        session_id=sid,
        workspace=Path(cfg.workspace),
        sandbox=sandbox or FakeSandbox(),
        store=store,
        cfg=cfg,
        llm=llm,
    )


def model_response(calls: list[ToolCall], text: str = "") -> dict:
    return {
        "text": text,
        "tool_calls": [c.model_dump() for c in calls],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        "model": "fake",
        "latency_ms": 1,
        "raw_ref": "",
    }


def tool_result_data(call: ToolCall, output: str, ok: bool = True) -> dict:
    chars = len(output)
    big = chars > 1200
    return {
        "call_id": call.call_id,
        "ok": ok,
        "output_preview": output[:400] if big else output,
        "tail": output[-400:] if big else "",
        "artifact_id": "aid",
        "chars": chars,
        "duration_ms": 0,
        "truncated": big,
    }


def seed_tool(store: Store, sid: str, name: str, ok: bool = True, call_id: str | None = None, **args) -> ToolCall:
    """直接往事件流里塞一对 tool.call / tool.result，用于测恢复与 finish 门槛。"""
    call = tool_call(name, call_id=call_id, **args)
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    store.append(sid, EventType.TOOL_RESULT, tool_result_data(call, "ok" if ok else "failed", ok=ok))
    return call
