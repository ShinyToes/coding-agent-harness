"""评测 runner：在真实仓库里跑真实模型，再用任务自带的 check.py 验收。

    $env:DEEPSEEK_API_KEY = "sk-..."
    python evals/run.py --label baseline
    python evals/run.py --label no-cache --no-cache
    python evals/run.py --label no-compact --no-compact --only long --window 6000 --compact-at 0.6

每个任务目录包含 task.md（给模型）、repo/（初始仓库，可选 setup.py 生成）、check.py（验收，模型看不到）。
结果写 evals/results/<label>.json，方便做消融对比。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from classify import classify
from rich.console import Console
from rich.table import Table

from harness.audit import make_audit
from harness.config import Settings
from harness.journal import steps_used
from harness.loop import run
from harness.models import EventType
from harness.store import Store

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"
WORK = ROOT.parent / ".evals" / "work"
STATE = ROOT.parent / ".evals" / "state"
RESULTS = ROOT / "results"
CONSOLE = Console()


def load_tasks(only: str | None, limit: int | None) -> list[str]:
    names = sorted(p.name for p in TASKS.iterdir() if (p / "task.md").exists())
    if only:
        names = [name for name in names if only in name]
    return names[:limit] if limit else names


def prepare(task: str, label: str) -> Path:
    source = TASKS / task / "repo"
    target = WORK / label / task
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    if source.exists():
        shutil.copytree(source, target, dirs_exist_ok=True)
    setup = TASKS / task / "setup.py"
    if setup.exists():
        proc = subprocess.run([sys.executable, str(setup)], cwd=target, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"{task}: setup failed: {proc.stderr[:400]}")
    return target


def make_cfg(work: Path, args) -> Settings:
    cfg = Settings().with_workspace(work)
    cfg.model.name = args.model
    cfg.model.api_base = args.api_base
    cfg.run.max_steps = args.max_steps
    cfg.run.read_cache = not args.no_cache
    cfg.run.compact_enabled = not args.no_compact
    cfg.run.turn_keep = args.turn_keep
    if args.window:
        cfg.model.window = args.window
    if args.max_tokens:
        cfg.model.max_tokens = args.max_tokens
    if args.compact_at:
        cfg.run.compact_at = args.compact_at
    return cfg


def run_task(task: str, label: str, args) -> dict:
    work = prepare(task, label)
    cfg = make_cfg(work, args)
    db_path = STATE / label / f"{task}.db"
    store = Store(db_path)

    text = (TASKS / task / "task.md").read_text(encoding="utf-8")
    session_id = store.create_session(text, str(work), cfg.model.name)
    store.append(
        session_id,
        EventType.SESSION_START,
        {
            "task": text,
            "repo": str(work),
            "base_commit": "",
            "model": cfg.model.name,
            "config_hash": cfg.config_hash(),
        },
    )
    store.append(session_id, EventType.USER_MESSAGE, {"text": text})

    approvals: list[str] = []

    def approve(call, ruling):
        approvals.append(f"{call.name}:{ruling.rule}")
        return "allow"

    started = time.perf_counter()
    try:
        status = run(session_id, store, cfg, approver=approve, audit=make_audit(cfg))
    except Exception as exc:  # noqa: BLE001 - 单任务失败不能中断整轮评测
        status = f"error:{type(exc).__name__}"
    duration = time.perf_counter() - started

    # 用 `python -c`：sys.path[0] 是 ""（即 cwd），任务仓库里的模块才能被 import
    check_code = (TASKS / task / "check.py").read_text(encoding="utf-8")
    check = subprocess.run(
        [sys.executable, "-c", check_code],
        cwd=work,
        capture_output=True,
        text=True,
        errors="replace",
        env={**os.environ, "EVAL_TASK_DIR": str(TASKS / task), "PYTHONPATH": str(work)},
    )
    events = store.events(session_id)
    session = store.session(session_id)
    passed, category = classify(status, check.returncode)
    result = {
        "task": task,
        "status": status,
        "passed": passed,
        "category": category,
        "check_rc": check.returncode,
        "check_out": (check.stdout + check.stderr).strip()[:400],
        "steps": steps_used(events),
        "tokens": session.tokens_total,
        "cost": round(session.cost, 6),
        "seconds": round(duration, 1),
        "approvals": len(approvals),
        "compactions": sum(1 for e in events if e.type == EventType.CONTEXT_COMPACT),
        "errors": sum(1 for e in events if e.type == EventType.ERROR),
        "session": session_id,
    }
    store.close()
    return result


def run_offline(tasks: list[str]) -> int:
    """离线自检：每个任务在**未修改**的初始仓库上跑验收，必须失败。

    如果某个任务初始就是绿的，说明验收没有真正检查新行为（任务白给），CI 应报错。
    """
    bad = 0
    for name in tasks:
        try:
            work = prepare(name, "offline")
        except RuntimeError as exc:
            CONSOLE.print(f"  [red]{name}: prepare failed: {exc}[/]")
            bad += 1
            continue
        check_code = (TASKS / name / "check.py").read_text(encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", check_code],
            cwd=work,
            capture_output=True,
            text=True,
            errors="replace",
            env={**os.environ, "EVAL_TASK_DIR": str(TASKS / name), "PYTHONPATH": str(work)},
        )
        if proc.returncode == 0:
            bad += 1
            CONSOLE.print(f"  [red]{name}: baseline is green (task checks nothing new)[/]")
        else:
            CONSOLE.print(f"  [green]{name}: baseline red as expected[/]")
    return bad


def main() -> None:
    # 等价于激活 venv：让 agent 的 `bash python ...` 用到当前解释器，而不是系统 Python
    venv_bin = str(Path(sys.executable).parent)
    os.environ["PATH"] = venv_bin + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("VIRTUAL_ENV", str(Path(sys.executable).parents[1]))

    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--api-base", default="https://api.deepseek.com")
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--window", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--compact-at", type=float, default=None)
    parser.add_argument("--turn-keep", type=int, default=2)
    parser.add_argument("--only", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-cache", action="store_true", help="消融：关闭 read 缓存")
    parser.add_argument("--no-compact", action="store_true", help="消融：关闭自动压缩")
    parser.add_argument("--dry-run", action="store_true", help="只列出任务")
    parser.add_argument("--offline", action="store_true", help="离线自检：初始仓库必须验收失败")
    args = parser.parse_args()

    tasks = load_tasks(args.only, args.limit)
    if not tasks:
        raise SystemExit("没有匹配的任务")
    if args.dry_run:
        for name in tasks:
            CONSOLE.print(name)
        return
    if args.offline:
        CONSOLE.print(f"[bold]offline self-check[/] tasks={len(tasks)}")
        bad = run_offline(tasks)
        if bad:
            raise SystemExit(f"{bad}/{len(tasks)} tasks are trivially green")
        CONSOLE.print(f"[green]{len(tasks)}/{len(tasks)} baselines are red as expected[/]")
        return

    CONSOLE.print(f"[bold]eval[/] label={args.label} tasks={len(tasks)} model={args.model}")
    rows = []
    for name in tasks:
        CONSOLE.print(f"  running [cyan]{name}[/] ...")
        row = run_task(name, args.label, args)
        rows.append(row)
        mark = "[green]pass[/]" if row["passed"] else "[red]fail[/]"
        CONSOLE.print(
            f"    {mark} category={row['category']} status={row['status']} steps={row['steps']} "
            f"tokens={row['tokens']} cost=${row['cost']:.6f} {row['seconds']}s"
        )

    table = Table(title=f"eval {args.label}")
    for column in ("task", "category", "status", "steps", "tokens", "cost", "compacts"):
        table.add_column(column, justify="right" if column not in {"task", "category", "status"} else "left")
    for row in rows:
        table.add_row(
            row["task"],
            row["category"],
            row["status"],
            str(row["steps"]),
            str(row["tokens"]),
            f"${row['cost']:.6f}",
            str(row["compactions"]),
        )
    CONSOLE.print(table)

    categories: dict[str, int] = {}
    for row in rows:
        categories[row["category"]] = categories.get(row["category"], 0) + 1

    summary = {
        "label": args.label,
        "model": args.model,
        "flags": {
            "no_cache": args.no_cache,
            "no_compact": args.no_compact,
            "window": args.window,
            "compact_at": args.compact_at,
            "turn_keep": args.turn_keep,
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "passed": sum(1 for r in rows if r["passed"]),
        "total": len(rows),
        "categories": categories,
        "tokens": sum(r["tokens"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 6),
        "rows": rows,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{args.label}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    CONSOLE.print(
        f"[bold]{summary['passed']}/{summary['total']} passed[/]  categories={categories}  "
        f"tokens={summary['tokens']}  cost=${summary['cost']:.6f}  → {out}"
    )


if __name__ == "__main__":
    main()
