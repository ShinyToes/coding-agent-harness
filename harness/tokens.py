"""token 估算：优先 tiktoken（可选依赖），否则回退到 chars/ratio 启发式。

不把 tiktoken 设成硬依赖：spec 的依赖白名单很小，装不上时 harness 必须照常工作。
"""

from __future__ import annotations

import functools

_FALLBACK_RATIO = 3.0


@functools.lru_cache(maxsize=1)
def _encoder():
    """返回 tiktoken 编码器；未安装或加载失败时返回 None。"""
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - 可选依赖，任何失败都回退
        return None


def backend() -> str:
    return "tiktoken" if _encoder() is not None else "heuristic"


def count(text: str, ratio: float = _FALLBACK_RATIO) -> int:
    encoder = _encoder()
    if encoder is not None:
        try:
            return max(1, len(encoder.encode(text)))
        except Exception:  # noqa: BLE001 - 编码失败就回退
            pass
    divisor = ratio if ratio and ratio > 0 else _FALLBACK_RATIO
    return max(1, int(len(text) / divisor))
