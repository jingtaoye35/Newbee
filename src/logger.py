"""项目统一 logger (基于 loguru).

Public API:
- ``logger``: loguru 的 logger 代理 (懒装默认 stderr sink, 保留旧调用方式 ``logger.info(...)``)
- ``attach_file_log(path)``: 给 datasource update 链路追加写文件 sink, 仍走 loguru ``add(filter=...)``
- ``get_log_path()``: 从 config 取 log 目录, 否则 ``./logs``

为什么用代理而不是直接 ``from loguru import logger``: 让第一次 log 触发时再去 ``add(sys.stderr, ...)``,
``level`` 字段从 config 读 (而不是 import 时固定, 因为 CLI 是先 import logger 再 ``load_config``).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger as _loguru_logger

__all__ = ["logger", "attach_file_log", "get_log_path"]

_DEFAULT_FORMAT = "[{time:YYYY-MM-DD HH:mm:ss.SSS}][{level}][{name}:{function}:{line}]: {message}"
_FORMAT = os.environ.get("LOG_FORMAT", _DEFAULT_FORMAT)

# ``attach_file_log`` 追加文件 sink 时, 只记录这些模块 (datasource update 链路).
# loguru 的 ``record["name"]`` 即调用方模块的 ``__name__``.
_FILE_LOG_TARGETS: Tuple[str, ...] = (
    "datasource.cli",
    "datasource.service.stock_kdata",
    "datasource.service.trade_date",
    "datasource.service.universe",
    "datasource.service.stock_basic_data",
)

# 默认 stderr sink 的 handler id (lazy init, 保留 ``_get_log_level`` 的语义).
_stderr_sink_id: int | None = None
# 已追加的文件 sink: {resolved_path: handler_id}, attach_file_log 用此保持幂等.
_file_sinks: dict[str, int] = {}


def _get_log_level() -> str:
    """从 config 读取 log_level, 未加载时降级到 INFO."""
    try:
        from config import get_config

        return get_config().runtime.log_level
    except Exception:
        return "INFO"


def get_log_path() -> str:
    """从 config 读取 log_path, 未加载时降级到 ``./logs``."""
    try:
        from config import get_config

        return get_config().runtime.log_path
    except Exception:
        return "./logs"


def _ensure_stderr_sink() -> None:
    """Lazy 安装默认 stderr sink, level 读 config (幂等)."""
    global _stderr_sink_id
    if _stderr_sink_id is not None:
        return
    # loguru 默认会装一个 stderr sink (无自定义 format), 由我们替换.
    _loguru_logger.remove()
    _stderr_sink_id = _loguru_logger.add(
        sys.stderr, format=_FORMAT, level=_get_log_level()
    )


def attach_file_log(path: str | Path | None = None) -> None:
    """给 datasource update 链路的固定模块追加写文件 sink (幂等).

    - 不传 ``path`` 时, 从 ``config.runtime.log_path`` 获取目录并生成带时间戳的文件名;
    - 自动创建父目录;
    - 对同一 ``path`` 幂等 (不重复挂 sink);
    - 目录 / 文件不可写时记 warning 并返回, 不抛异常.
    """
    if path is None:
        path = Path(get_log_path()) / f"newbee-{datetime.now():%Y%m%d-%H%M%S}.log"
    else:
        path = Path(path)

    resolved = str(path)

    _ensure_stderr_sink()  # 顺便确保 default sink 已装 (level 已读)

    if resolved in _file_sinks:
        return

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _loguru_logger.warning("attach_file_log: 无法创建目录 {}: {}", path.parent, exc)
        return

    def _filter(record) -> bool:
        return record["name"] in _FILE_LOG_TARGETS

    try:
        handler_id = _loguru_logger.add(
            resolved,
            format=_FORMAT,
            filter=_filter,
            encoding="utf-8",
            level="DEBUG",
        )
    except OSError as exc:
        _loguru_logger.warning("attach_file_log: 无法打开文件 {}: {}", path, exc)
        return
    _file_sinks[resolved] = handler_id


class _LoggerProxy:
    """薄代理, 在第一次访问属性时 lazy 装默认 stderr sink.

    ``logger.info(...)`` / ``logger.warning(...)`` 等用法保持不变; ``logger.bind(...)`` /
    ``logger.add(...)`` 也直接转发给 loguru.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        _ensure_stderr_sink()
        return getattr(_loguru_logger, name)


logger = _LoggerProxy()
