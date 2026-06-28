"""统一日志入口: 模块级 `logger` proxy, 使用方零样板.

用法:
    from alpha_backend.utils import logger
    logger.info("hi")  # 自动归属调用方模块名, 走 logging 标准库

行为:
    - `logger` 是 `_LoggerProxy` 实例; 任何属性访问经 `__getattr__` 转发
    - 内部 `sys._getframe(1).f_globals["__name__"]` 取调用方模块名
    - 每次解析首次拿到具体 `logging.Logger` 时, 由 `_configure` 挂统一 StreamHandler + Formatter
    - 重复访问同一 name 不重复挂 handler (用 `_newbee_configured` 标记)
    - `LOG_FORMAT` 环境变量非空时覆盖默认格式

设计:
    - 不接管 root logger, 不调 `basicConfig`, 不修改 `logging.root`
    - 入口脚本继续用 `logging.basicConfig(level=INFO)` 控制 root 行为

文件日志:
    - `attach_file_log(path)` 给一组固定的模块 logger 挂 `FileHandler`,
      供 datasource update 之类的长任务把运行轨迹写到磁盘.
    - proxy 给每个模块 logger 设了 `propagate=False`,所以不能挂在 root
      上靠冒泡抓;helper 显式挂到 allowlist 中的几个 logger.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

__all__ = ["logger", "attach_file_log"]

# datasource update 会触发的 logger (proxy 设 propagate=False, 必须显式挂)
_FILE_LOG_TARGETS: tuple[str, ...] = (
    "alpha_backend.datasource.service.kdata",
    "alpha_backend.datasource.service.universe",
    "alpha_backend.datasource.sources.akshare",
    "alpha_backend.datasource.storage.io",
    "alpha_backend.datasource.storage.state",
)

# ---------- 配置 ----------

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
# 模块导入时读一次; 后续 process 内修改环境变量不再生效 (logging.Formatter 缓存).
_FORMAT: str = os.environ.get("LOG_FORMAT", DEFAULT_FORMAT)

# ---------- 内部 ----------

# 用 setattr 给 logger 打标记, 避免污染 logging.Logger 的 __slots__/attribute 命名
# (注: stdlib logging.Logger 没有 __slots__, 普通属性赋值安全; 但仍用 setattr 显式表态).
_MARK_ATTR = "_newbee_configured"


def _configure(logger: logging.Logger) -> None:
    """首次拿到某 logger 时挂统一 StreamHandler + Formatter. 幂等.

    Note:
        设 `propagate=False` 防止冒泡到 root (避免与入口脚本 `logging.basicConfig`
        配的 root handler 产生重复输出). proxy 管的 logger 完全自包含, 不影响
        其它走 `logging.getLogger` 的模块.
    """
    if getattr(logger, _MARK_ATTR, False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    # 标记位置: 用 setattr 避开任何潜在的 attribute 冲突
    try:
        setattr(logger, _MARK_ATTR, True)
    except (AttributeError, TypeError):
        # 防御: 极端情况下 logger 不允许 setattr, 退化到下一次再挂 (产生重复日志但不影响功能)
        pass


# ---------- Proxy ----------

# 黑名单: 这些属性不应被代理, 避免破坏 proxy 自身行为
_DENY_ATTRS = frozenset({"bind"})


class _LoggerProxy:
    """模块级 `logger` 的实现. `__getattr__` 转发到 `logging.getLogger(caller)`.

    Note:
        - 不实现 `__getattribute__`, 让 `isinstance` / `pprint` / IDE 提示等
          对 proxy 自身的属性查询走正常路径, 不会被劫持.
        - 每次属性访问都从 frame 取 caller, 不缓存, 保证跨模块复用同一 proxy 时归属正确.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        if name in _DENY_ATTRS:
            raise NotImplementedError(
                f"alpha_backend.utils.logger.{name} 留给后续 change, 当前未实现"
            )
        # 取调用方 frame[1] 的 __name__
        try:
            frame = sys._getframe(1)
        except ValueError:
            # 极少见: 解释器关闭阶段; 退化到不挂 handler 的根级 logger
            real = logging.getLogger("alpha_backend.utils.logger")
            return getattr(real, name)
        caller_name = frame.f_globals.get("__name__", "alpha_backend.utils.logger")
        real = logging.getLogger(caller_name)
        if not getattr(real, _MARK_ATTR, False):
            _configure(real)
        return getattr(real, name)


# ---------- 公共对象 ----------

logger = _LoggerProxy()


# ---------- 文件日志 helper ----------


def attach_file_log(path: Path) -> None:
    """给 `_FILE_LOG_TARGETS` 中每个模块 logger 挂一个写 `path` 的 `FileHandler`.

    设计要点:
    - proxy 给每个模块 logger 设了 `propagate=False`, 因此 FileHandler 必须
      显式挂在模块 logger 上, 不能挂在 root.
    - 父目录不存在时自动创建;创建失败 → 退化为 stderr warning,不抛错.
    - 同一进程对同一 `path` 重复调用不会重复挂 handler (按 `baseFilename` 去重).
    - `mode="w"`: 每次运行覆写;调用方通过时间戳化的文件名保证不覆盖.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # 不让日志路径问题挡住业务运行
        logging.getLogger("alpha_backend.utils.logger").warning(
            "file log disabled: mkdir %s failed: %s", path.parent, e
        )
        return

    try:
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    except OSError as e:
        logging.getLogger("alpha_backend.utils.logger").warning(
            "file log disabled: open %s failed: %s", path, e
        )
        return

    handler.setFormatter(logging.Formatter(_FORMAT))
    # FileHandler 自带 close() 在 atexit 时由 logging 走 shutdown 调用, 不用手动管.

    # 解析为绝对路径, 让幂等检查 (比较 h.baseFilename) 稳定
    resolved = path.resolve()
    for name in _FILE_LOG_TARGETS:
        lg = logging.getLogger(name)
        # 幂等: 同 path 的 FileHandler 已挂过就跳过
        already = any(
            isinstance(h, logging.FileHandler) and Path(h.baseFilename) == resolved
            for h in lg.handlers
        )
        if already:
            continue
        lg.addHandler(handler)
