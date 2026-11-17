"""token 估算：精确后端（tiktoken，可选）与启发式回退。"""

from __future__ import annotations

import contextlib

from harness import tokens


@contextlib.contextmanager
def _patched(monkeypatch, encoder):
    original = tokens._encoder
    original.cache_clear()
    try:
        monkeypatch.setattr(tokens, "_encoder", lambda: encoder)
        yield
    finally:
        monkeypatch.undo()
        original.cache_clear()


def test_heuristic_fallback_uses_ratio(monkeypatch):
    with _patched(monkeypatch, None):
        assert tokens.backend() == "heuristic"
        assert tokens.count("abcdef") == 2  # 6 / 3
        assert tokens.count("abcdef", ratio=2.0) == 3
        assert tokens.count("") == 1
        assert tokens.count("abcdef", ratio=0) == 2  # 非法 ratio 回退默认 3.0


class _FakeEncoder:
    def encode(self, text: str) -> list[int]:
        return [0] * (len(text.split()) + 1)


def test_tiktoken_backend_counts_exactly(monkeypatch):
    with _patched(monkeypatch, _FakeEncoder()):
        assert tokens.backend() == "tiktoken"
        assert tokens.count("one two three") == 4
        assert tokens.count("anything", ratio=99) == 2  # encoder 优先，ratio 不参与


def test_encoder_failure_falls_back(monkeypatch):
    class Broken:
        def encode(self, text: str) -> list[int]:
            raise RuntimeError("boom")

    with _patched(monkeypatch, Broken()):
        assert tokens.count("abcdef") == 2
