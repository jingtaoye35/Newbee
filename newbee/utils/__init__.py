"""工具: config 加载, logging.

注意: 子 module 命名为 `_logger` (下划线前缀), 不是 `logger`.
原因: Python `from package import name` 会强制把 `name` 当子 module 加载,
`__getattr__` (PEP 562) 在子 module 存在时会被覆盖; 只有子 module 改名才能让
`from newbee.utils import logger` 拿到 `_LoggerProxy` 而非子 module.
"""
from __future__ import annotations

from newbee.utils._logger import logger

__all__ = ["logger"]
