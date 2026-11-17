"""litellm 包装：重试、成本、原始响应 artifact。

响应体进事件（model.response），请求体由 loop 在调用前落 artifact。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from importlib.util import find_spec
from typing import Any

from harness.models import Completion, ToolCall, canonical_json

# 让 litellm 用内置价格表：否则 import 时会去 GitHub 拉 cost map，慢且会刷警告
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

LITELLM_AVAILABLE = find_spec("litellm") is not None
litellm: Any = None
if LITELLM_AVAILABLE:
    import litellm as _litellm_module

    litellm = _litellm_module
    # 1.108 在 provider 解析失败路径上直接 print 到 stdout，会污染 h run / trace 的输出。
    litellm.suppress_debug_info = True

MAX_ATTEMPTS = 3


class ContextOverflow(RuntimeError):
    """模型上下文超限：重试没有意义，只能压缩后重来。"""


_OVERFLOW_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
)


def _is_context_overflow(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)


def _is_rate_limit(exc: Exception) -> bool:
    if litellm is not None:
        rate_limit = getattr(litellm, "exceptions", None)
        if rate_limit is not None and isinstance(exc, getattr(rate_limit, "RateLimitError", ())):
            return True
    return "429" in str(exc) or "rate limit" in str(exc).lower()


def _parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_value": parsed}


def resolve_model_name(name: str, api_base: str) -> str:
    """litellm 需要 provider 前缀；配置里允许写裸模型名（spec 的 `--model deepseek-chat`）。"""
    if "/" in name or not api_base:
        return name
    return f"deepseek/{name}" if "deepseek" in api_base.lower() else f"openai/{name}"


def _is_retryable(exc: Exception) -> bool:
    """只重试瞬时故障；把 ImportError/鉴权/参数错误重试 3 次纯属浪费。"""
    if _is_rate_limit(exc):
        return True
    text = str(exc).lower()
    if "no module named" in text or "authentication" in text or "invalid api key" in text:
        return False
    markers = (
        "timeout",
        "timed out",
        "connection",
        "disconnected",
        "temporarily",
        "overloaded",
        "internal server error",
        "service unavailable",
        "502",
        "503",
        "504",
    )
    return any(marker in text for marker in markers)


class LLM:
    def __init__(self, cfg, store, session_id: str, api_key: str | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.session_id = session_id
        self.api_key = api_key

    def _kwargs(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": resolve_model_name(self.cfg.model.name, self.cfg.model.api_base),
            "messages": messages,
            "temperature": self.cfg.model.temperature,
            "max_tokens": self.cfg.model.max_tokens,
            "timeout": 600,
        }
        if tools:
            kwargs["tools"] = tools
        if self.cfg.model.api_base:
            kwargs["api_base"] = self.cfg.model.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return kwargs

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        if not LITELLM_AVAILABLE:
            raise RuntimeError("litellm is not installed; `uv sync` before running live sessions")
        started = time.perf_counter()
        response = None
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(MAX_ATTEMPTS):
            attempts = attempt + 1
            try:
                response = litellm.completion(**self._kwargs(messages, tools))
                break
            except Exception as exc:  # noqa: BLE001 - 必须在耗尽重试后抛出
                last_error = exc
                if _is_context_overflow(exc):
                    raise ContextOverflow(str(exc)) from exc
                if not _is_retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                    break
                delay = 2**attempt * (2 if _is_rate_limit(exc) else 1)
                time.sleep(delay)
        if response is None:
            raise RuntimeError(f"model call failed after {attempts} attempt(s): {last_error}") from last_error

        latency_ms = int((time.perf_counter() - started) * 1000)
        choice = response.choices[0].message
        calls: list[ToolCall] = []
        for raw in getattr(choice, "tool_calls", None) or []:
            calls.append(
                ToolCall(
                    call_id=getattr(raw, "id", None) or f"call_{uuid.uuid4().hex[:12]}",
                    name=raw.function.name,
                    args=_parse_args(getattr(raw.function, "arguments", "")),
                )
            )
        usage_obj = getattr(response, "usage", None)
        usage: dict[str, int] = {}
        if usage_obj is not None:
            raw_usage = usage_obj.model_dump() if hasattr(usage_obj, "model_dump") else dict(usage_obj)
            usage = {str(k): int(v) for k, v in raw_usage.items() if isinstance(v, int)}
        raw_ref = self.store.put_artifact(
            self.session_id,
            "response",
            canonical_json(response.model_dump() if hasattr(response, "model_dump") else str(response)).encode(),
        )
        return Completion(
            text=choice.content or "",
            tool_calls=calls,
            usage=usage,
            model=getattr(response, "model", "") or self.cfg.model.name,
            latency_ms=latency_ms,
            raw_ref=raw_ref,
            cost=self.cost(response),
        )

    def cost(self, response: Any) -> float:
        try:
            return float(litellm.completion_cost(completion_response=response))
        except Exception:  # noqa: BLE001 - provider 未注册成本模型时记账为 0，不能阻断主循环
            return 0.0
