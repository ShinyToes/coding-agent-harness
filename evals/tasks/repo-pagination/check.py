"""SYN1 验收：隐藏契约测试 + 仓库原有测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

task_dir = Path(os.environ["EVAL_TASK_DIR"])
work = Path.cwd()
hidden = work / "_hidden_test.py"
shutil.copyfile(task_dir / "hidden_test.py", hidden)
try:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "_hidden_test.py", "tests"],
        cwd=work,
        capture_output=True,
        text=True,
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(work)},
    )
finally:
    hidden.unlink(missing_ok=True)

print(proc.stdout[-3000:])
print(proc.stderr[-1500:])
raise SystemExit(proc.returncode)
