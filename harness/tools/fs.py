"""fs 工具：read（带缓存）/ write / list，以及写类调用的 diff 预览。"""

from __future__ import annotations

import difflib
from pathlib import Path

from harness.models import ToolResult
from harness.tools.registry import ToolCtx, register

MAX_LIST_ENTRIES = 300
SKIP_DIRS = {
    ".git", ".harness", ".venv", "venv", "__pycache__", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", "target",
}


def resolve_path(ctx: ToolCtx, raw: str) -> Path:
    """相对路径按 workspace 解析；绝对路径原样（越界由 policy 拦写）。"""
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (Path(ctx.workspace) / p)


def is_under(root: Path, path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def diff_text(label: str, before: str, after: str, limit: int = 4_000) -> str:
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            lineterm="",
            n=2,
        )
    )
    text = "\n".join(diff)
    if len(text) > limit:
        text = text[:limit] + f"\n... (diff truncated at {limit} chars)"
    return text or "(no change)"


def preview_write(path: Path, content: str) -> str:
    before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return diff_text(str(path), before, content)


def preview(call, ctx: ToolCtx) -> str:
    """审批前给人类看的 diff；只读工具返回空串。"""
    from harness.tools import patch as patch_tools

    path = resolve_path(ctx, str(call.args.get("path", "")))
    if call.name == "write":
        return preview_write(path, str(call.args.get("content", "")))
    if call.name == "patch":
        source = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        outcome = patch_tools.apply_patch(source, str(call.args.get("patch", "")))
        if not outcome.ok:
            return f"patch preview unavailable: {outcome.error}"
        return diff_text(str(path), source, outcome.text)
    return ""


class ReadTool:
    name = "read"
    effect = "read"
    description = "读取文件文本，可按 1-based 行号切片；未变化的重复读返回摘要。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对 workspace 或绝对）"},
            "start": {"type": "integer", "description": "起始行（1-based，含）"},
            "end": {"type": "integer", "description": "结束行（含）"},
        },
        "required": ["path"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        path = resolve_path(ctx, str(args["path"]))
        if not path.is_file():
            return ToolResult(ok=False, output=f"not a file: {path}", meta={"path": str(path)})
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            size = path.stat().st_size
            return ToolResult(ok=False, output=f"binary file ({size} bytes): {path}", meta={"binary": True})

        stat = path.stat()
        lines = text.splitlines()
        start = max(1, int(args.get("start") or 1))
        end = int(args.get("end") or len(lines))
        if end < start:
            return ToolResult(ok=False, output=f"bad range start={start} end={end}")
        selected = lines[start - 1 : end]
        body = "\n".join(selected)
        if start > 1 or end < len(lines):
            body = f"# lines {start}-{min(end, len(lines))} of {len(lines)}\n{body}"

        key = (str(path), stat.st_mtime_ns, stat.st_size, start, end)
        cache: dict = ctx.state.setdefault("read_cache", {})
        use_cache = getattr(ctx.cfg.run, "read_cache", True)
        hit = cache.get(key) if use_cache else None
        cache_meta = {
            "path": str(path),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "start": start,
            "end": end,
            "head": "\n".join(selected[:3]),
        }
        if hit is not None:
            return ToolResult(
                ok=True,
                output=f"[unchanged since seq {hit['seq']}]\n{hit['head']}\n... ({len(selected)} lines)",
                meta={
                    "cache_hit": True,
                    "lines": len(selected),
                    "total_lines": len(lines),
                    **cache_meta,
                },
            )
        last = ctx.store.last(ctx.session_id)
        seq = last.seq if last else 0
        if use_cache:
            cache[key] = {"seq": seq, "head": "\n".join(selected[:3])}
        return ToolResult(
            ok=True,
            output=body,
            meta={
                "cache_hit": False,
                "lines": len(selected),
                "total_lines": len(lines),
                "bytes": stat.st_size,
                **cache_meta,
            },
        )


class WriteTool:
    name = "write"
    effect = "write"
    description = "整文件覆盖写入（会自动创建父目录）。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        path = resolve_path(ctx, str(args["path"]))
        content = str(args.get("content", ""))
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        verb = "overwrote" if existed else "created"
        return ToolResult(
            ok=True,
            output=f"{verb} {path} ({len(content.encode())} bytes)",
            meta={"path": str(path), "bytes": len(content.encode()), "created": not existed},
        )


class ListTool:
    name = "list"
    effect = "read"
    description = "列目录树，限制深度与条数。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "默认 workspace 根", "default": "."},
            "depth": {"type": "integer", "description": "最大深度，默认 2", "default": 2},
            "limit": {"type": "integer", "description": "最多条目，默认 200", "default": 200},
        },
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        root = resolve_path(ctx, str(args.get("path") or "."))
        depth = max(1, min(int(args.get("depth") or 2), 6))
        limit = max(1, min(int(args.get("limit") or 200), MAX_LIST_ENTRIES))
        if not root.exists():
            return ToolResult(ok=False, output=f"no such path: {root}")
        if root.is_file():
            return ToolResult(ok=True, output=str(root))

        out: list[str] = []
        truncated = False
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and not truncated:
            current, level = stack.pop(0)
            try:
                entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError as exc:
                out.append(f"! cannot list {current}: {exc}")
                continue
            for entry in entries:
                rel = entry.relative_to(root).as_posix()
                if entry.is_dir():
                    if entry.name in SKIP_DIRS:
                        continue
                    out.append(rel + "/")
                    if level + 1 < depth:
                        stack.append((entry, level + 1))
                else:
                    out.append(rel)
                if len(out) >= limit:
                    truncated = True
                    break
        body = "\n".join(out) or "(empty)"
        if truncated:
            body += f"\n... (truncated at {limit} entries)"
        return ToolResult(ok=True, output=body, meta={"root": str(root), "entries": len(out), "truncated": truncated})


register(ReadTool())
register(WriteTool())
register(ListTool())
