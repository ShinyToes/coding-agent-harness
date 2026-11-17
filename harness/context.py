"""上下文组装：纯函数 build_context(events, cfg) -> (messages, stats)。

不变量：无隐藏状态、无 DB 副作用；相同 events+cfg 必得相同 messages。
预算按 S0-S5 优先级装填，超预算从最旧的一轮开始丢。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from harness import tokens
from harness.models import Event, EventType, canonical_json

SYSTEM_PROMPT = """\
You are a coding agent working in a single repository workspace.
Rules:
- Everything you change must be inside the workspace. Use the tools; never claim a change you did not make.
- Prefer read/grep/glob before editing. Edits go through write or patch (SEARCH/REPLACE or unified diff).
- Every shell command is policy-gated; a denied call returns a failure result, do not retry it unchanged.
- Shell commands run through the host shell (cmd.exe on Windows, /bin/sh on POSIX). Do not assume bash-only syntax such as `$?`, `$(...)` or heredocs.
- Tool output is truncated in this transcript; the full text is stored as an artifact id. Call `read_artifact` with that id when you need the omitted middle.
- Record task progress with `update_task` (feature id / passes / evidence) and `progress`; they become part of the task brief next turn.
- When the task is complete (or truly blocked), call `finish` with a concrete summary of what changed and how you verified it. Edits must be followed by a successful `bash` verification, or every feature marked passing, otherwise `finish` is rejected.
- As soon as your last edit is followed by a successful verification, call `finish` in that same turn. Do not keep exploring or re-verifying once the acceptance command passes.
"""


class ContextStats(BaseModel):
    tokens: int = 0
    n_messages: int = 0
    dropped_groups: int = 0
    covered_to: int = 0
    sections: dict[str, int] = Field(default_factory=dict)


def estimate_tokens(text: str, cfg) -> int:
    return tokens.count(text, cfg.calibration if cfg.calibration and cfg.calibration > 0 else 3.0)


def calibrate(chars: int, real_prompt_tokens: int) -> float:
    if real_prompt_tokens <= 0 or chars <= 0:
        return 3.0
    return round(min(6.0, max(1.5, chars / real_prompt_tokens)), 4)


def message_tokens(message: dict, cfg) -> int:
    """一条消息的 token 估算：content + assistant tool_calls 的 arguments。

    只算 content 会漏掉 patch 正文这类大参数，预算被低估、压缩触发偏晚。
    """
    total = estimate_tokens(str(message.get("content") or ""), cfg) + 8
    for call in message.get("tool_calls") or []:
        total += estimate_tokens(canonical_json(call.get("function") or {}), cfg)
    return total


def features_from_events(events: list[Event]) -> dict[str, dict]:
    features: dict[str, dict] = {}
    for event in events:
        if event.type != EventType.TASK_UPDATE:
            continue
        fid = str(event.data.get("feature_id") or "")
        if fid:
            features[fid] = event.data
    return features


def task_brief(events: list[Event], cfg) -> str:
    features = features_from_events(events)
    lines: list[str] = []
    task = next((str(e.data.get("task") or "") for e in events if e.type == EventType.SESSION_START), "")
    if task:
        lines.append("## 任务\n" + task.strip()[:2_000])
    if features:
        lines.append("## Feature list（来自 task.update，压缩不会丢）")
        for fid, data in features.items():
            mark = "x" if data.get("passes") else " "
            evidence = str(data.get("evidence") or "").strip()
            lines.append(f"- [{mark}] {fid}" + (f" — {evidence[:160]}" if evidence else ""))
    if cfg.task_brief:
        tail = cfg.task_brief.strip()
        lines.append("## 进度末尾\n" + tail[-4000:])
    return "\n".join(lines)


def render_compact(data: dict) -> str:
    lines = ["## 已压缩历史（摘要，细节见 artifact）", str(data.get("summary") or "")]
    for key, label in (
        ("decisions", "已确定决策"),
        ("open_items", "未完成事项"),
        ("files_touched", "涉及文件"),
        ("dead_ends", "死路"),
    ):
        items = [str(i) for i in (data.get(key) or [])][:20]
        if items:
            lines.append(f"{label}: " + "; ".join(items))
    return "\n".join(lines)


def render_tool_result(data: dict, cfg) -> str:
    chars = int(data.get("chars") or 0)
    preview = str(data.get("output_preview") or "")
    if chars <= cfg.max_artifact_preview:
        text = preview
    else:
        tail = str(data.get("tail") or "")
        text = (
            preview[:400]
            + f"\n...[truncated {chars - 800} chars, artifact={data.get('artifact_id', '')}]...\n"
            + tail[-400:]
        )
    return text if data.get("ok", True) else "ERROR: " + text


def group_events(events: list[Event]) -> list[list[Event]]:
    groups: list[list[Event]] = []
    for event in events:
        if event.type in (EventType.MODEL_RESPONSE, EventType.USER_MESSAGE) or not groups:
            groups.append([event])
        else:
            groups[-1].append(event)
    return groups


def messages_for_group(group: list[Event], cfg) -> list[dict]:
    out: list[dict] = []
    results = {e.call_id: e for e in group if e.type == EventType.TOOL_RESULT}
    for event in group:
        if event.type == EventType.USER_MESSAGE:
            out.append({"role": "user", "content": str(event.data.get("text") or "")})
    response = next((e for e in group if e.type == EventType.MODEL_RESPONSE), None)
    if response is None:
        return out
    calls = response.data.get("tool_calls") or []
    message: dict = {"role": "assistant", "content": str(response.data.get("text") or "")}
    if calls:
        message["tool_calls"] = [
            {
                "id": str(call.get("call_id") or ""),
                "type": "function",
                "function": {"name": str(call.get("name") or ""), "arguments": canonical_json(call.get("args") or {})},
            }
            for call in calls
        ]
    out.append(message)
    for call in calls:
        cid = str(call.get("call_id") or "")
        result = results.get(cid)
        content = render_tool_result(result.data, cfg) if result else "[pending execution]"
        out.append({"role": "tool", "tool_call_id": cid, "content": content})
    return out


def build_context(events: list[Event], cfg, snapshot: dict | None = None) -> tuple[list[dict], ContextStats]:
    """纯函数：相同 events + cfg（或相同 snapshot）必得相同 messages。

    snapshot 是当年那次 model.request 记录的 S1/S2 快照，replay 用它对齐历史请求；
    不传则用 cfg 里的注入值（live 路径一直如此）。
    """
    if snapshot:
        cfg = cfg.model_copy(
            update={
                "task_brief": snapshot.get("task_brief", cfg.task_brief),
                "repo_map": snapshot.get("repo_map", cfg.repo_map),
                "calibration": float(snapshot.get("calibration", cfg.calibration)),
            }
        )
    stats = ContextStats()
    compacts = [e for e in events if e.type == EventType.CONTEXT_COMPACT]
    latest = compacts[-1] if compacts else None
    covered_to = int(latest.data.get("covers_to") or 0) if latest else 0
    stats.covered_to = covered_to

    sections = [SYSTEM_PROMPT]
    brief = task_brief(events, cfg)
    if brief:
        sections.append(brief)
    if cfg.repo_map:
        sections.append("## Repo map\n" + cfg.repo_map[: 800 * 3])
    if latest:
        sections.append(render_compact(latest.data))
    system_text = "\n\n".join(sections)
    system_tokens = estimate_tokens(system_text, cfg)
    stats.sections["system"] = system_tokens

    messages: list[dict] = [{"role": "system", "content": system_text}]
    budget = cfg.budget_tokens() - system_tokens

    # 只有这些事件会产生消息；CONTEXT_COMPACT 等记账事件不参与分组与覆盖判定。
    rendered_types = {
        EventType.USER_MESSAGE,
        EventType.MODEL_RESPONSE,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.POLICY_DECISION,
        EventType.APPROVAL_REQUEST,
        EventType.APPROVAL_RESPONSE,
    }
    renderable = [e for e in events if e.type in rendered_types]
    rendered = [(g, messages_for_group(g, cfg)) for g in group_events(renderable)]
    rendered = [(g, m) for g, m in rendered if m and max(e.seq for e in g) > covered_to]

    kept: list[list[dict]] = []
    used = 0
    for _group, msgs in reversed(rendered):
        cost = sum(message_tokens(m, cfg) for m in msgs)
        if kept and used + cost > budget:
            break
        kept.append(msgs)
        used += cost
    kept.reverse()
    stats.dropped_groups = len(rendered) - len(kept)

    for msgs in kept:
        messages.extend(msgs)

    # S5：当前 user.message 永不丢。
    user_events = [e for e in events if e.type == EventType.USER_MESSAGE]
    if user_events:
        text = str(user_events[-1].data.get("text") or "")
        if not any(m.get("role") == "user" and m.get("content") == text for m in messages):
            messages.append({"role": "user", "content": text})

    stats.n_messages = len(messages)
    stats.tokens = sum(message_tokens(m, cfg) for m in messages)
    return messages, stats
