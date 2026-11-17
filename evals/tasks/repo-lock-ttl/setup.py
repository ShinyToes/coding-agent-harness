"""准备仓库：复制本地 harness 项目，并注入一个锁 TTL 回归 bug。"""

from __future__ import annotations

import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEST = Path.cwd()

IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".uv-cache", "dist", ".demo", ".live-demo",
    ".evals", ".phoenix", ".phoenix-venv", "deploy", "docs", "examples", "evals",
    "tmp-tests", "pytest-tmp", ".pytest_cache", ".ruff_cache", ".mypy_cache",
)

for name in ("harness", "tests", "pyproject.toml", "harness.toml"):
    source = PROJECT_ROOT / name
    target = DEST / name
    if not source.exists():
        raise SystemExit(f"missing project file: {source}")
    if source.is_dir():
        shutil.copytree(source, target, ignore=IGNORE, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)

# 注入 bug：把可配置的 ttl 写成常量 3600，导致过期锁永远不被回收
store_py = DEST / "harness" / "store.py"
text = store_py.read_text(encoding="utf-8")
old = "if age < ttl_seconds and row[\"owner\"] != owner:"
new = "if age < 3600 and row[\"owner\"] != owner:"
if text.count(old) != 1:
    raise SystemExit(f"injection anchor not found exactly once: {old!r}")
store_py.write_text(text.replace(old, new), encoding="utf-8")
print("repo ready with injected lock bug:", DEST)
