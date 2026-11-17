"""bash 工具：所有命令走 sandbox，不直接 subprocess。"""

from __future__ import annotations

from harness.models import ToolResult
from harness.tools.registry import ToolCtx, register

MAX_TIMEOUT = 600


class BashTool:
    name = "bash"
    effect = "exec"
    description = "在 workspace 里执行 shell 命令（默认 timeout 120s）。"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "秒，最大 600"},
        },
        "required": ["command"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult(ok=False, output="empty command")
        timeout = min(int(args.get("timeout") or ctx.cfg.sandbox.timeout), MAX_TIMEOUT)
        result = ctx.sandbox.exec(command, str(ctx.workspace), timeout)
        output = result.stdout
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        if result.timed_out:
            output = f"[timed out after {timeout}s]\n{output}"
        if not output.strip():
            output = f"(no output, rc={result.rc})"
        return ToolResult(
            ok=result.rc == 0 and not result.timed_out,
            output=output,
            meta={
                "rc": result.rc,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
            },
        )


register(BashTool())
