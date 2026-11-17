"""失败分类：把一次评测结果映射成可统计的类别。

为什么要分类：`passed=false` 把「没写完」「写对了没停」「测试挂」「环境错」混在一起，
无法指导改进（B-038 就是靠人工看 trace 才发现的）。
"""

from __future__ import annotations


def classify(status: str, check_rc: int) -> tuple[bool, str]:
    """返回 (是否通过, 类别)。

    类别：
    - pass              模型完成且验收通过
    - solved_no_finish  验收通过，但会话没正常结束（典型：不调用 finish 被 max_steps 截断）
    - tests_fail        会话正常结束，但验收不通过
    - budget            步数耗尽且验收不通过
    - cost_budget       成本上限到达且验收不通过
    - cancelled         用户中断（Ctrl+C）
    - stopped           模型不再调用工具且验收不通过
    - error             运行异常（provider/沙箱等）
    """
    acceptance_ok = check_rc == 0
    normal_end = status == "done"
    if normal_end and acceptance_ok:
        return True, "pass"
    if acceptance_ok and not normal_end:
        return False, "solved_no_finish"
    if normal_end and not acceptance_ok:
        return False, "tests_fail"
    if status == "max_steps":
        return False, "budget"
    if status == "max_cost":
        return False, "cost_budget"
    if status == "cancelled":
        return False, "cancelled"
    if status == "no_tool_calls":
        return False, "stopped"
    if status.startswith("error"):
        return False, "error"
    return False, "unknown"
