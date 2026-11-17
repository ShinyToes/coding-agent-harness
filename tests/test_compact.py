"""压缩：触发后任务状态不丢（验收第 3 条）。"""

from __future__ import annotations

import json

from _helpers import model_response, new_session, open_store, settings, tool_call
from harness.compact import compact, parse_summary, plan_compaction, transcript
from harness.context import build_context
from harness.models import Completion, EventType
from harness.store import Store

SUMMARY = {
    "summary": "SUMMARY: 改了解析器，剩测试没写",
    "decisions": ["用 sqlite 存事件"],
    "open_items": ["补 test_parse"],
    "files_touched": ["src/date.py"],
    "dead_ends": ["试过正则解析时间戳"],
}


class SummaryLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        return Completion(
            text=json.dumps(SUMMARY),
            usage={"prompt_tokens": 900, "completion_tokens": 60},
            model="fake",
        )


def seed_big_session(tmp_path, turns: int = 500):
    cfg = settings(tmp_path)
    cfg.run.turn_keep = 2
    store = open_store(tmp_path)
    sid = new_session(store, "长任务")
    features = [f"feature-{i:02d}" for i in range(6)]
    for feature in features:
        store.append(sid, EventType.TASK_UPDATE, {"feature_id": feature, "passes": False, "evidence": ""})
    for turn in range(turns):
        call = tool_call("read", call_id=f"c{turn}", path=f"f{turn}.py")
        store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
        store.append(sid, EventType.TOOL_CALL, call.model_dump())
        output = f"MARK{turn:03d}" + "x" * 1_194  # 每条 1200 字符：不触发截断，全部进消息
        store.append(
            sid,
            EventType.TOOL_RESULT,
            {
                "call_id": call.call_id,
                "ok": True,
                "output_preview": output,
                "tail": "",
                "artifact_id": f"aid{turn}",
                "chars": len(output),
                "duration_ms": 1,
                "truncated": False,
            },
        )
    return store, sid, cfg, features


def test_compact_keeps_feature_list_after_resume(tmp_path):
    store, sid, cfg, features = seed_big_session(tmp_path)
    events = store.events(sid)
    stream_chars = sum(int(e.data.get("chars") or 0) for e in events if e.type == EventType.TOOL_RESULT)
    assert stream_chars >= 600_000  # 20 万 token 量级的假事件流

    before = build_context(events, cfg)[1]
    assert before.tokens > 50_000

    llm = SummaryLLM()
    event = compact(store, sid, cfg, llm)
    assert event is not None
    assert llm.calls == 1
    assert event.data["covers_to"] > event.data["covers_from"]
    assert event.data["tokens_after"] < event.data["tokens_before"]
    assert event.data["summary"].startswith("SUMMARY")

    compacts = [e for e in store.events(sid) if e.type == EventType.MODEL_REQUEST and e.data.get("kind") == "compact"]
    assert len(compacts) == 1
    assert compacts[0].data["artifact_id"] == event.data["request_artifact"]

    # 模拟重启：新连接、新配置对象
    reopened = Store(store.path)
    cfg2 = settings(tmp_path)
    cfg2.run.turn_keep = 2
    messages, stats = build_context(reopened.events(sid), cfg2)
    joined = "\n".join(str(m.get("content") or "") for m in messages)

    for feature in features:
        assert feature in joined
    assert "SUMMARY" in joined
    assert "MARK000" not in joined  # 被覆盖的历史不再进消息
    assert "MARK499" in joined  # 最近 turn 保留原文
    assert stats.covered_to == event.data["covers_to"]


def test_compact_noop_without_candidates(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    assert compact(store, sid, settings(tmp_path), SummaryLLM()) is None


def test_plan_compaction_starts_at_first_turn_and_keeps_recent(tmp_path):
    store, sid, cfg, _ = seed_big_session(tmp_path, turns=6)
    events = store.events(sid)
    covered = plan_compaction(events, cfg)
    assert covered
    first_candidate = next(e for e in events if e.type not in {
        EventType.SESSION_START, EventType.TASK_UPDATE, EventType.CONTEXT_COMPACT, EventType.MODEL_REQUEST,
    })
    assert covered[0].seq == first_candidate.seq
    assert len(covered) < len(events)


def test_parse_summary_handles_fences_and_prose():
    assert parse_summary(json.dumps(SUMMARY))["summary"].startswith("SUMMARY")
    fenced = "```json\n" + json.dumps(SUMMARY) + "\n```"
    assert parse_summary(fenced)["open_items"] == ["补 test_parse"]
    assert parse_summary("not json at all") is None


def test_transcript_includes_policy_and_errors():
    from harness.models import Event, EventType, utcnow

    events = [
        Event(session_id="s", seq=1, type=EventType.POLICY_DECISION,
              ts=utcnow(), data={"decision": "deny", "rule": "cmd.destructive", "reason": "no"}),
        Event(session_id="s", seq=2, type=EventType.ERROR,
              ts=utcnow(), data={"where": "loop", "message": "boom"}),
    ]
    text = transcript(events)
    assert "deny" in text and "cmd.destructive" in text
    assert "loop: boom" in text
