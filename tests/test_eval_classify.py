"""失败分类的纯函数测试（evals 不是包，用文件加载）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "evals_classify", Path(__file__).resolve().parents[1] / "evals" / "classify.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
classify = MODULE.classify


@pytest.mark.parametrize(
    ("status", "rc", "passed", "category"),
    [
        ("done", 0, True, "pass"),
        ("max_steps", 0, False, "solved_no_finish"),
        ("done", 1, False, "tests_fail"),
        ("max_steps", 1, False, "budget"),
        ("max_cost", 1, False, "cost_budget"),
        ("cancelled", 1, False, "cancelled"),
        ("no_tool_calls", 1, False, "stopped"),
        ("error:RuntimeError", 1, False, "error"),
        ("weird", 1, False, "unknown"),
    ],
)
def test_classify(status, rc, passed, category):
    assert classify(status, rc) == (passed, category)


def test_solved_no_finish_is_not_a_pass():
    """B-038 的核心：验收过了但没 finish，不能算通过。"""
    assert classify("max_steps", 0) == (False, "solved_no_finish")
