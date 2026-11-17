"""审计：3 个 turn 三个 span，工具 span 数与 tool.call 事件数相等（验收第 5 条）。"""

from __future__ import annotations

from _helpers import FakeSandbox, ScriptedLLM, completion, new_session, open_store, settings, tool_call
from harness.audit import Audit, parse_headers
from harness.loop import run
from harness.models import EventType
from harness.policy import deny_approver


def test_parse_headers_for_otlp_auth():
    headers = parse_headers("Authorization=Basic dXNlcjpwYXNz,X-Env=ci")
    assert headers == {"Authorization": "Basic dXNlcjpwYXNz", "X-Env": "ci"}
    assert parse_headers("") == {}
    assert parse_headers(None) == {}
    assert parse_headers("broken") == {}


def test_three_turns_and_matching_tool_spans(tmp_path):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    audit = Audit(exporter=exporter)

    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store, "三个 turn")
    (tmp_path / "a.py").write_text("x = 1\n")

    llm = ScriptedLLM(
        [
            completion("", [tool_call("read", call_id="r1", path="a.py")]),
            completion("", [tool_call("read", call_id="r2", path="a.py")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ]
    )
    status = run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=audit)
    assert status == "done"
    audit.provider.force_flush()

    spans = exporter.get_finished_spans()
    turns = [s for s in spans if s.name == "harness.turn"]
    tools = [s for s in spans if s.name == "harness.tool"]
    calls = [e for e in store.events(sid) if e.type == EventType.TOOL_CALL]

    assert len(turns) == 3
    assert len(tools) == len(calls) == 3
    assert turns[0].attributes["gen_ai.request.model"] == cfg.model.name
    assert turns[0].attributes["gen_ai.usage.input_tokens"] == 100
    assert turns[0].attributes["gen_ai.usage.output_tokens"] == 10
    assert turns[0].attributes["harness.turn.index"] == 0
    assert tools[0].attributes["harness.tool.name"] == "read"
    assert tools[0].attributes["harness.policy.decision"] == "allow"
    assert tools[0].attributes["harness.ok"] is True


def test_tool_spans_are_children_of_the_turn_span(tmp_path):
    """B-036：spec 要求工具调用是 turn 的子 span，不能是平级。"""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    audit = Audit(exporter=exporter)
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

    spans = exporter.get_finished_spans()
    turns = [s for s in spans if s.name == "harness.turn"]
    tools = [s for s in spans if s.name == "harness.tool"]
    assert len(turns) == 2 and len(tools) == 2
    turn_by_id = {s.context.span_id: s for s in turns}
    for tool in tools:
        assert tool.parent is not None, "tool span has no parent"
        assert tool.parent.span_id in turn_by_id
    read_span = next(t for t in tools if t.attributes["harness.tool.name"] == "read")
    assert read_span.parent.span_id == turns[0].context.span_id


def test_denied_tool_still_produces_span(tmp_path):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    audit = Audit(exporter=exporter)
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    llm = ScriptedLLM(
        [
            completion("", [tool_call("bash", call_id="b1", command="rm -rf /")]),
            completion("", [tool_call("finish", call_id="f1", summary="done")]),
        ]
    )
    run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=audit)
    audit.provider.force_flush()
    spans = [s for s in exporter.get_finished_spans() if s.name == "harness.tool"]
    bash_span = next(s for s in spans if s.attributes["harness.tool.name"] == "bash")
    assert len(spans) == 2  # bash + finish 都是 tool.call
    assert bash_span.attributes["harness.policy.decision"] == "deny"
    assert bash_span.attributes["harness.ok"] is False
