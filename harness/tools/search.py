"""search 工具：grep / glob / repo_map（缓存，≤800 tokens）。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from harness.models import ToolResult
from harness.tools.fs import SKIP_DIRS, is_under, resolve_path
from harness.tools.registry import ToolCtx, register

MAX_RESULTS = 200
MAX_FILE_BYTES = 1_000_000
CODE_SUFFIX = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".scala",
    ".sql", ".sh", ".ps1", ".toml", ".yaml", ".yml", ".json", ".md",
}
SYMBOL = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|const|let|struct|enum|func|impl|trait)\s+([A-Za-z_$][\w$]*)",
    re.M,
)


def walk_files(root: Path, limit: int = 4_000):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name
            count += 1
            if count >= limit:
                return


def build_repo_map(root: Path, max_tokens: int = 800) -> str:
    budget = max_tokens * 3  # len(s)//3 的 token 估算
    lines: list[str] = []
    used = 0
    for path in walk_files(Path(root)):
        if path.suffix not in CODE_SUFFIX:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # 二进制/无权限文件不进 repo map，不影响正确性
        names = SYMBOL.findall(text)[:10]
        if not names:
            continue
        rel = path.relative_to(root).as_posix()
        line = f"{rel}: {', '.join(names)}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


class GrepTool:
    name = "grep"
    effect = "read"
    description = "正则搜索文件内容，返回 file:line: text，上限 200 条。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "glob": {"type": "string", "description": "可选文件名过滤，如 *.py"},
            "max_results": {"type": "integer", "default": 200},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        base = resolve_path(ctx, str(args.get("path") or "."))
        try:
            rx = re.compile(str(args["pattern"]))
        except re.error as exc:
            return ToolResult(ok=False, output=f"bad regex: {exc}")
        limit = min(int(args.get("max_results") or MAX_RESULTS), MAX_RESULTS)
        glob_filter = str(args.get("glob") or "").strip()
        files = [base] if base.is_file() else walk_files(base)
        hits: list[str] = []
        scanned = 0
        for path in files:
            if path.suffix and glob_filter and not path.match(glob_filter):
                continue
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            for lineno, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    label = path.relative_to(base).as_posix() if base.is_dir() else path.name
                    hits.append(f"{label}:{lineno}: {line.strip()[:200]}")
                    if len(hits) >= limit:
                        break
            if len(hits) >= limit:
                break
        if not hits:
            return ToolResult(ok=True, output="(no matches)", meta={"matches": 0, "files_scanned": scanned})
        body = "\n".join(hits)
        if len(hits) >= limit:
            body += f"\n... (truncated at {limit} matches)"
        return ToolResult(ok=True, output=body, meta={"matches": len(hits), "files_scanned": scanned})


class GlobTool:
    name = "glob"
    effect = "read"
    description = "按路径模式匹配文件，如 **/*.py。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        base = resolve_path(ctx, str(args.get("path") or "."))
        pattern = str(args["pattern"])
        matches: list[str] = []
        for path in sorted(base.glob(pattern)):
            if path.is_file() and is_under(ctx.workspace, path):
                matches.append(path.relative_to(base).as_posix())
            if len(matches) >= MAX_RESULTS:
                break
        if not matches:
            return ToolResult(ok=True, output="(no matches)", meta={"matches": 0})
        body = "\n".join(matches)
        if len(matches) >= MAX_RESULTS:
            body += f"\n... (truncated at {MAX_RESULTS} matches)"
        return ToolResult(ok=True, output=body, meta={"matches": len(matches)})


class RepoMapTool:
    name = "repo_map"
    effect = "read"
    description = "仓库符号概览（路径: 顶部符号），用于低成本建立全局视角。"
    parameters = {
        "type": "object",
        "properties": {"max_tokens": {"type": "integer", "default": 800}},
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        max_tokens = max(100, min(int(args.get("max_tokens") or 800), 2_000))
        cache: dict = ctx.state.setdefault("repo_map", {})
        text = cache.get(max_tokens)
        if text is None:
            text = build_repo_map(Path(ctx.workspace), max_tokens)
            cache[max_tokens] = text
        if not text:
            return ToolResult(ok=True, output="(no symbols found)", meta={"cache_hit": False})
        return ToolResult(ok=True, output=text, meta={"cache_hit": max_tokens in cache, "lines": text.count("\n") + 1})


register(GrepTool())
register(GlobTool())
register(RepoMapTool())
