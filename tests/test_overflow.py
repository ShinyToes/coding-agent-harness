"""B-009：context_length 超限时强制压缩并重试一次，而不是直接 session error。"""

from __future__ import annotations

import json

from _helpers import FakeSandbox, new_session, open_store, settings, tool_call
from harness.audit import NoopAudit
from harness.llm import ContextOverflow
from harness.loop import run
from harness.models import Completion, EventType
from harness.policy import deny_approver

SUMMARY_JSON = {
    "summary": "压缩后的摘要",
    "decisions": [],
    "open_items": [],
    "files_touched": [],
    "dead_ends": [],
}


class OverflowLLM:
    """前 ``overflows`` 次普通调用抛超限；压缩请求永远返回合法摘要。"""

    def __init__(self, overflows: int = 1) -> None:
        self.overflows = overflows
        self.calls: list[list[dict]] = []

    def complete(self, messages, tools):
        self.calls.append(messages)
        if messages and str(messages[0].get("content", "")).startswith("You compress"):
            return Completion(text=json.dumps(SUMMARY_JSON), usage={"prompt_tokens": 50, "completion_tokens": 20}, model="fake")
        if self.overflows > 0:
            self.overflows -= 1
            raise ContextOverflow("This model's maximum context length is 128000 tokens")
        return Completion(
            text="",
            tool_calls=[tool_call("finish", call_id="f1", summary="done")],
            usage={"prompt_tokens": 100, "completion_tokens": 10},
            model="fake",
        )


def seed_one_turn(store, sid, tmp_path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    call = tool_call("read", call_id="r0", path="a.py")
    store.append(
        sid,
        EventType.MODEL_RESPONSE,
        {"text": "", "tool_calls": [call.model_dump()], "usage": {"prompt_tokens": 100, "completion_tokens": 10},
         "model": "fake", "latency_ms": 1, "raw_ref": ""},
    )
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    store.append(
        sid,
        EventType.TOOL_RESULT,
        {"call_id": call.call_id, "ok": True, "output_preview": "x = 1", "tail": "", "artifact_id": "a",
         "chars": 5, "duration_ms": 1, "truncated": False},
    )


def test_overflow_forces_compaction_and_retries(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    seed_one_turn(store, sid, tmp_path)
    llm = OverflowLLM(overflows=1)

    status = run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=NoopAudit())

    assert status == "done"
    types = [e.type for e in store.events(sid)]
    assert EventType.CONTEXT_COMPACT in types
    errors = [e for e in store.events(sid) if e.type == EventType.ERROR and e.data.get("where") == "model"]
    assert len(errors) == 1


def test_second_overflow_ends_session_cleanly(tmp_path):
    cfg = settings(tmp_path)
    store = open_store(tmp_path)
    sid = new_session(store)
    seed_one_turn(store, sid, tmp_path)
    llm = OverflowLLM(overflows=99)

    status = run(sid, store, cfg, llm=llm, sandbox=FakeSandbox(), approver=deny_approver, audit=NoopAudit())

    assert status == "context_overflow"
    assert store.session(sid).status == "context_overflow"
    assert store.events(sid)[-1].data["status"] == "context_overflow"
