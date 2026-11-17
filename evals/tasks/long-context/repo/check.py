from pathlib import Path

answer = Path("answer.txt").read_text(encoding="utf-8").strip()
assert answer == "42", f"answer.txt is {answer!r}, expected '42'"
print("OK")
