"""工具: logging 等统一入口.

`from newbee.utils import logger` 走模块级 proxy 实例 (`_LoggerProxy`),
实现见 `newbee.utils.logger`.

实现细节:
  - `from newbee.utils.logger import logger` 在 `__init__` 末尾会覆盖 import
    machinery 自动设的 `newbee.utils.logger` 子 module 引用;因此
    `newbee.utils.logger` (从 package attribute 读) 拿到的是 proxy 而非
    子 module 本身. 子 module 的访问入口改为 `sys.modules['newbee.utils.logger']`,
    业务代码无需此路径.
"""
from __future__ import annotations

from newbee.utils.logger import logger

__all__ = ["logger"]