"""Context：S0-S5 顺序、预算丢弃、截断、摘要不丢任务状态。"""

from __future__ import annotations

from _helpers import model_response, new_session, open_store, settings, tool_call
from harness.context import build_context, calibrate, message_tokens, render_tool_result
from harness.models import EventType


def seed_turn(store, sid: str, index: int, chars: int, marker: str) -> None:
    call = tool_call("read", call_id=f"c{index}", path="a.py")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_CALL, call.model_dump())
    output = marker + "x" * chars
    store.append(
        sid,
        EventType.TOOL_RESULT,
        {
            "call_id": call.call_id,
            "ok": True,
            "output_preview": output[:400],
            "tail": output[-400:],
            "artifact_id": f"aid{index}",
            "chars": len(output),
            "duration_ms": 1,
            "truncated": len(output) > 1200,
        },
    )


def test_system_first_user_last_and_feature_brief(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store, "修 parse_date")
    store.append(sid, EventType.TASK_UPDATE, {"feature_id": "parse_date tz", "passes": False, "evidence": ""})
    messages, stats = build_context(store.events(sid), settings(tmp_path))

    assert messages[0]["role"] == "system"
    assert "parse_date tz" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "修 parse_date"}
    assert stats.n_messages == len(messages)
    assert stats.tokens > 0


def test_long_tool_output_rendered_with_artifact_ref(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    call = tool_call("read", call_id="c1", path="a.py")
    store.append(sid, EventType.MODEL_RESPONSE, model_response([call]))
    store.append(sid, EventType.TOOL_RESULT, {"call_id": "c1", "ok": True, "output_preview": "A" * 400,
                                              "tail": "Z" * 400, "artifact_id": "abc123", "chars": 5000,
                                              "duration_ms": 1, "truncated": True})
    messages, _ = build_context(store.events(sid), settings(tmp_path))
    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert "...[truncated 4200 chars, artifact=abc123]..." in tool_msg["content"]
    assert len(tool_msg["content"]) < 1000


def test_short_tool_output_is_verbatim(tmp_path):
    data = {"ok": True, "chars": 12, "output_preview": "short output", "artifact_id": "x"}
    assert render_tool_result(data, settings(tmp_path)) == "short output"
    failed = {"ok": False, "chars": 5, "output_preview": "nope"}
    assert render_tool_result(failed, settings(tmp_path)).startswith("ERROR: ")


def test_budget_drops_oldest_groups_but_keeps_latest(tmp_path):
    cfg = settings(tmp_path)
    cfg.model.window = 3_000
    cfg.run.compact_at = 0.5  # 预算 1500 tokens
    store = open_store(tmp_path)
    sid = new_session(store)
    for index in range(12):
        seed_turn(store, sid, index, 3_000, f"MARK{index}")
    messages, stats = build_context(store.events(sid), cfg)

    assert stats.dropped_groups > 0
    assert messages[0]["role"] == "system"
    joined = "\n".join(str(m.get("content") or "") for m in messages)
    assert "MARK11" in joined
    assert "MARK0" not in joined


def test_compact_summary_replaces_covered_events(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    seed_turn(store, sid, 0, 200, "OLDMARK")
    store.append(
        sid,
        EventType.CONTEXT_COMPACT,
        {
            "covers_from": 1,
            "covers_to": 5,
            "summary": "SUMMARY-MARKER",
            "decisions": ["use sqlite"],
            "open_items": ["add tests"],
            "files_touched": ["a.py"],
            "dead_ends": ["tried regex"],
            "tokens_before": 10_000,
            "tokens_after": 500,
        },
    )
    seed_turn(store, sid, 9, 200, "NEWMARK")
    messages, stats = build_context(store.events(sid), settings(tmp_path))

    assert "SUMMARY-MARKER" in messages[0]["content"]
    assert stats.covered_to == 5
    joined = "\n".join(str(m.get("content") or "") for m in messages)
    assert "OLDMARK" not in joined
    assert "NEWMARK" in joined


def test_build_context_is_deterministic(tmp_path):
    store = open_store(tmp_path)
    sid = new_session(store)
    for index in range(5):
        seed_turn(store, sid, index, 500, f"M{index}")
    events = store.events(sid)
    cfg = settings(tmp_path)
    first, stats_a = build_context(events, cfg)
    second, stats_b = build_context(events, cfg)
    assert first == second
    assert stats_a.tokens == stats_b.tokens


def test_calibration_ratio_is_bounded():
    assert calibrate(3000, 1000) == 3.0
    assert calibrate(3000, 1) == 6.0
    assert calibrate(10, 1000) == 1.5
    assert calibrate(0, 0) == 3.0


def test_token_estimate_counts_tool_call_arguments(tmp_path):
    """回归 B-002：工具调用参数（如 patch 正文）也要进预算。"""
    cfg = settings(tmp_path)
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "patch", "arguments": "x" * 3000}}
        ],
    }
    plain = {"role": "assistant", "content": "", "tool_calls": []}
    # tiktoken 会把长串 x 压缩成很少的 token，所以用相对差而不是绝对阈值
    assert message_tokens(message, cfg) > message_tokens(plain, cfg) + 300
    assert message_tokens({"role": "assistant", "content": ""}, cfg) < 20
