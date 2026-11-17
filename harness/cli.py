"""CLI：h run / resume / replay / trace / diff / approvals。"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from harness import tasks
from harness.config import Settings
from harness.log import setup as setup_logging
from harness.loop import plan_resume
from harness.loop import run as run_loop
from harness.models import EventType, canonical_json
from harness.replay import rebuild
from harness.store import Store

app = typer.Typer(add_completion=False, help="单机本地 coding agent harness")
console = Console()

DEFAULT_CONFIG = Path("harness.toml")


def _store(db: Path | None, repo: Path) -> Store:
    path = Path(db) if db else Path(repo).resolve() / ".harness" / "harness.db"
    return Store(path)


def _cfg(config: Path | None, repo: Path, model: str | None, session=None) -> Settings:
    cfg = Settings.load(Path(config) if config else DEFAULT_CONFIG).with_workspace(repo)
    if model:
        cfg.model.name = model
    if session is not None:
        cfg.calibration = session.calibration
    return cfg


def summarize(event) -> str:
    data, kind = event.data, event.type
    if kind == EventType.SESSION_START:
        return f"{data.get('model')} @ {data.get('repo')}"
    if kind == EventType.USER_MESSAGE:
        return str(data.get("text") or "")[:80]
    if kind == EventType.MODEL_REQUEST:
        return f"n_messages={data.get('n_messages')} est_tokens={data.get('est_tokens')}"
    if kind == EventType.MODEL_RESPONSE:
        calls = data.get("tool_calls") or []
        if calls:
            return ", ".join(f"{c.get('name')}({canonical_json(c.get('args') or {})[:60]})" for c in calls)
        return str(data.get("text") or "")[:80]
    if kind == EventType.TOOL_CALL:
        return f"{data.get('name')} {canonical_json(data.get('args') or {})[:80]}"
    if kind == EventType.POLICY_DECISION:
        return f"{data.get('decision')} (rule={data.get('rule')})"
    if kind == EventType.APPROVAL_REQUEST:
        return str(data.get("summary") or "")[:80]
    if kind == EventType.APPROVAL_RESPONSE:
        return f"{data.get('decision')} ({data.get('actor')})"
    if kind == EventType.TOOL_RESULT:
        flag = "ok" if data.get("ok") else "fail"
        return f"{flag} {data.get('chars')} chars artifact={str(data.get('artifact_id') or '')[:8]}"
    if kind == EventType.CONTEXT_COMPACT:
        return f"{data.get('covers_from')}-{data.get('covers_to')} {data.get('tokens_before')}->{data.get('tokens_after')}"
    if kind == EventType.TASK_UPDATE:
        return f"{data.get('feature_id')} passes={data.get('passes')}"
    if kind == EventType.ERROR:
        return f"{data.get('where')}: {data.get('message')}"[:100]
    if kind == EventType.SESSION_END:
        return f"{data.get('status')} {data.get('reason')}"
    return ""


@app.command()
def run(
    task: str = typer.Argument("", help="任务描述；或配合 --task-file"),
    task_file: Path = typer.Option(None, exists=True, help="从文件读任务"),
    repo: Path = typer.Option(Path("."), exists=True, help="仓库/workspace"),
    model: str = typer.Option(None),
    config: Path = typer.Option(None, exists=True),
    db: Path = typer.Option(None, help="sqlite 路径，默认 <repo>/.harness/harness.db"),
    yes: bool = typer.Option(False, "--yes", help="非交互：所有 ask 降级为 deny"),
    max_steps: int = typer.Option(None),
    max_cost: float = typer.Option(None, "--max-cost", help="成本上限（美元），超过后结束会话"),
    quiet: bool = typer.Option(False, "--quiet", help="不打印实时进度"),
) -> None:
    text = task.strip() or (task_file.read_text(encoding="utf-8") if task_file else "")
    if not text.strip():
        raise typer.BadParameter("需要任务描述或 --task-file")
    cfg = _cfg(config, repo, model)
    if yes:
        cfg.policy.approval_mode = "deny"
    if max_steps:
        cfg.run.max_steps = max_steps
    if max_cost:
        cfg.run.max_cost = max_cost
    store = _store(db, repo)
    base_commit = tasks.head_or_empty(cfg.workspace)
    session_id = store.create_session(text, str(cfg.workspace), cfg.model.name, base_commit=base_commit)
    store.append(
        session_id,
        EventType.SESSION_START,
        {
            "task": text,
            "repo": str(cfg.workspace),
            "base_commit": base_commit,
            "model": cfg.model.name,
            "config_hash": cfg.config_hash(),
        },
    )
    store.append(session_id, EventType.USER_MESSAGE, {"text": text})
    console.print(f"[bold]session[/] {session_id}  repo={cfg.workspace}  model={cfg.model.name}")
    status = run_loop(session_id, store, cfg, on_event=None if quiet else _progress)
    console.print(f"[bold]status[/] {status}\n[dim]db={store.path}[/]")
    store.close()


@app.command()
def resume(
    session_id: str,
    repo: Path = typer.Option(None, help="覆盖 session 记录的 repo"),
    config: Path = typer.Option(None, exists=True),
    db: Path = typer.Option(None),
    yes: bool = typer.Option(False, "--yes"),
    quiet: bool = typer.Option(False, "--quiet", help="不打印实时进度"),
) -> None:
    store = _store(db, repo or Path("."))
    session = store.session(session_id)
    base_repo = repo or (Path(session.repo) if session.repo else Path("."))
    cfg = _cfg(config, base_repo, session.model or None, session)
    if yes:
        cfg.policy.approval_mode = "deny"
    plan = plan_resume(store.events(session_id))
    console.print(f"[bold]resume plan[/] {plan}")
    if plan == "done":
        console.print("session 已结束，无需恢复")
        store.close()
        return
    status = run_loop(session_id, store, cfg, on_event=None if quiet else _progress)
    console.print(f"[bold]status[/] {status}")
    store.close()


@app.command()
def replay(
    session_id: str,
    to_seq: int = typer.Option(None, "--to-seq", help="只回放到该 seq"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只打印指纹，不打印 messages"),
    repo: Path = typer.Option(None),
    config: Path = typer.Option(None, exists=True),
    db: Path = typer.Option(None),
) -> None:
    store = _store(db, repo or Path("."))
    session = store.session(session_id)
    cfg = _cfg(config, Path(session.repo) if session.repo else Path("."), session.model or None, session)
    events = store.events(session_id)
    if to_seq:
        events = [e for e in events if e.seq <= to_seq]
    check = rebuild(store, events, cfg)
    console.print(
        f"messages={len(check.messages)} est_tokens={check.stats.tokens} "
        f"covered_to={check.stats.covered_to} sha256={check.sha}"
    )
    if check.record_seq is None:
        console.print("[dim]recorded_match=n/a（该 session 没有快照，属于旧格式）[/]")
    else:
        mark = "[green]match[/]" if check.match else "[red]mismatch[/]"
        console.print(f"recorded seq={check.record_seq} {mark}")
    if not dry_run:
        console.print_json(canonical_json(check.messages))
    store.close()


def event_tokens(event) -> tuple[int, int, float]:
    """(prompt, completion, cost)；只有 model.response 有值。"""
    if event.type != EventType.MODEL_RESPONSE:
        return 0, 0, 0.0
    usage = event.data.get("usage") or {}
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), float(event.data.get("cost") or 0.0)


PROGRESS_TYPES = {
    EventType.MODEL_RESPONSE,
    EventType.TOOL_CALL,
    EventType.TOOL_RESULT,
    EventType.POLICY_DECISION,
    EventType.APPROVAL_REQUEST,
    EventType.APPROVAL_RESPONSE,
    EventType.CONTEXT_COMPACT,
    EventType.TASK_UPDATE,
    EventType.ERROR,
    EventType.SESSION_END,
}


def _progress(event) -> None:
    """h run 的实时进度：一行一个事件，字段顺序便于 grep。"""
    if event.type not in PROGRESS_TYPES:
        return
    line = Text(f"{event.seq:>3} ", style="dim")
    line.append(f"{event.type!s:<17} ")
    line.append(summarize(event)[:110])
    prompt, completion, spent = event_tokens(event)
    if event.type == EventType.MODEL_RESPONSE:
        line.append(f"  {prompt + completion} tok ${spent:.6f}", style="dim")
    console.print(line)


@app.command()
def trace(
    session_id: str,
    repo: Path = typer.Option(None),
    db: Path = typer.Option(None),
    limit: int = typer.Option(300),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON 行，便于脚本处理"),
) -> None:
    store = _store(db, repo or Path("."))
    events = store.events(session_id)[:limit]
    session = store.session(session_id)

    if as_json:
        rows = [
            {
                "seq": event.seq,
                "type": str(event.type),
                "ts": event.ts.isoformat(),
                "summary": summarize(event),
                "tokens": sum(event_tokens(event)[:2]),
                "cost": event_tokens(event)[2],
            }
            for event in events
        ]
        console.print_json(json.dumps({"session": session.id, "status": session.status, "events": rows}, ensure_ascii=False))
        store.close()
        return

    table = Table(title=f"trace {session_id}")
    for column in ("seq", "type", "tokens", "cost", "summary"):
        table.add_column(column, overflow="fold")
    for event in events:
        prompt, completion, cost = event_tokens(event)
        # Text()：summary 里可能有 `ordered[mid]` 这类方括号，不能被当成 rich markup 吃掉
        table.add_row(
            str(event.seq),
            str(event.type),
            str(prompt + completion) if prompt or completion else "",
            f"${cost:.6f}" if cost else "",
            Text(summarize(event)),
        )
    console.print(table)
    console.print(
        f"[bold]tokens[/] {session.tokens_total}  [bold]cost[/] ${session.cost:.6f}  "
        f"[bold]events[/] {len(events)}  [bold]status[/] {session.status}"
    )
    store.close()


@app.command()
def sessions(
    repo: Path = typer.Option(None),
    db: Path = typer.Option(None),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    store = _store(db, repo or Path("."))
    rows = store.sessions()
    if as_json:
        payload = [
            {
                "id": s.id,
                "status": s.status,
                "model": s.model,
                "tokens": s.tokens_total,
                "cost": s.cost,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "task": s.task[:200],
            }
            for s in rows
        ]
        console.print_json(json.dumps(payload, ensure_ascii=False))
        store.close()
        return
    table = Table(title="sessions")
    for column in ("id", "status", "model", "tokens", "cost", "task"):
        table.add_column(column, overflow="fold")
    for s in rows:
        table.add_row(s.id, s.status, s.model, str(s.tokens_total), f"${s.cost:.6f}", Text(s.task[:60]))
    console.print(table)
    store.close()


@app.command()
def cost(
    session_id: str,
    repo: Path = typer.Option(None),
    db: Path = typer.Option(None),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    store = _store(db, repo or Path("."))
    rows = []
    total_prompt = total_completion = 0
    total_cost = 0.0
    for event in store.events(session_id):
        prompt, completion, spent = event_tokens(event)
        if event.type != EventType.MODEL_RESPONSE:
            continue
        rows.append(
            {
                "seq": event.seq,
                "model": event.data.get("model", ""),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cost": round(spent, 6),
            }
        )
        total_prompt += prompt
        total_completion += completion
        total_cost += spent

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "session": session_id,
                    "calls": rows,
                    "prompt_tokens": total_prompt,
                    "completion_tokens": total_completion,
                    "cost": round(total_cost, 6),
                },
                ensure_ascii=False,
            )
        )
        store.close()
        return

    table = Table(title=f"cost {session_id}")
    for column in ("seq", "model", "prompt", "completion", "cost"):
        table.add_column(column, justify="right" if column != "model" else "left")
    for row in rows:
        table.add_row(str(row["seq"]), row["model"], str(row["prompt_tokens"]), str(row["completion_tokens"]), f"${row['cost']:.6f}")
    console.print(table)
    console.print(
        f"[bold]total[/] prompt={total_prompt} completion={total_completion} cost=${total_cost:.6f}"
    )
    store.close()


@app.command()
def diff(
    session_id: str,
    repo: Path = typer.Option(None),
    db: Path = typer.Option(None),
) -> None:
    store = _store(db, repo or Path("."))
    session = store.session(session_id)
    workspace = Path(session.repo) if session.repo else Path(".")
    try:
        output = tasks.diff(session.base_commit, workspace)
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]git diff failed[/]: {exc}")
        store.close()
        raise typer.Exit(code=1) from exc
    console.print(output or "(no changes)")
    store.close()


@app.command()
def approvals(
    session_id: str,
    repo: Path = typer.Option(None),
    db: Path = typer.Option(None),
) -> None:
    store = _store(db, repo or Path("."))
    table = Table(title=f"approvals {session_id}")
    for column in ("seq", "call_id", "kind", "detail"):
        table.add_column(column, overflow="fold")
    for event in store.events(session_id):
        data = event.data
        if event.type == EventType.POLICY_DECISION:
            table.add_row(str(event.seq), str(data.get("call_id")), "policy", f"{data.get('decision')} ({data.get('rule')})")
        elif event.type in (EventType.APPROVAL_REQUEST, EventType.APPROVAL_RESPONSE):
            detail = data.get("summary") or data.get("decision")
            table.add_row(str(event.seq), str(data.get("call_id")), str(event.type), str(detail))
    console.print(table)
    store.close()


@app.command()
def gc(
    repo: Path = typer.Option(None),
    db: Path = typer.Option(None),
    keep_days: int = typer.Option(7, "--keep-days", help="只删这个天数之前产生的无引用 artifact"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只统计，不删除"),
) -> None:
    """清理事件流不再引用的 artifact；events 表永远不动。"""
    store = _store(db, repo or Path("."))
    stats = store.gc(keep_days=keep_days, dry_run=dry_run)
    mode = "dry-run" if stats["dry_run"] else "deleted"
    console.print(
        f"[bold]{mode}[/] artifacts: scanned={stats['scanned']} referenced={stats['referenced']} "
        f"deleted={stats['deleted']} freed={stats['freed_bytes']} bytes"
    )
    if stats["sample"]:
        console.print("[dim]" + ", ".join(stats["sample"]) + "[/]")
    store.close()


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":
    main()
