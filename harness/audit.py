"""OTel 审计：一个 turn 一个 span，工具/压缩为 child span。

属性沿用 OTel GenAI 约定（gen_ai.*），自定义字段统一 harness.*。
OTel 缺失时降级为 NoopAudit —— 审计不能成为主循环的硬依赖。
"""

from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext

try:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False


class _NoopSpan:
    def set_attribute(self, key: str, value) -> None:
        return None

    def set_attributes(self, attrs: dict) -> None:
        return None


class NoopAudit:
    def span(self, name: str, attrs: dict | None = None):
        return nullcontext(_NoopSpan())

    def turn(self, session_id: str, index: int, model_name: str):
        return nullcontext(_NoopSpan())

    def tool(self, name: str, args_hash: str, decision: str = ""):
        return nullcontext(_NoopSpan())

    def compact(self, before: int, after: int, covers: str):
        return nullcontext(_NoopSpan())


def parse_headers(raw: str | None) -> dict[str, str]:
    """`HARNESS_OTLP_HEADERS="Authorization=Basic xxx,k2=v2"` → dict。"""
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for item in raw.split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            headers[key.strip()] = value.strip()
    return headers


class Audit:
    def __init__(
        self,
        service_name: str = "harness",
        exporter=None,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        resource = Resource.create({"service.name": service_name})
        self.provider = TracerProvider(resource=resource)
        selected = exporter
        if selected is None and endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            selected = OTLPSpanExporter(endpoint=endpoint, headers=headers or None)
        if selected is not None:
            self.provider.add_span_processor(SimpleSpanProcessor(selected))
        self.tracer = self.provider.get_tracer("harness")

    @contextmanager
    def span(self, name: str, attrs: dict | None = None):
        with self.tracer.start_as_current_span(name) as span:
            for key, value in (attrs or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            yield span

    def turn(self, session_id: str, index: int, model_name: str):
        return self.span(
            "harness.turn",
            {
                "harness.session.id": session_id,
                # 标准约定：Phoenix / Langfuse 用它把多个 trace 归到同一个 session
                "session.id": session_id,
                "harness.turn.index": index,
                "gen_ai.request.model": model_name,
            },
        )

    def tool(self, name: str, args_hash: str, decision: str = ""):
        return self.span(
            "harness.tool",
            {"harness.tool.name": name, "harness.args.hash": args_hash, "harness.policy.decision": decision},
        )

    def compact(self, before: int, after: int, covers: str):
        return self.span(
            "harness.compact",
            {"harness.tokens.before": before, "harness.tokens.after": after, "harness.compact.covers": covers},
        )

    def shutdown(self) -> None:
        self.provider.shutdown()


def make_audit(cfg) -> Audit | NoopAudit:
    if not OTEL_AVAILABLE:
        return NoopAudit()
    endpoint = os.environ.get("HARNESS_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    headers = parse_headers(os.environ.get("HARNESS_OTLP_HEADERS"))
    return Audit(endpoint=endpoint, headers=headers)
