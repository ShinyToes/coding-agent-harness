from pathlib import Path

answer = Path("answer.txt").read_text(encoding="utf-8").strip()
assert answer == "42", f"answer.txt is {answer!r}, expected '42'"

search = Path("src")
values = [p.name for p in search.glob("*.py") if "TARGET" in p.read_text(encoding="utf-8")]
assert values == ["mod_17.py"], f"fixture changed: {values}"
print("OK")
