"""长会话压测：大量事件下的写入、查询与上下文构建（不调模型）。"""

from __future__ import annotations

import time

from _helpers import new_session, open_store, settings
from harness.context import build_context
from harness.models import EventType


def _noisy_history(store, sid: str, turns: int = 400) -> None:
    store.append(sid, EventType.MODEL_REQUEST, {"n_messages": 1, "est_tokens": 10})
    for index in range(turns):
        call_id = f"c{index}"
        store.append(sid, EventType.MODEL_RESPONSE, {"text": "", "tool_calls": [], "usage": {}})
        store.append(
            sid,
            EventType.TOOL_CALL,
            {"call_id": call_id, "name": "read", "args": {"path": f"src/m{index % 20}.py"}},
        )
        store.append(
            sid,
            EventType.TOOL_RESULT,
            {
                "call_id": call_id,
                "ok": True,
                "output_preview": "x" * 200,
                "tail": "",
                "artifact_id": "a" * 64,
                "chars": 200,
            },
        )


def test_many_events_append_and_query(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store, "stress")

    started = time.perf_counter()
    _noisy_history(store, sid, turns=700)  # 1 + 700 * 3 = 2101 条事件
    elapsed = time.perf_counter() - started
    assert elapsed < 20, f"append too slow: {elapsed:.1f}s"

    events = store.events(sid)
    assert len(events) == 2103  # 2 条 session 初始化 + 1 + 700 * 3
    seqs = [event.seq for event in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)

    tail = store.events(sid, after_seq=2000)
    assert tail and tail[0].seq > 2000

    session = store.session(sid)
    assert session.tokens_total == 0


def test_context_build_stays_within_budget_on_long_history(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store, "stress")
    _noisy_history(store, sid, turns=300)

    cfg = settings(tmp_path, model__window=4000, model__max_tokens=400, run__turn_keep=8)
    started = time.perf_counter()
    messages, stats = build_context(store.events(sid), cfg)
    elapsed = time.perf_counter() - started

    assert elapsed < 5, f"context build too slow: {elapsed:.1f}s"
    budget = cfg.model.window - cfg.model.max_tokens
    assert stats.tokens <= budget
    assert messages
