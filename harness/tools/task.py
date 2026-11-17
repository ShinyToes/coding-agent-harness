"""task 工具：finish（提交结论）、explorer（只读子 agent）、reviewer（只读审查）。"""

from __future__ import annotations

import json

from harness import tasks
from harness.models import EventType, ToolResult, canonical_json
from harness.tools.registry import ToolCtx, get_tool, register, tool_schemas

READ_ONLY_TOOLS = {"read", "grep", "glob", "list", "repo_map", "read_artifact"}
WRITE_TOOLS = {"write", "patch"}


def verify_reason(ctx: ToolCtx) -> str | None:
    """finish 的证据门槛：返回拒绝理由；None 表示可以收工。

    规则：没有改动 → 放行；有改动 → 最后一次改动之后必须有一次成功的 bash；
    或者 feature list 全部 passes（等价于“每项都有证据”）。
    """
    features = tasks.load_features(ctx.workspace)
    if features and all(feature.get("passes") for feature in features):
        return None

    events = ctx.store.events(ctx.session_id)
    names = {e.data.get("call_id"): e.data.get("name") for e in events if e.type == EventType.TOOL_CALL}
    results = {e.data.get("call_id"): e for e in events if e.type == EventType.TOOL_RESULT}
    edits = [
        result.seq
        for cid, result in results.items()
        if result.data.get("ok") and names.get(cid) in WRITE_TOOLS
    ]
    if not edits:
        return None
    last_edit = max(edits)
    verified = any(
        result.seq > last_edit and result.data.get("ok") and names.get(result.data.get("call_id")) == "bash"
        for result in results.values()
    )
    if verified:
        return None
    return "最后一次编辑之后没有成功的 bash 验证（例如跑测试或检查脚本）"


class FinishTool:
    name = "finish"
    effect = "read"
    description = "任务完成时调用，提交结论并终止循环。改过文件时需先有成功的 bash 验证。"
    parameters = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "做了什么、验证了什么"},
            "status": {"type": "string", "enum": ["done", "blocked"], "default": "done"},
        },
        "required": ["summary"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        summary = str(args.get("summary", "")).strip() or "(no summary)"
        status = str(args.get("status") or "done")
        if ctx.cfg.run.require_verification:
            reason = verify_reason(ctx)
            if reason:
                # 不设 finished：让模型继续干活，而不是直接失败退出
                return ToolResult(
                    ok=False,
                    output=f"finish rejected: {reason}。先跑一次 verifiable 的 bash 再 finish，"
                    "或把 feature list 全部标记为通过。",
                    meta={"rejected": True},
                )
        ctx.state["finished"] = {"summary": summary, "status": status}
        return ToolResult(ok=True, output=summary, meta={"status": status})


class ExplorerTool:
    name = "explorer"
    effect = "read"
    description = "只读子 agent：就代码库回答一个具体问题，不产生任何副作用。"
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["question"],
    }

    MAX_TURNS = 3
    MAX_TOOL_CALLS = 6

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        if ctx.llm is None:
            return ToolResult(ok=False, output="explorer unavailable: no LLM bound to this session")
        if ctx.state.get("explorer_depth", 0) > 0:
            return ToolResult(ok=False, output="explorer cannot nest")
        ctx.state["explorer_depth"] = ctx.state.get("explorer_depth", 0) + 1
        try:
            return self._explore(args, ctx)
        finally:
            ctx.state["explorer_depth"] = 0

    def _explore(self, args: dict, ctx: ToolCtx) -> ToolResult:
        question = str(args.get("question", "")).strip()
        hints = [str(p) for p in (args.get("paths") or [])]
        system = (
            "You are a read-only explorer. Inspect the repository with read/grep/glob/list "
            "and answer the question with concrete file:line evidence. Never modify files."
        )
        user = question + (f"\nStart from: {', '.join(hints)}" if hints else "")
        text, meta = readonly_loop(ctx, system, user, self.MAX_TURNS, self.MAX_TOOL_CALLS)
        return ToolResult(ok=True, output=text, meta=meta)


def readonly_loop(
    ctx: ToolCtx,
    system: str,
    user: str,
    max_turns: int = 3,
    max_calls: int = 6,
) -> tuple[str, dict]:
    """只读子 agent 的共用循环：explorer 与 reviewer 都走这里，不产生副作用。"""
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    schemas = tool_schemas(READ_ONLY_TOOLS)
    text = ""
    tool_calls = 0
    for turn in range(max_turns):
        comp = ctx.llm.complete(messages, schemas)
        text = comp.text or ""
        if not comp.tool_calls:
            return text or "(no answer)", {"turns": turn + 1}
        if tool_calls + len(comp.tool_calls) > max_calls:
            return (text or "") + "\n(budget exhausted)", {"turns": turn + 1, "budget_exceeded": True}
        tool_calls += len(comp.tool_calls)
        messages.append(
            {
                "role": "assistant",
                "content": text,
                "tool_calls": [
                    {
                        "id": c.call_id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": canonical_json(c.args)},
                    }
                    for c in comp.tool_calls
                ],
            }
        )
        for call in comp.tool_calls:
            if call.name not in READ_ONLY_TOOLS:
                content = f"denied: sub-agent is read-only ({call.name})"
            else:
                try:
                    content = get_tool(call.name).run(call.args, ctx).output
                except (KeyError, OSError, ValueError) as exc:
                    content = f"tool error: {type(exc).__name__}: {exc}"
            messages.append({"role": "tool", "tool_call_id": call.call_id, "content": content})
    return text or "(hit turn limit)", {"turns": max_turns}


REVIEW_PROMPT = """\
You are a strict code reviewer. Inspect the repository read-only if needed, then review the diff.
Reply with ONLY a JSON object:
{"verdict": "approve" | "request_changes",
 "risks": ["concrete risk, with file:line when possible"],
 "missing_tests": ["behaviour that has no test"],
 "notes": "one short paragraph"}
Be specific and skeptical; do not approve untested behaviour.
"""


class ReviewerTool:
    name = "reviewer"
    effect = "read"
    description = "只读代码审查：对给定的 diff（或工作区改动）产出结构化风险清单。"
    parameters = {
        "type": "object",
        "properties": {
            "diff": {"type": "string", "description": "要审查的 diff；省略则用 git diff"},
            "focus": {"type": "string", "description": "可选关注点，如并发、错误处理"},
        },
    }

    MAX_TURNS = 3
    MAX_TOOL_CALLS = 6
    MAX_DIFF_CHARS = 20_000

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        if ctx.llm is None:
            return ToolResult(ok=False, output="reviewer unavailable: no LLM bound to this session")
        diff = str(args.get("diff") or "").strip()
        if not diff:
            try:
                diff = tasks.git(["diff"], ctx.workspace)
            except (RuntimeError, OSError) as exc:
                return ToolResult(ok=False, output=f"reviewer needs a diff: {exc}")
        if not diff.strip():
            return ToolResult(ok=True, output="(no changes to review)", meta={"verdict": "approve"})
        focus = str(args.get("focus") or "").strip()
        user = f"Diff to review:\n{diff[: self.MAX_DIFF_CHARS]}"
        if focus:
            user += f"\n\nFocus: {focus}"
        text, meta = readonly_loop(ctx, REVIEW_PROMPT, user, self.MAX_TURNS, self.MAX_TOOL_CALLS)
        review = parse_review(text)
        if review is None:
            return ToolResult(ok=True, output=text, meta={**meta, "parsed": False})
        return ToolResult(
            ok=True,
            output=f"verdict={review.get('verdict')} risks={len(review.get('risks') or [])}",
            meta={
                **meta,
                "parsed": True,
                "verdict": str(review.get("verdict") or ""),
                "risks": review.get("risks") or [],
                "missing_tests": review.get("missing_tests") or [],
                "notes": str(review.get("notes") or ""),
            },
        )


def parse_review(text: str) -> dict | None:
    cleaned = text.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and "verdict" in parsed else None


class UpdateTaskTool:
    name = "update_task"
    effect = "write"
    description = "更新 feature list 中某一项的通过状态与证据；全绿后 finish 才放行。"
    parameters = {
        "type": "object",
        "properties": {
            "feature_id": {"type": "string", "description": "feature list 里的条目 id"},
            "passes": {"type": "boolean"},
            "evidence": {"type": "string", "description": "证据：测试名、命令输出、文件:行"},
        },
        "required": ["feature_id", "passes"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        feature_id = str(args.get("feature_id") or "").strip()
        if not feature_id:
            return ToolResult(ok=False, output="feature_id is required")
        passes = bool(args.get("passes"))
        evidence = str(args.get("evidence") or "")
        tasks.set_feature(ctx.store, ctx.session_id, ctx.workspace, feature_id, passes, evidence)
        return ToolResult(
            ok=True,
            output=f"feature {feature_id!r} passes={passes}" + (f" evidence={evidence[:120]}" if evidence else ""),
            meta={"feature_id": feature_id, "passes": passes},
        )


class ProgressTool:
    name = "progress"
    effect = "write"
    description = "往 progress.md 追加一行进度；下一轮会以「进度末尾」进入上下文。"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "一行进度，建议含文件与验证结果"}},
        "required": ["text"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        text = str(args.get("text") or "").strip()
        if not text:
            return ToolResult(ok=False, output="text is required")
        tasks.append_progress(ctx.workspace, text)
        return ToolResult(ok=True, output=f"progress: {text[:160]}")


register(FinishTool())
register(ExplorerTool())
register(UpdateTaskTool())
register(ProgressTool())
register(ReviewerTool())
