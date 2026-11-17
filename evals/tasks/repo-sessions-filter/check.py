"""H1 验收：隐藏测试放进 tests/ 以复用项目 conftest 的 tmp_path 规避，然后跑相关测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

task_dir = Path(os.environ["EVAL_TASK_DIR"])
work = Path.cwd()
hidden = work / "tests" / "_hidden_test.py"
shutil.copyfile(task_dir / "hidden_test.py", hidden)
try:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/_hidden_test.py",
            "tests/test_cli.py",
            "tests/test_store.py",
        ],
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
