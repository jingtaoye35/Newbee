"""工具: logging 等统一入口.

`from alpha_backend.utils import logger` 走模块级 proxy 实例 (`_LoggerProxy`),
实现见 `alpha_backend.utils.logger`.

实现细节:
  - `from alpha_backend.utils.logger import logger` 在 `__init__` 末尾会覆盖 import
    machinery 自动设的 `alpha_backend.utils.logger` 子 module 引用;因此
    `alpha_backend.utils.logger` (从 package attribute 读) 拿到的是 proxy 而非
    子 module 本身. 子 module 的访问入口改为 `sys.modules['alpha_backend.utils.logger']`,
    业务代码无需此路径.
"""
from __future__ import annotations

from alpha_backend.utils.logger import attach_file_log, logger

__all__ = ["logger", "attach_file_log"]