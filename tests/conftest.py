"""测试环境适配。

本机沙箱下用 0o700 新建的目录对当前 token 不可读（pytest 内建 tmp_path 正是这样建的），
因此这里覆盖 tmp_path，用普通权限在工作区内建目录，避免整个 tmpdir 插件不可用。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "tmp-tests"


@pytest.fixture
def tmp_path(request):
    name = request.node.nodeid.replace("::", "_").replace("/", "_").replace("\\", "_")
    path = BASE / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
