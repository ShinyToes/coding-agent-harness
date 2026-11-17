"""read_artifact：把上下文里被截断的工具输出按需取回。

工具输出全量存在 artifacts 表，消息里只有头尾摘要；没有这个工具，模型看到
`artifact=xxxx` 却拿不到全文，等于把信息截断成了信息丢失。
"""

from __future__ import annotations

from harness.models import ToolResult
from harness.tools.registry import ToolCtx, register

MAX_CHUNK = 8_000


class ReadArtifactTool:
    name = "read_artifact"
    effect = "read"
    description = "按 artifact id 读取被截断的工具输出全文，支持 offset/limit 分块。"
    parameters = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "description": "tool.result 里给出的 artifact id"},
            "offset": {"type": "integer", "description": "字符偏移，默认 0"},
            "limit": {"type": "integer", "description": "本次最多返回多少字符，默认 4000，上限 8000"},
        },
        "required": ["artifact_id"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return ToolResult(ok=False, output="artifact_id is required")
        try:
            meta = ctx.store.artifact_meta(artifact_id)
            raw = ctx.store.get_artifact(artifact_id)
        except (KeyError, FileNotFoundError) as exc:
            return ToolResult(ok=False, output=f"artifact unavailable: {artifact_id} ({exc})")

        text = raw.decode("utf-8", errors="replace")
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, min(int(args.get("limit") or 4_000), MAX_CHUNK))
        chunk = text[offset : offset + limit]
        remaining = max(0, len(text) - offset - len(chunk))
        header = f"# artifact {artifact_id} kind={meta.get('kind')} bytes={meta.get('bytes')} offset={offset}"
        if remaining:
            header += f" remaining={remaining} (next offset={offset + len(chunk)})"
        return ToolResult(
            ok=True,
            output=f"{header}\n{chunk}",
            meta={
                "artifact_id": artifact_id,
                "kind": str(meta.get("kind") or ""),
                "offset": offset,
                "chars": len(chunk),
                "remaining": remaining,
                "total_chars": len(text),
            },
        )


register(ReadArtifactTool())
