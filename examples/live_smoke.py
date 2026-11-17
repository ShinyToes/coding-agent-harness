"""真模型冒烟测试：让模型在真实仓库里改代码，走完 read → patch → bash → finish。

    $env:DEEPSEEK_API_KEY = "sk-..."
    python examples/live_smoke.py                 # 场景 bug：修 average
    python examples/live_smoke.py --scenario feature   # 场景 feature：补 median + 改 check.py

可选环境变量：HARNESS_MODEL（默认 deepseek-chat）、HARNESS_API_BASE、HARNESS_MAX_STEPS。
不设 key 时直接退出，不会产生任何请求。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from harness.audit import make_audit
from harness.cli import summarize
from harness.config import Settings
from harness.context import build_context
from harness.loop import run
from harness.models import EventType, canonical_json
from harness.store import Store

ROOT = Path(__file__).resolve().parent.parent / ".live-demo"
REPO = ROOT / "repo"
DB = ROOT / "state" / "harness.db"

CONSOLE = Console()

SCENARIOS = {
    "bug": {
        "task": "运行 python check.py 看它为什么失败，修复 calc.py 让检查通过，然后 finish。",
        "files": {
            "calc.py": "def average(values):\n    return sum(values) / (len(values) - 1)\n",
            "check.py": (
                "from calc import average\n\n"
                "assert average([2, 4]) == 3\n"
                "assert average([1, 2, 3]) == 2\n"
                'print("OK")\n'
            ),
        },
    },
    "feature": {
        "task": "check.py 引用了 calc.py 里还没有的 median。补上 median（排序后取中位数）并让 python check.py 通过，然后 finish。",
        "files": {
            "calc.py": (
                "def average(values):\n"
                "    return sum(values) / len(values)\n"
            ),
            "check.py": (
                "from calc import average, median\n\n"
                "assert average([2, 4]) == 3\n"
                "assert median([3, 1, 2]) == 2\n"
                "assert median([4, 1, 3, 2]) == 2.5\n"
                'print("OK")\n'
            ),
        },
    },
    "edge": {
        "task": "average([]) 会抛 ZeroDivisionError。改成空列表返回 0，并在 check.py 里加一条 average([]) == 0 的断言，最后跑 python check.py 确认。",
        "files": {
            "calc.py": "def average(values):\n    return sum(values) / len(values)\n",
            "check.py": (
                "from calc import average\n\n"
                "assert average([2, 4]) == 3\n"
                'print("OK")\n'
            ),
        },
    },
}


def build_repo(scenario: dict) -> None:
    """只重建仓库；state/ 里的历史会话保留，便于多次运行后对比 trace。"""
    shutil.rmtree(REPO, ignore_errors=True)
    REPO.mkdir(parents=True)
    for name, content in scenario["files"].items():
        (REPO / name).write_text(content, encoding="utf-8")


def approver(call, ruling):
    """demo 专用：打印后放行。生产里应该看 diff 再决定。"""
    CONSOLE.print(f"[yellow]审批[/] {call.name} ({ruling.rule}) {canonical_json(call.args)[:180]}")
    return "allow"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="bug")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("HARNESS_API_KEY")
    if not api_key:
        sys.exit("请先设置 DEEPSEEK_API_KEY（或 HARNESS_API_KEY）再运行本脚本")

    scenario = SCENARIOS[args.scenario]
    build_repo(scenario)
    cfg = Settings().with_workspace(REPO)
    cfg.state_dir = ROOT / "state"
    cfg.model.name = os.environ.get("HARNESS_MODEL", "deepseek-chat")
    cfg.model.api_base = os.environ.get("HARNESS_API_BASE", "https://api.deepseek.com")
    cfg.model.api_key_env = "DEEPSEEK_API_KEY"
    cfg.run.max_steps = int(os.environ.get("HARNESS_MAX_STEPS", "14"))

    store = Store(DB)
    session_id = store.create_session(scenario["task"], str(REPO), cfg.model.name, base_commit="")
    store.append(
        session_id,
        EventType.SESSION_START,
        {
            "task": scenario["task"],
            "repo": str(REPO),
            "base_commit": "",
            "model": cfg.model.name,
            "config_hash": cfg.config_hash(),
        },
    )
    store.append(session_id, EventType.USER_MESSAGE, {"text": scenario["task"]})

    CONSOLE.print(
        f"[bold]live session[/] {session_id}  scenario={args.scenario}  "
        f"model={cfg.model.name}  base={cfg.model.api_base}"
    )
    status = run(session_id, store, cfg, approver=approver, audit=make_audit(cfg))

    table = Table(title=f"h trace {session_id}  (status={status})")
    table.add_column("seq", justify="right")
    table.add_column("type")
    table.add_column("summary", overflow="fold")
    for event in store.events(session_id):
        table.add_row(str(event.seq), str(event.type), Text(summarize(event)))
    CONSOLE.print(table)

    session = store.session(session_id)
    messages, _stats = build_context(store.events(session_id), cfg)
    digest = hashlib.sha256(canonical_json(messages).encode()).hexdigest()
    CONSOLE.print(
        f"[bold]tokens[/] {session.tokens_total}  [bold]cost[/] ${session.cost:.6f}  "
        f"[bold]messages[/] {len(messages)}  [bold]replay[/] {digest[:16]}…"
    )
    for name in scenario["files"]:
        CONSOLE.print(f"[bold]{name}[/]")
        CONSOLE.print((REPO / name).read_text(encoding="utf-8").rstrip(), markup=False)
    CONSOLE.print(f"[dim]state: {ROOT}  db={store.path}[/]")


if __name__ == "__main__":
    main()
