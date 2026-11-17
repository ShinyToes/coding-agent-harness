"""工具注册表 + OpenAI JSON schema 导出。

函数优先：工具是对象，但注册、查询都是模块级函数。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from harness.models import ToolResult, canonical_json


@dataclass
class ToolCtx:
    session_id: str
    workspace: Path
    sandbox: Any
    store: Any
    cfg: Any
    state: dict[str, Any] = field(default_factory=dict)
    llm: Any = None
    audit: Any = None


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    effect: str

    def run(self, args: dict[str, Any], ctx: ToolCtx) -> ToolResult: ...


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if not tool.name:
        raise ValueError("tool.name must be non-empty")
    TOOLS[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool:
    try:
        return TOOLS[name]
    except KeyError:
        raise KeyError(f"unknown tool {name!r}; available: {sorted(TOOLS)}") from None


def tool_schemas(only: set[str] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in sorted(TOOLS):
        if only is not None and name not in only:
            continue
        tool = TOOLS[name]
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return out


def tools_hash() -> str:
    return hashlib.sha256(canonical_json(tool_schemas()).encode()).hexdigest()[:12]
