"""Replay：用事件里记录的 S1/S2 快照重建某次 model.request，并和记录值比对。

为什么需要这个模块：S1（进度末尾）和 S2（repo map）来自文件系统，不在事件流里。
不记录快照的话，`replay` 重建的是「换一个配置重跑」的请求，而不是模型当时真正看到的那份。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from harness.context import ContextStats, build_context
from harness.models import Event, EventType, canonical_json


@dataclass
class ReplayCheck:
    messages: list[dict]
    stats: ContextStats
    sha: str
    record_seq: int | None = None
    match: bool | None = None


def _sha(messages: list[dict]) -> str:
    return hashlib.sha256(canonical_json(messages).encode()).hexdigest()


def last_request(events: list[Event]) -> Event | None:
    requests = [
        e for e in events if e.type == EventType.MODEL_REQUEST and e.data.get("snapshot_artifact")
    ]
    return requests[-1] if requests else None


def rebuild(store, events: list[Event], cfg) -> ReplayCheck:
    request = last_request(events)
    if request is None:
        messages, stats = build_context(events, cfg)
        return ReplayCheck(messages=messages, stats=stats, sha=_sha(messages))

    before = [e for e in events if e.seq < request.seq]
    try:
        snapshot = json.loads(store.get_artifact_text(str(request.data["snapshot_artifact"])))
    except (KeyError, FileNotFoundError, ValueError):
        snapshot = {}
    messages, stats = build_context(before, cfg, snapshot=snapshot)

    match: bool | None = None
    recorded_ref = request.data.get("artifact_id")
    if recorded_ref:
        try:
            recorded = json.loads(store.get_artifact_text(str(recorded_ref))).get("messages")
        except (KeyError, FileNotFoundError, ValueError):
            recorded = None
        if isinstance(recorded, list):
            match = canonical_json(recorded) == canonical_json(messages)
    return ReplayCheck(
        messages=messages,
        stats=stats,
        sha=_sha(messages),
        record_seq=request.seq,
        match=match,
    )
