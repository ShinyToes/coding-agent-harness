"""事件记账与恢复判定。

record_result 是全量输出进 artifact、事件只存头尾摘要的唯一入口。
"""

from __future__ import annotations

from pathlib import Path

from harness import tasks
from harness.models import EventType, ToolCall, ToolResult, sha256_hex
from harness.tools import TOOLS

PREVIEW_LIMIT = 1_200


def has_result(events: list, call_id: str | None) -> bool:
    return any(e.type == EventType.TOOL_RESULT and e.call_id == call_id for e in events)


def pending_calls(events: list) -> list[ToolCall]:
    done = {e.call_id for e in events if e.type == EventType.TOOL_RESULT}
    out: dict[str, ToolCall] = {}
    for event in events:
        if event.type != EventType.MODEL_RESPONSE:
            continue
        for raw in event.data.get("tool_calls") or []:
            call = ToolCall.model_validate(raw)
            if call.call_id not in done and call.call_id not in out:
                out[call.call_id] = call
    return list(out.values())


def plan_resume(events: list) -> str:
    if not events:
        return "call_model"
    if events[-1].type == EventType.SESSION_END:
        return "done"
    if pending_calls(events):
        return "continue_calls"
    return "call_model"


def steps_used(events: list) -> int:
    return sum(1 for e in events if e.type == EventType.MODEL_RESPONSE)


def record_result(session_id: str, store, call: ToolCall, result: ToolResult) -> ToolResult:
    """工具返回全量进 artifact；事件里只放头尾摘要 + 引用，截断只发生在上下文层。"""
    text = result.output or ""
    chars = len(text)
    big = chars > PREVIEW_LIMIT
    artifact_id = store.put_artifact(session_id, "tool_output", text.encode())
    data: dict = {
        "call_id": call.call_id,
        "ok": result.ok,
        "output_preview": text[:400] if big else text,
        "tail": text[-400:] if big else "",
        "artifact_id": artifact_id,
        "chars": chars,
        "duration_ms": int(result.meta.get("duration_ms") or 0),
        "truncated": big,
        # 工具自定义元数据（如 read 的 cache_hit）挂在 meta 下，按 spec 的事件负载约定
        "meta": {k: v for k, v in result.meta.items() if isinstance(v, (str, int, float, bool))},
    }
    store.append(session_id, EventType.TOOL_RESULT, data)
    return result


def rebuild_read_cache(store, ctx) -> int:
    """从事件流重建 read 缓存：resume 后同一文件仍能命中，不必全量重读。"""
    events = store.events(ctx.session_id)
    names = {e.data.get("call_id"): e.data.get("name") for e in events if e.type == EventType.TOOL_CALL}
    cache: dict = ctx.state.setdefault("read_cache", {})
    restored = 0
    for event in events:
        if event.type != EventType.TOOL_RESULT or names.get(event.data.get("call_id")) != "read":
            continue
        meta = event.data.get("meta") or {}
        path = meta.get("path")
        mtime = meta.get("mtime_ns")
        size = meta.get("size")
        if not path or mtime is None or size is None:
            continue
        key = (str(path), int(mtime), int(size), int(meta.get("start") or 1), int(meta.get("end") or 0))
        cache[key] = {"seq": event.seq, "head": str(meta.get("head") or "")}
        restored += 1
    return restored


def recover_pending(store, cfg, ctx, policy, approver, audit) -> int:
    """continue_calls：纯读可重跑，有副作用的调用状态未知，不重跑。"""
    from harness.loop import execute  # 延迟导入：execute 依赖本模块，避免循环导入

    events = store.events(ctx.session_id)
    completed = {e.call_id for e in events if e.type == EventType.TOOL_RESULT}
    recorded = {e.data.get("call_id"): e.data for e in events if e.type == EventType.TOOL_CALL}
    recovered = 0
    for call in pending_calls(events):
        if call.call_id in completed:
            continue
        tool = TOOLS.get(call.name)
        meta = recorded.get(call.call_id, {})
        if tool is not None and tool.effect == "read":
            execute(call, ctx, policy, approver, audit, record_call=False)
        elif _already_applied(meta):
            record_result(
                ctx.session_id,
                store,
                call,
                ToolResult(ok=True, output=f"{call.name} was already applied before the crash (content hash matches)"),
            )
        elif _untouched(meta):
            execute(call, ctx, policy, approver, audit, record_call=False)
        else:
            store.append(
                ctx.session_id,
                EventType.ERROR,
                {
                    "where": "resume",
                    "message": f"interrupted {call.name} call {call.call_id}: state unknown, not re-executed",
                    "stack_hash": "",
                },
            )
            record_result(
                ctx.session_id,
                store,
                call,
                ToolResult(
                    ok=False,
                    output=(
                        f"interrupted before tool.result was recorded. {call.name} was NOT re-executed "
                        "(state unknown). Inspect the workspace, then retry explicitly."
                    ),
                ),
            )
        recovered += 1
    return recovered


def _file_sha(path: str | None) -> str:
    if not path:
        return ""
    target = Path(path)
    return sha256_hex(target.read_bytes()) if target.is_file() else ""


def _already_applied(meta: dict) -> bool:
    after = str(meta.get("sha_after") or "")
    return bool(after) and _file_sha(meta.get("target_path")) == after


def _untouched(meta: dict) -> bool:
    """目标文件仍是改前状态（或本来就不存在）→ 重跑是安全的。"""
    after = str(meta.get("sha_after") or "")
    if not after or not meta.get("target_path"):
        return False
    return _file_sha(meta.get("target_path")) == str(meta.get("sha_before") or "")


def check_drift(store, session_id: str, cfg) -> None:
    session = store.session(session_id)
    if not session.last_commit:
        return
    head = tasks.head_or_empty(cfg.workspace)
    if not head or head == session.last_commit:
        return
    store.append(
        session_id,
        EventType.ERROR,
        {"where": "resume", "message": f"HEAD moved {session.last_commit}->{head}", "stack_hash": ""},
    )
    # 单独存：loop 每轮会用 progress_tail 覆盖 task_brief，漂移提示不能跟着被冲掉。
    cfg.drift_notice = f"工作区自上次会话后被改动（{session.last_commit[:8]}->{head[:8]}），先 git diff 确认。"
    store.set_commit(session_id, head)
