"""日志：默认 WARNING，`HARNESS_LOG=DEBUG` 打开细节。

为什么需要：事件流是给 replay 用的审计记录，不是运维日志；进程级异常还得有 stderr 版本。
"""

from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger("harness")
_CONFIGURED = False


def setup() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("HARNESS_LOG", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _CONFIGURED = True


def log(level: int, message: str, *args) -> None:
    setup()
    LOGGER.log(level, message, *args)
