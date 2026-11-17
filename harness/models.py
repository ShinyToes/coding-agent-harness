"""事件与领域模型。

约束：事件 schema 只能加字段，不能改名/删除；改名即破坏 replay。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SESSION_START = "session.start"
    USER_MESSAGE = "user.message"
    MODEL_REQUEST = "model.request"
    MODEL_RESPONSE = "model.response"
    TOOL_CALL = "tool.call"
    POLICY_DECISION = "policy.decision"
    APPROVAL_REQUEST = "approval.request"
    APPROVAL_RESPONSE = "approval.response"
    TOOL_RESULT = "tool.result"
    CONTEXT_COMPACT = "context.compact"
    TASK_UPDATE = "task.update"
    ERROR = "error"
    SESSION_END = "session.end"


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_json(obj: Any) -> str:
    """稳定序列化：idem_key / config_hash / replay 指纹都依赖它。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def idem_key(session_id: str, seq: int, name: str, args: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({"s": session_id, "q": seq, "n": name, "a": args}))[:16]


class Event(BaseModel):
    id: int | None = None
    session_id: str
    seq: int
    type: EventType
    ts: datetime
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def call_id(self) -> str | None:
        cid = self.data.get("call_id")
        return str(cid) if cid else None


class ToolCall(BaseModel):
    call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    idem_key: str = ""

    @classmethod
    def build(
        cls,
        session_id: str,
        seq: int,
        name: str,
        args: dict[str, Any],
        call_id: str | None = None,
    ) -> ToolCall:
        cid = call_id or f"call_{idem_key(session_id, seq, name, args)}"
        return cls(call_id=cid, name=name, args=args, idem_key=idem_key(session_id, seq, name, args))


class Completion(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    model: str = ""
    latency_ms: int = 0
    raw_ref: str = ""
    cost: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("prompt_tokens", 0))

    @property
    def completion_tokens(self) -> int:
        return int(self.usage.get("completion_tokens", 0))


class Ruling(BaseModel):
    decision: Literal["allow", "ask", "deny"]
    rule: str
    reason: str


class ExecResult(BaseModel):
    rc: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    truncated: bool = False


class ToolResult(BaseModel):
    ok: bool
    output: str
    meta: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    task: str = ""
    repo: str = ""
    base_commit: str = ""
    model: str = ""
    status: str = "running"
    parent_id: str | None = None
    created_at: datetime | None = None
    last_commit: str | None = None
    calibration: float = 3.0
    tokens_total: int = 0
    cost: float = 0.0
