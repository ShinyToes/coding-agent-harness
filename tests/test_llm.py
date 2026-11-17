"""LLM：响应解析、重试判定、provider 前缀（不调真模型）。"""

from __future__ import annotations

from harness.llm import _is_rate_limit, _is_retryable, _parse_args, resolve_model_name


def test_parse_args_handles_empty_and_bad_json():
    assert _parse_args(None) == {}
    assert _parse_args("") == {}
    assert _parse_args('{"path": "a.py"}') == {"path": "a.py"}
    assert _parse_args("not json") == {"_raw_arguments": "not json"}
    assert _parse_args('["x"]') == {"_value": ["x"]}


def test_rate_limit_detection():
    assert _is_rate_limit(RuntimeError("Error 429: too many requests"))
    assert _is_rate_limit(RuntimeError("rate limit exceeded"))
    assert not _is_rate_limit(RuntimeError("connection reset"))


def test_resolve_model_name_adds_provider_prefix():
    assert resolve_model_name("deepseek-chat", "https://api.deepseek.com") == "deepseek/deepseek-chat"
    assert resolve_model_name("qwen2.5", "https://api.siliconflow.cn/v1") == "openai/qwen2.5"
    assert resolve_model_name("deepseek/deepseek-chat", "https://api.deepseek.com") == "deepseek/deepseek-chat"
    assert resolve_model_name("deepseek-chat", "") == "deepseek-chat"


def test_only_transient_errors_are_retried():
    """回归 B-031 / B-035：瞬时 5xx 要重试，缺依赖/坏 key 不该重试。"""
    assert _is_retryable(TimeoutError("request timed out")) is True
    assert _is_retryable(RuntimeError("Error 429: rate limit")) is True
    assert _is_retryable(ConnectionError("connection reset")) is True
    assert _is_retryable(RuntimeError("InternalServerError: Server disconnected without sending a response")) is True
    assert _is_retryable(RuntimeError("503 service unavailable")) is True
    assert _is_retryable(ImportError("No module named 'mcp'")) is False
    assert _is_retryable(RuntimeError("invalid api key")) is False
