"""B-026 的可离线部分：OTLP/HTTP 导出到本地 receiver，用 proto 解码验证 span 树。

Langfuse 只是把这个 endpoint 换成它的地址；协议与 span 结构在这里已经验证过。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _helpers import FakeSandbox, ScriptedLLM, completion, new_session, open_store, settings, tool_call
from harness.audit import Audit
from harness.loop import run
from harness.policy import deny_approver


class _Receiver:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server 约定
                length = int(self.headers.get("Content-Length", "0"))
                receiver.bodies.append(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()
                self.wfile.write(b"")

            def log_message(self, *args) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Receiver:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def endpoint(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"


def test_otlp_export_contains_turn_and_tool_spans(tmp_path):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    with _Receiver() as receiver:
        audit = Audit(endpoint=receiver.endpoint)
        cfg = settings(tmp_path)
        store = open_store(tmp_path)
        sid = new_session(store)
        (tmp_path / "a.py").write_text("x = 1\n")
        llm = ScriptedLLM(
            [
                completion("", [tool_call("read", call_id="r1", path="a.py")]),
                completion("", [tool_call("finish", call_id="f1", summary="done")]),
            ]
        )
        run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=audit)
        audit.provider.force_flush()
        audit.shutdown()

    assert receiver.bodies, "OTLP exporter did not POST anything"

    names: list[str] = []
    parents: dict[str, str] = {}
    for body in receiver.bodies:
        request = ExportTraceServiceRequest()
        request.ParseFromString(body)
        for resource_spans in request.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    names.append(span.name)
                    if span.parent_span_id:
                        parents[span.name] = span.parent_span_id.hex()

    assert "harness.turn" in names
    assert "harness.tool" in names
    # 两个 turn、每个 turn 一个工具：工具 span 必须带 parent_span_id
    assert sum(1 for name in names if name == "harness.turn") == 2
    assert parents.get("harness.tool")
