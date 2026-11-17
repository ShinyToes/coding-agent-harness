"""离线演示：脚本化 LLM 跑一个完整 session，不调真模型、不联网。

    python examples/demo.py
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from harness.audit import NoopAudit
from harness.cli import summarize
from harness.config import Settings
from harness.context import build_context
from harness.loop import run
from harness.models import Completion, EventType, ToolCall, canonical_json
from harness.store import Store

ROOT = Path(__file__).resolve().parent.parent / ".demo"
WORKSPACE = ROOT / "repo"
DB = ROOT / "state" / "harness.db"
TASK = "给 parse_date 加上时区支持"

CONSOLE = Console()
APPROVALS: list[str] = []


class ScriptedLLM:
    """按脚本吐 tool call，用来演示循环/策略/审计，而不是演示模型能力。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)

    def complete(self, messages: list[dict], tools: list[dict]) -> Completion:
        step = self.script.pop(0)
        return Completion(
            text=step.get("text", ""),
            tool_calls=[ToolCall(call_id=cid, name=name, args=args) for cid, name, args in step["calls"]],
            usage={"prompt_tokens": 900, "completion_tokens": 40},
            model="scripted-llm",
            latency_ms=1,
        )


def approve(call, ruling) -> str:
    APPROVALS.append(f"{call.name} {ruling.rule}")
    return "allow"


PATCH = """<<<<<<< SEARCH
def parse_date(s):
    return s
=======
from datetime import datetime


def parse_date(s, tz=None):
    return datetime.fromisoformat(s, tzinfo=tz)
>>>>>>> REPLACE"""

SCRIPT = [
    {"calls": [("c1", "read", {"path": "src/date.py"})]},
    {"calls": [("c2", "patch", {"path": "src/date.py", "patch": PATCH})]},
    {"calls": [("c3", "bash", {"command": "python list_src.py"})]},
    {"calls": [("c4", "grep", {"pattern": "tzinfo", "path": "src"})]},
    {"text": "已加上时区支持", "calls": [("c5", "finish", {"summary": "parse_date 支持 tz 参数，已 grep 验证"})]},
]


def build_workspace() -> None:
    shutil.rmtree(ROOT, ignore_errors=True)
    (WORKSPACE / "src").mkdir(parents=True)
    (WORKSPACE / "src" / "date.py").write_text("def parse_date(s):\n    return s\n", encoding="utf-8")
    (WORKSPACE / "list_src.py").write_text(
        "import os\n\nprint(sorted(os.listdir('src')))\n", encoding="utf-8"
    )


def main() -> None:
    build_workspace()
    store = Store(DB)
    cfg = Settings().with_workspace(WORKSPACE)
    cfg.state_dir = ROOT / "state"

    session_id = store.create_session(TASK, str(WORKSPACE), "scripted-llm")
    store.append(
        session_id,
        EventType.SESSION_START,
        {"task": TASK, "repo": str(WORKSPACE), "base_commit": "", "model": "scripted-llm", "config_hash": cfg.config_hash()},
    )
    store.append(session_id, EventType.USER_MESSAGE, {"text": TASK})

    status = run(session_id, store, cfg, llm=ScriptedLLM(SCRIPT), approver=approve, audit=NoopAudit())

    table = Table(title=f"h trace {session_id}  (status={status})")
    table.add_column("seq", justify="right")
    table.add_column("type")
    table.add_column("summary", overflow="fold")
    for event in store.events(session_id):
        table.add_row(str(event.seq), str(event.type), Text(summarize(event)))
    CONSOLE.print(table)

    messages, stats = build_context(store.events(session_id), cfg)
    digest = hashlib.sha256(canonical_json(messages).encode()).hexdigest()
    CONSOLE.print(f"[bold]approvals[/] {APPROVALS}")
    CONSOLE.print(f"[bold]replay[/] messages={len(messages)} est_tokens={stats.tokens} sha256={digest[:16]}…")
    CONSOLE.print(f"[bold]after[/] {WORKSPACE / 'src' / 'date.py'}")
    # markup=False：代码里的 `values[mid]` 不能被 rich 当成样式标签吃掉
    CONSOLE.print((WORKSPACE / "src" / "date.py").read_text(encoding="utf-8"), markup=False)
    CONSOLE.print(f"[dim]state: {ROOT}[/]")


if __name__ == "__main__":
    main()
