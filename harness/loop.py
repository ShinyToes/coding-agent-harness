"""主循环。

顺序不可颠倒：tool.call 先落库 → policy.decision → (审批) → 执行 → tool.result。
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from pathlib import Path

from harness import tasks, tokens
from harness.audit import make_audit
from harness.compact import compact
from harness.context import build_context, calibrate
from harness.journal import (
    check_drift,
    plan_resume,
    rebuild_read_cache,
    record_result,
    recover_pending,
    steps_used,
)
from harness.llm import LLM, ContextOverflow
from harness.log import LOGGER
from harness.models import (
    EventType,
    Ruling,
    ToolCall,
    ToolResult,
    canonical_json,
    idem_key,
    sha256_hex,
)
from harness.policy import Policy, make_approver
from harness.sandbox import make_sandbox
from harness.tools import ToolCtx, get_tool, tool_schemas, tools_hash
from harness.tools.fs import preview as preview_diff
from harness.tools.fs import resolve_path
from harness.tools.patch import apply_patch
from harness.tools.search import build_repo_map


def _nudges(events: list) -> int:
    return sum(
        1
        for e in events
        if e.type == EventType.USER_MESSAGE and e.data.get("nudge") and e.data.get("kind") != "budget"
    )


def _budget_nudged(events: list) -> bool:
    return any(e.type == EventType.USER_MESSAGE and e.data.get("kind") == "budget" for e in events)


def _brief(progress: str, drift_notice: str) -> str:
    """S1 = 进度末尾 + 漂移警告；两者来源不同，不能互相覆盖。"""
    if not drift_notice:
        return progress
    notice = f"## 漂移警告\n{drift_notice}"
    return f"{progress}\n\n{notice}" if progress else notice


def end(store, session_id: str, status: str, reason: str = "") -> str:
    events = store.events(session_id)
    session = store.session(session_id)
    store.append(
        session_id,
        EventType.SESSION_END,
        {"status": status, "reason": reason, "steps": steps_used(events), "tokens_total": session.tokens_total},
    )
    store.set_status(session_id, status)
    return status


def _safe_preview(call: ToolCall, ctx: ToolCtx) -> str:
    try:
        return preview_diff(call, ctx)[:2_000]
    except (OSError, ValueError, RuntimeError) as exc:
        return f"(diff preview unavailable: {type(exc).__name__}: {exc})"


def _run_tool(call: ToolCall, ctx: ToolCtx) -> ToolResult:
    try:
        return get_tool(call.name).run(call.args, ctx)
    except KeyError as exc:
        return ToolResult(ok=False, output=f"unknown tool: {exc}")
    except Exception as exc:  # noqa: BLE001 - 工具隔离：崩溃要变成失败结果，不能炸主循环
        return ToolResult(ok=False, output=f"tool crashed: {type(exc).__name__}: {exc}")


def _approve_and_run(call: ToolCall, ctx: ToolCtx, ruling: Ruling, policy: Policy, approver) -> ToolResult:
    store, sid = ctx.store, ctx.session_id
    store.append(
        sid,
        EventType.APPROVAL_REQUEST,
        {
            "call_id": call.call_id,
            "summary": f"{call.name} {canonical_json(call.args)[:280]}",
            "diff_preview": _safe_preview(call, ctx),
        },
    )
    answer = approver(call, ruling)
    store.append(sid, EventType.APPROVAL_RESPONSE, {"call_id": call.call_id, "actor": "user", "decision": answer})
    if answer == "always":
        policy.remember(call)
        return _run_tool(call, ctx)
    if answer == "allow":
        return _run_tool(call, ctx)
    return ToolResult(ok=False, output="denied by user")


def target_hashes(call: ToolCall, ctx: ToolCtx) -> dict[str, str]:
    """写类调用在执行前算好「改前/改后」内容 hash，崩溃恢复时据此判定是否已执行。"""
    if call.name not in {"write", "patch"}:
        return {}
    raw = str(call.args.get("path") or "")
    if not raw:
        return {}
    path = resolve_path(ctx, raw)
    before = sha256_hex(path.read_bytes()) if path.is_file() else ""
    if call.name == "write":
        after = sha256_hex(str(call.args.get("content", "")).encode())
    else:
        source = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        outcome = apply_patch(source, str(call.args.get("patch", "")))
        after = sha256_hex(outcome.text.encode()) if outcome.ok else ""
    return {"target_path": str(path), "sha_before": before, "sha_after": after}


def _verification_hint(store, session_id: str) -> str | None:
    """最后一次编辑之后还没有成功 bash 时返回提示；用于把「验证已过」放进工具结果。"""
    events = store.events(session_id)
    names = {e.data.get("call_id"): e.data.get("name") for e in events if e.type == EventType.TOOL_CALL}
    edits = [
        e.seq
        for e in events
        if e.type == EventType.TOOL_RESULT
        and e.data.get("ok")
        and names.get(e.data.get("call_id")) in {"write", "patch"}
    ]
    if not edits:
        return None
    verified = any(
        e.seq > max(edits)
        and e.type == EventType.TOOL_RESULT
        and e.data.get("ok")
        and names.get(e.data.get("call_id")) == "bash"
        for e in events
    )
    if verified:
        return None
    return (
        "[harness] 这次验证是在你最后一次编辑之后跑通的。"
        "如果任务已完成，请现在调用 finish；如果还需要改代码，继续即可。"
    )


def execute(
    call: ToolCall,
    ctx: ToolCtx,
    policy: Policy,
    approver,
    audit,
    *,
    record_call: bool = True,
) -> ToolResult:
    store, sid = ctx.store, ctx.session_id
    if record_call:
        last = store.last(sid)
        seq_hint = (last.seq if last else 0) + 1
        call = call.model_copy(update={"idem_key": idem_key(sid, seq_hint, call.name, call.args)})
        data = call.model_dump()
        data.update(target_hashes(call, ctx))
        store.append(sid, EventType.TOOL_CALL, data)

    started = time.perf_counter()
    # span 覆盖 policy 决策与执行：被拒绝的调用也要有 span，否则审计对不上 tool.call 数。
    with audit.tool(call.name, sha256_hex(canonical_json(call.args))[:12]) as span:
        ruling: Ruling = policy.decide(call, ctx)
        span.set_attribute("harness.policy.decision", ruling.decision)
        store.append(sid, EventType.POLICY_DECISION, {"call_id": call.call_id, **ruling.model_dump()})

        if ruling.decision == "deny":
            result = ToolResult(ok=False, output=f"denied by {ruling.rule}: {ruling.reason}")
        elif ruling.decision == "ask":
            result = _approve_and_run(call, ctx, ruling, policy, approver)
        else:
            result = _run_tool(call, ctx)

        # 验证刚通过时把收尾提示塞进工具结果本身（模型一定会读到这条消息）
        if call.name == "bash" and result.ok:
            hint = _verification_hint(store, sid)
            if hint:
                result = result.model_copy(update={"output": f"{result.output}\n\n{hint}"})

        span.set_attribute("harness.duration.ms", int((time.perf_counter() - started) * 1000))
        span.set_attribute("harness.ok", result.ok)
    return record_result(sid, store, call, result)


def _emit(on_event, store, session_id: str, since: int) -> int:
    """把 since 之后的新事件推给调用方（CLI 实时进度），返回已推送到的 seq。"""
    if on_event is None:
        return since
    for event in store.events(session_id, after_seq=since):
        on_event(event)
        since = event.seq
    return since


def run(
    session_id: str,
    store,
    cfg,
    *,
    llm=None,
    sandbox=None,
    approver=None,
    audit=None,
    on_event=None,
) -> str:
    events = store.events(session_id)
    if plan_resume(events) == "done":
        return "done"

    llm = llm or LLM(cfg, store, session_id)
    audit = audit or make_audit(cfg)
    policy = Policy(cfg, events)
    approve = approver if approver is not None else make_approver(cfg)
    sandbox = sandbox or make_sandbox(cfg, store, session_id)
    ctx = ToolCtx(
        session_id=session_id,
        workspace=Path(cfg.workspace),
        sandbox=sandbox,
        store=store,
        cfg=cfg,
        llm=llm,
        audit=audit,
    )
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    if not store.acquire_lock(session_id, owner):
        raise RuntimeError(f"session {session_id} is locked by {store.lock_holder(session_id)}")
    status = "running"
    overflow_retried = False
    seen = 0
    turns_since_compact = cfg.run.compact_cooldown
    try:
        sandbox.start()
        check_drift(store, session_id, cfg)
        recover_pending(store, cfg, ctx, policy, approve, audit)
        rebuild_read_cache(store, ctx)
        seen = _emit(on_event, store, session_id, seen)
        if not cfg.repo_map:
            cfg.repo_map = build_repo_map(Path(cfg.workspace), 800)
        while True:
            events = store.events(session_id)
            steps = steps_used(events)
            if steps >= cfg.run.max_steps:
                status = end(store, session_id, "max_steps", "step budget exhausted")
                break
            # 预算提醒：模型经常「活干完了但不 finish」，快用完步数时明确催一次
            if not _budget_nudged(events) and steps >= cfg.run.max_steps * cfg.run.nudge_at:
                store.append(
                    session_id,
                    EventType.USER_MESSAGE,
                    {
                        "text": (
                            f"已用 {steps}/{cfg.run.max_steps} 步，接近预算上限。"
                            "若任务已完成请立即调用 finish；否则优先完成剩余工作并收尾。"
                        ),
                        "nudge": True,
                        "kind": "budget",
                    },
                )
                events = store.events(session_id)
            cfg.task_brief = _brief(tasks.progress_tail(cfg.workspace), cfg.drift_notice)
            messages, stats = build_context(events, cfg)
            tools = tool_schemas()
            # S1/S2 来自文件系统，只有落进事件，replay 才能重建模型真正看过的请求（B-004）
            snapshot_ref = store.put_artifact(
                session_id,
                "context_snapshot",
                canonical_json(
                    {
                        "task_brief": cfg.task_brief,
                        "repo_map": cfg.repo_map,
                        "calibration": cfg.calibration,
                    }
                ).encode(),
            )
            request_ref = store.put_artifact(
                session_id, "request", canonical_json({"messages": messages, "tools": tools}).encode()
            )
            store.append(
                session_id,
                EventType.MODEL_REQUEST,
                {
                    "artifact_id": request_ref,
                    "n_messages": len(messages),
                    "est_tokens": stats.tokens,
                    "tools_hash": tools_hash(),
                    "snapshot_artifact": snapshot_ref,
                },
            )
            turn_index = steps_used(store.events(session_id))
            # turn span 覆盖模型调用 + 工具执行：工具 span 才能成为它的子节点（B-036）
            with audit.turn(session_id, turn_index, cfg.model.name) as span:
                try:
                    completion = llm.complete(messages, tools)
                except ContextOverflow as exc:
                    # 超限重试没有意义：强制压缩一次再重来；仍失败就如实结束（B-009）
                    span.set_attribute("harness.error", "context_overflow")
                    store.append(
                        session_id,
                        EventType.ERROR,
                        {"where": "model", "message": f"context overflow: {exc}", "stack_hash": ""},
                    )
                    if overflow_retried:
                        status = end(store, session_id, "context_overflow", str(exc)[:200])
                        break
                    overflow_retried = True
                    try:
                        compacted = compact(store, session_id, cfg, llm, audit, force=True)
                    except ContextOverflow:
                        compacted = None  # 连摘要请求都超限：只能如实结束
                    if compacted is None:
                        status = end(store, session_id, "context_overflow", "no history left to compact")
                        break
                    continue

                span.set_attribute("gen_ai.usage.input_tokens", completion.prompt_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", completion.completion_tokens)
                overflow_retried = False
                store.append(session_id, EventType.MODEL_RESPONSE, completion.model_dump())
                store.add_usage(session_id, completion.prompt_tokens + completion.completion_tokens, completion.cost)
                seen = _emit(on_event, store, session_id, seen)

                # 成本上限：本步已经花掉的钱超过预算就不再继续（允许最后一次调用略微超支）
                spent = store.session(session_id).cost
                if cfg.run.max_cost and spent >= cfg.run.max_cost:
                    status = end(store, session_id, "max_cost", f"cost budget exhausted: ${spent:.4f}")
                    break

                chars = sum(len(str(m.get("content") or "")) for m in messages)
                # tiktoken 精确计数时不需要 chars/ratio 校准；只有启发式后端才校准
                if tokens.backend() == "tiktoken":
                    if cfg.calibration != 1.0:
                        cfg.calibration = 1.0
                        store.set_calibration(session_id, 1.0)
                else:
                    ratio = calibrate(chars, completion.prompt_tokens)
                    if abs(ratio - cfg.calibration) > 0.01:
                        cfg.calibration = ratio
                        store.set_calibration(session_id, ratio)

                if not completion.tool_calls:
                    if _nudges(store.events(session_id)) < cfg.run.max_nudges:
                        store.append(
                            session_id,
                            EventType.USER_MESSAGE,
                            {"text": "继续。若已完成请调用 finish。", "nudge": True},
                        )
                        continue
                    status = end(store, session_id, "no_tool_calls", "model stopped calling tools")
                    break

                for call in completion.tool_calls:
                    execute(call, ctx, policy, approve, audit)
                seen = _emit(on_event, store, session_id, seen)

            finished = ctx.state.get("finished")
            if finished:
                status = end(store, session_id, str(finished["status"]), str(finished["summary"])[:500])
                break
            if (
                cfg.run.compact_enabled
                and turns_since_compact >= cfg.run.compact_cooldown
                and stats.tokens > cfg.model.window * cfg.run.compact_at
            ):
                compacted = compact(store, session_id, cfg, llm, audit)
                seen = _emit(on_event, store, session_id, seen)
                if compacted is not None:
                    turns_since_compact = 0
            turns_since_compact += 1
    except KeyboardInterrupt:
        # Ctrl+C：把会话标记为 cancelled 再退出，保证事件流完整、锁能释放
        status = end(store, session_id, "cancelled", "interrupted by user")
    except Exception as exc:
        LOGGER.exception("loop failed: %s", exc)
        store.append(
            session_id,
            EventType.ERROR,
            {"where": "loop", "message": f"{type(exc).__name__}: {exc}", "stack_hash": sha256_hex(str(exc))[:12]},
        )
        store.set_status(session_id, "error")
        raise
    finally:
        sandbox.stop()
        store.release_lock(session_id, owner)
        _emit(on_event, store, session_id, seen)
    return status
