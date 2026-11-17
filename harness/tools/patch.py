"""patch 工具：支持 SEARCH/REPLACE 块与 unified diff。

两种格式都先完整校验再落盘，避免写一半。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from harness.models import ToolResult
from harness.tools.fs import resolve_path
from harness.tools.registry import ToolCtx, register

SEARCH_BLOCK = re.compile(r"<{7} SEARCH\n(.*?)\n?={7}\n(.*?)\n?>{7} REPLACE", re.S)
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class PatchOutcome:
    ok: bool
    text: str = ""
    error: str = ""
    added: int = 0
    removed: int = 0


def parse_search_blocks(patch_text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in SEARCH_BLOCK.finditer(patch_text)]


def apply_search_blocks(source: str, blocks: list[tuple[str, str]]) -> PatchOutcome:
    text = source
    added = removed = 0
    for old, new in blocks:
        if not old:
            return PatchOutcome(False, error="empty SEARCH block (insertion not supported)")
        if old not in text:
            head = old.strip().splitlines()[0] if old.strip() else ""
            return PatchOutcome(False, error=f"SEARCH block not found: {head!r}")
        if text.count(old) > 1:
            # 多义匹配会改错位置，宁可失败让模型补充上下文
            return PatchOutcome(False, error="SEARCH block is ambiguous (matches multiple places)")
        text = text.replace(old, new, 1)
        removed += len(old.splitlines())
        added += len(new.splitlines())
    return PatchOutcome(True, text=text, added=added, removed=removed)


def parse_hunks(diff_text: str) -> list[dict]:
    hunks: list[dict] = []
    current: dict | None = None
    for line in diff_text.splitlines():
        header = HUNK_HEADER.match(line)
        if header:
            current = {"old_start": int(header.group(1)), "lines": []}
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith(("---", "+++", "diff ", "Index: ")):
            continue
        if line.startswith("\\"):
            continue
        if line == "":
            current["lines"].append((" ", ""))
            continue
        current["lines"].append((line[0], line[1:]))
    return hunks


def apply_unified(source: str, diff_text: str) -> PatchOutcome:
    hunks = parse_hunks(diff_text)
    if not hunks:
        return PatchOutcome(False, error="no unified diff hunks (@@ ... @@) found")
    src = source.splitlines()
    out: list[str] = []
    pos = 0
    added = removed = 0
    for hunk in hunks:
        start = hunk["old_start"] - 1
        if start < pos or start > len(src):
            return PatchOutcome(False, error=f"hunk out of order or range at line {hunk['old_start']}")
        out.extend(src[pos:start])
        pos = start
        for tag, text in hunk["lines"]:
            if tag == " ":
                if pos >= len(src) or src[pos].rstrip() != text.rstrip():
                    got = src[pos] if pos < len(src) else "<eof>"
                    return PatchOutcome(False, error=f"context mismatch at line {pos + 1}: expected {text!r} got {got!r}")
                out.append(src[pos])
                pos += 1
            elif tag == "-":
                if pos >= len(src) or src[pos].rstrip() != text.rstrip():
                    got = src[pos] if pos < len(src) else "<eof>"
                    return PatchOutcome(False, error=f"remove mismatch at line {pos + 1}: expected {text!r} got {got!r}")
                pos += 1
                removed += 1
            elif tag == "+":
                out.append(text)
                added += 1
    out.extend(src[pos:])
    body = "\n".join(out)
    if source.endswith("\n") or not source:
        body += "\n" if body and not body.endswith("\n") else ""
    return PatchOutcome(True, text=body, added=added, removed=removed)


def apply_patch(source: str, patch_text: str) -> PatchOutcome:
    if "<<<<<<< SEARCH" in patch_text:
        blocks = parse_search_blocks(patch_text)
        if not blocks:
            return PatchOutcome(False, error="malformed SEARCH/REPLACE block")
        return apply_search_blocks(source, blocks)
    return apply_unified(source, patch_text)


class PatchTool:
    name = "patch"
    effect = "write"
    description = "按 SEARCH/REPLACE 块或 unified diff 修改文件。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "patch": {"type": "string", "description": "SEARCH/REPLACE 块或 @@ unified diff"},
        },
        "required": ["path", "patch"],
    }

    def run(self, args: dict, ctx: ToolCtx) -> ToolResult:
        path = resolve_path(ctx, str(args["path"]))
        patch_text = str(args.get("patch", ""))
        if not patch_text.strip():
            return ToolResult(ok=False, output="empty patch")
        source = path.read_text(encoding="utf-8") if path.exists() else ""
        outcome = apply_patch(source, patch_text)
        if not outcome.ok:
            return ToolResult(ok=False, output=f"patch failed: {outcome.error}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(outcome.text, encoding="utf-8")
        return ToolResult(
            ok=True,
            output=f"patched {path} (+{outcome.added}/-{outcome.removed})",
            meta={"path": str(path), "added": outcome.added, "removed": outcome.removed},
        )


register(PatchTool())
