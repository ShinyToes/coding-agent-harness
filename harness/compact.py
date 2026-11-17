"""压缩：把最早的一段事件摘要成 context.compact 事件。

任务状态（feature list）不在候选集里，永远以原文进 S1 —— 摘要丢细节不能丢任务。
"""

from __future__ import annotations

import json
from contextlib import nullcontext

from harness.context import build_context, estimate_tokens
from harness.models import Event, EventType, canonical_json, utcnow

MAX_TRANSCRIPT_CHARS = 24_000
KEEP_TYPES_EXEMPT = {
    EventType.SESSION_START,
    EventType.TASK_UPDATE,
    EventType.CONTEXT_COMPACT,
    EventType.MODEL_REQUEST,
}

SUMMARY_PROMPT = """\
You compress agent history. Read the transcript and reply with ONLY a JSON object:
{"summary": "<= 800 chars, what happened and where things stand",
 "decisions": ["technical decisions already made"],
 "open_items": ["unfinished work"],
 "files_touched": ["path"],
 "dead_ends": ["approaches tried that failed"]}
Keep concrete identifiers (paths, function names, commands, error messages). No prose outside the JSON.
"""


def plan_compaction(events: list[Event], cfg, force: bool = False) -> list[Event]:
    covered_to = 0
    for event in events:
        if event.type == EventType.CONTEXT_COMPACT:
            covered_to = int(event.data.get("covers_to") or 0)
    candidates = [e for e in events if e.seq > covered_to and e.type not in KEEP_TYPES_EXEMPT]
    turn_starts = [i for i, e in enumerate(candidates) if e.type == EventType.MODEL_RESPONSE]
    keep = 1 if force else cfg.run.turn_keep
    if len(turn_starts) <= keep:
        if not force or len(candidates) < 2:
            return []
        return candidates[:-1]  # 强制压缩：除了最后一条全部覆盖
    keep_from = turn_starts[-keep]
    cut = min(int(len(candidates) * 0.6), keep_from)
    if cut <= 0:
        return []
    return candidates[:cut]


def transcript(events: list[Event]) -> str:
    lines: list[str] = []
    for event in events:
        data = event.data
        if event.type == EventType.USER_MESSAGE:
            lines.append(f"[user] {str(data.get('text') or '')[:800]}")
        elif event.type == EventType.MODEL_RESPONSE:
            text = str(data.get("text") or "")[:800]
            calls = ", ".join(
                f"{c.get('name')}({canonical_json(c.get('args') or {})[:160]})" for c in data.get("tool_calls") or []
            )
            lines.append(f"[assistant] {text}" + (f" | tools: {calls}" if calls else ""))
        elif event.type == EventType.TOOL_RESULT:
            lines.append(
                f"[tool {data.get('call_id')}] ok={data.get('ok')} {str(data.get('output_preview') or '')[:600]}"
            )
        elif event.type == EventType.POLICY_DECISION and data.get("decision") != "allow":
            lines.append(f"[policy] {data.get('decision')} ({data.get('rule')}) {data.get('reason')}")
        elif event.type == EventType.APPROVAL_RESPONSE:
            lines.append(f"[approval] {data.get('decision')} by {data.get('actor')}")
        elif event.type == EventType.ERROR:
            lines.append(f"[error] {data.get('where')}: {data.get('message')}")
    text = "\n".join(lines)
    return text[:MAX_TRANSCRIPT_CHARS]


def parse_summary(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "summary" in parsed:
            return parsed
    return None


def compact(store, session_id: str, cfg, llm, audit=None, force: bool = False) -> Event | None:
    events = store.events(session_id)
    covered = plan_compaction(events, cfg, force=force)
    if not covered:
        return None
    before = build_context(events, cfg)[1].tokens
    body = transcript(covered)
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": body},
    ]
    request_ref = store.put_artifact(session_id, "compact_request", canonical_json(messages).encode())
    # 摘要也是一次模型调用：不记 model.request 的话，审计/成本/回放里都会缺这一笔
    store.append(
        session_id,
        EventType.MODEL_REQUEST,
        {
            "artifact_id": request_ref,
            "kind": "compact",
            "n_messages": len(messages),
            "est_tokens": estimate_tokens(body, cfg),
            "tools_hash": "",
        },
    )
    completion = llm.complete(messages, [])
    store.add_usage(session_id, completion.prompt_tokens + completion.completion_tokens, completion.cost)
    parsed = parse_summary(completion.text)
    if parsed is None:
        store.append(
            session_id,
            EventType.ERROR,
            {
                "where": "compact",
                "message": f"summary not parseable, kept raw excerpt: {completion.text[:200]}",
                "stack_hash": "",
            },
        )
    summary = parsed or {}
    data = {
        "covers_from": covered[0].seq,
        "covers_to": covered[-1].seq,
        "summary": str(summary.get("summary") or completion.text[:2_000]),
        "decisions": summary.get("decisions") or [],
        "open_items": summary.get("open_items") or [],
        "files_touched": summary.get("files_touched") or [],
        "dead_ends": summary.get("dead_ends") or [],
        "tokens_before": before,
        "request_artifact": request_ref,
        "fallback": parsed is None,
    }
    preview_event = Event(
        session_id=session_id,
        seq=events[-1].seq + 1,
        type=EventType.CONTEXT_COMPACT,
        ts=utcnow(),
        data=data,
    )
    after = build_context([*events, preview_event], cfg)[1].tokens
    data["tokens_after"] = after
    data["compressed_tokens"] = max(0, before - after)
    span_cm = audit.compact(before, after, f"{data['covers_from']}-{data['covers_to']}") if audit else nullcontext()
    with span_cm:
        return store.append(session_id, EventType.CONTEXT_COMPACT, data)
