"""把本地 harness 项目复制进评测工作区（跳过 venv / 缓存 / 评测自身）。"""

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
print("repo ready:", DEST)
