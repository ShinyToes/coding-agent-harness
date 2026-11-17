"""生成一个多模块仓库：24 个文件，只有 mod_17.py 里定义了 TARGET。"""

from pathlib import Path

src = Path("src")
src.mkdir(exist_ok=True)
for index in range(24):
    lines = [
        f'"""module {index}"""',
        "",
        f"NAME = 'mod_{index:02d}'",
        f"WEIGHT = {index * 3}",
        "",
        "def transform(values):",
        "    return [v * WEIGHT for v in values]",
        "",
        "def summarize(values):",
        "    total = sum(values)",
        "    return {'total': total, 'count': len(values)}",
        "",
    ]
    if index == 17:
        lines.append("TARGET = 42")
        lines.append("")
    (src / f"mod_{index:02d}.py").write_text("\n".join(lines), encoding="utf-8")
print("repo ready")
