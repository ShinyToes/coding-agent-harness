"""策略：有序规则表，首个命中生效。

决策本身是事件（policy.decision），审批是阻塞回调；非交互模式 ask 降级为 deny。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from harness.models import EventType, Ruling, ToolCall
from harness.tools.fs import is_under, resolve_path
from harness.tools.registry import get_tool

Approver = Callable[[ToolCall, Ruling], Literal["allow", "deny", "always"]]
Decision = Literal["allow", "ask", "deny"]

DESTRUCTIVE = re.compile(
    r"(?:"
    r"\brm\s+-[a-zA-Z]*(?:r[a-zA-Z]*f|f[a-zA-Z]*r)[a-zA-Z]*\s+(?:/|~|\*|\$HOME)(?=[\s;*]|$)"
    r"|\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/(?=[\s;*]|$)"
    r"|\bmkfs(?:\.\w+)?\b"
    r"|\bdd\s+[^\n]*\bof=/dev/"
    r"|:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"
    r"|\b(?:shutdown|poweroff|halt)\b"
    r"|\bformat\s+[a-z]:"
    r"|\bRemove-Item\b[^\n]*-Recurse[^\n]*[a-z]:\\?(?=[\s;*]|$)"
    r")",
    re.I,
)
NETWORK = re.compile(
    r"\b(?:curl|wget|ssh|scp|sftp|telnet|nc|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b"
    r"|\b(?:pip|pip3|npm|yarn|pnpm|uv pip|uv add)\s+(?:install|add|i)\b"
    r"|\bgit\s+(?:clone|fetch|pull)\b",
    re.I,
)
GIT_WRITE = re.compile(
    r"\bgit\s+(?:commit|push|reset|checkout|switch|merge|rebase|clean|tag|stash|cherry-pick|apply|am)\b",
    re.I,
)
CHAINED = re.compile(r"[;&|><`]|\$\(")
SAFE_CMD = re.compile(
    r"^\s*(?:"
    r"ls|dir|pwd|cat|type|head|tail|wc|grep|rg|find|which|where|echo|printf|"
    r"tree|stat|file|du|df|date|whoami|hostname|sort|uniq|cut|tr|"
    r"git\s+(?:status|diff|log|show|branch|rev-parse|remote|describe)|"
    r"python(?:3)?\s+(?:-V|--version)|node\s+-v|uv\s+--version|"
    r"pip(?:3)?\s+list|go\s+version|cargo\s+--version|npm\s+--version"
    r")(?:\s|$)"
)
SENSITIVE = (".ssh", ".aws", "id_rsa", ".env", "credentials", ".git/config", "npmrc", "netrc")

RULES: list[tuple[str, Callable[[ToolCall, object], bool], Decision, str]] = []


def _add(rule_id: str, predicate, decision: Decision, reason: str) -> None:
    RULES.append((rule_id, predicate, decision, reason))


def _command(call: ToolCall) -> str:
    return str(call.args.get("command") or call.args.get("cmd") or "")


def _path(call: ToolCall) -> str:
    return str(call.args.get("path") or "")


def _effect(call: ToolCall) -> str:
    try:
        return get_tool(call.name).effect
    except KeyError:
        return "unknown"


def _is_write(call: ToolCall) -> bool:
    return _effect(call) in {"write"}


def _escapes(call: ToolCall, ctx) -> bool:
    if not _is_write(call) or not _path(call):
        return False
    return not is_under(Path(ctx.workspace), resolve_path(ctx, _path(call)))


def _strings(value) -> list[str]:
    """递归取出参数里所有字符串；敏感路径可能藏在 command 或嵌套参数里。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _strings(nested)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _strings(nested)]
    return []


def _sensitive(call: ToolCall, ctx) -> bool:
    lowered = [value.lower() for value in _strings(call.args)]
    return any(marker in value for value in lowered for marker in SENSITIVE)


def _read_outside(call: ToolCall, ctx) -> bool:
    if _effect(call) != "read" or not _path(call):
        return False
    if getattr(ctx.cfg.policy, "allow_read_outside", False):
        return False
    return not is_under(Path(ctx.workspace), resolve_path(ctx, _path(call)))


def _bash(call: ToolCall) -> bool:
    return call.name in {"bash", "shell"}


def _destructive(call: ToolCall, ctx) -> bool:
    return _bash(call) and bool(DESTRUCTIVE.search(_command(call)))


def _network(call: ToolCall, ctx) -> bool:
    return _bash(call) and bool(NETWORK.search(_command(call)))


def _git_write(call: ToolCall, ctx) -> bool:
    return _bash(call) and bool(GIT_WRITE.search(_command(call)))


def _chained(call: ToolCall, ctx) -> bool:
    return _bash(call) and bool(CHAINED.search(_command(call)))


def _safe_cmd(call: ToolCall, ctx) -> bool:
    command = _command(call)
    if not command:
        return False
    return bool(SAFE_CMD.match(command)) and not _chained(call, ctx)


def _overwrite(call: ToolCall, ctx) -> bool:
    if call.name not in {"write", "patch"}:
        return False
    path = resolve_path(ctx, _path(call))
    return bool(_path(call)) and path.exists()


_add("path.escape", _escapes, "deny", "写操作目标在 workspace 之外")
_add("path.read_outside", _read_outside, "deny", "读操作目标在 workspace 之外")
_add("path.sensitive", _sensitive, "ask", "参数命中敏感清单（.env/.ssh/.aws 等）")
_add("cmd.destructive", _destructive, "deny", "命令会破坏系统或仓库外文件")
_add("cmd.network", _network, "ask", "命令会访问网络")
_add("cmd.git_write", _git_write, "ask", "命令会改动 git 历史或工作区")
_add("cmd.chained", _chained, "ask", "命令含管道/串联/重定向，需人工确认")
_add("cmd.safe_readonly", _safe_cmd, "allow", "只读命令白名单")
# harness 自己的任务状态文件（.harness/tasks/*），不是仓库代码，不该每次都弹审批
_add("task.state", lambda call, ctx: call.name in {"update_task", "progress"}, "allow", "写入 harness 任务状态")
_add("fs.overwrite", _overwrite, "ask", "覆盖已存在文件")
_add(
    "read.default",
    lambda call, ctx: _effect(call) == "read",
    "allow",
    "只读工具默认放行",
)


class Policy:
    def __init__(self, cfg, events=None) -> None:
        self.cfg = cfg
        self._allowed: set[str] = set()
        if events:
            self._load_allowlist(events)

    def _load_allowlist(self, events) -> None:
        """从事件流恢复 `always` 白名单：resume 之后不必重新问一遍。"""
        calls = {e.data.get("call_id"): e.data for e in events if e.type == EventType.TOOL_CALL}
        for event in events:
            if event.type != EventType.APPROVAL_RESPONSE or event.data.get("decision") != "always":
                continue
            call_data = calls.get(event.data.get("call_id"))
            if not call_data:
                continue
            self._allowed.add(
                f"{call_data.get('name')}:{str(call_data.get('args', {}).get('path') or call_data.get('args', {}).get('command') or '')[:160]}"
            )

    @staticmethod
    def key(call: ToolCall) -> str:
        target = _path(call) or _command(call)
        return f"{call.name}:{target[:160]}"

    def remember(self, call: ToolCall) -> None:
        self._allowed.add(self.key(call))

    def decide(self, call: ToolCall, ctx) -> Ruling:
        if self.key(call) in self._allowed:
            return Ruling(decision="allow", rule="session.allowlist", reason="本 session 已批准过同类调用")
        try:
            tool = get_tool(call.name)
        except KeyError:
            return Ruling(decision="deny", rule="tool.unknown", reason=f"未知工具 {call.name}")
        for rule_id, predicate, decision, reason in RULES:
            if predicate(call, ctx):
                return Ruling(decision=decision, rule=rule_id, reason=reason)
        if tool.effect == "read":
            if self.cfg.policy.auto_allow_read:
                return Ruling(decision="allow", rule="effect.default", reason="只读")
            return Ruling(decision="ask", rule="effect.default", reason="只读但 auto_allow_read=false")
        return Ruling(decision="ask", rule="effect.default", reason=f"{tool.effect} 类工具默认需审批")


def deny_approver(call: ToolCall, ruling: Ruling) -> Literal["allow", "deny", "always"]:
    return "deny"


def interactive_approver(call: ToolCall, ruling: Ruling) -> Literal["allow", "deny", "always"]:
    """CLI 用；非 tty 环境下也安全（返回 deny）。"""
    import sys

    if not sys.stdin.isatty():
        return "deny"
    from rich.console import Console
    from rich.prompt import Prompt

    console = Console()
    console.print(f"\n[bold yellow]审批[/] {call.name} ({ruling.rule}): {ruling.reason}")
    console.print(f"[dim]{call.args}[/]")
    answer = Prompt.ask("允许执行？", choices=["y", "n", "a"], default="n")
    answers: dict[str, Literal["allow", "deny", "always"]] = {"y": "allow", "n": "deny", "a": "always"}
    return answers[answer]


def make_approver(cfg) -> Approver:
    if cfg.policy.approval_mode == "deny":
        return deny_approver
    return interactive_approver
