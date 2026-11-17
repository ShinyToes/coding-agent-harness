"""任务状态：feature list / progress / git 查询。

任务状态是长跑地基：它以原文进 S1，不参与摘要压缩。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from harness.models import Event, EventType


def git(args: list[str], cwd: str | Path, timeout: int = 30) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.rstrip("\n")


def head_or_empty(cwd: str | Path) -> str:
    try:
        return git(["rev-parse", "HEAD"], cwd)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return ""  # 非 git 仓库：漂移检查自动禁用，会话照常跑


def diff(base: str, cwd: str | Path) -> str:
    """base 之后所有改动：包含工作区未提交内容，才是「本 session 干了什么」。"""
    return git(["diff", base], cwd) if base else git(["diff"], cwd)


def features_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".harness" / "tasks" / "feature_list.json"


def load_features(workspace: str | Path) -> list[dict]:
    path = features_path(workspace)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    features = raw.get("features") if isinstance(raw, dict) else raw
    return list(features or [])


def save_features(workspace: str | Path, features: list[dict]) -> None:
    path = features_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"features": features}, ensure_ascii=False, indent=2), encoding="utf-8")


def set_feature(store, session_id: str, workspace: str | Path, feature_id: str, passes: bool, evidence: str = "") -> Event:
    features = load_features(workspace)
    entry = next((f for f in features if f.get("id") == feature_id), None)
    if entry is None:
        entry = {"id": feature_id}
        features.append(entry)
    entry["passes"] = bool(passes)
    entry["evidence"] = evidence
    save_features(workspace, features)
    return store.append(
        session_id,
        EventType.TASK_UPDATE,
        {"feature_id": feature_id, "passes": bool(passes), "evidence": evidence},
    )


def progress_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".harness" / "tasks" / "progress.md"


def append_progress(workspace: str | Path, line: str) -> None:
    path = progress_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")


def progress_tail(workspace: str | Path, lines: int = 40) -> str:
    path = progress_path(workspace)
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(content[-lines:])
