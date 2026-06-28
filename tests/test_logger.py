"""`alpha_backend.utils.logger` 模块级 proxy 测试.

覆盖 spec/custom-logger 的全部 Scenario:
  - Requirement 1: 调用方模块名归属 / 跨模块复用 / 透传方法
  - Requirement 2: 首次挂 handler / 重复不重复挂
  - Requirement 3: 默认格式 / LOG_FORMAT 覆盖
  - Requirement 4: root logger 不被接管
  - Requirement 5: __all__ / 顶层导出

注意: `logger.info(...)` 的真实 caller 模块是 `tests.test_logger` (本文件),
不是测试里硬编码的 "test_logger_mod_a" 等. proxy 通过 `sys._getframe(1)`
拿的是"调用 .info 的代码所在模块", 即 import logger 的那个模块.
"""
from __future__ import annotations

import importlib
import logging
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))


# ---------- helpers ----------

_CALLER_NAME = "tests.test_logger"  # 本测试文件 __name__

_DEFAULT_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def _reset_logger(name: str) -> None:
    """清掉某 logger 上由 proxy 注入的 handler + 配置标记."""
    lg = logging.getLogger(name)
    lg.handlers = [h for h in lg.handlers if not _is_proxy_handler(h)]
    if hasattr(lg, "_newbee_configured"):
        try:
            delattr(lg, "_newbee_configured")
        except AttributeError:
            pass


def _is_proxy_handler(h: logging.Handler) -> bool:
    """识别 proxy 注入的 handler (formatter 走 _FORMAT, 默认即 _DEFAULT_FMT)."""
    fmt = h.formatter
    if fmt is None:
        return False
    return getattr(fmt, "_fmt", None) == _DEFAULT_FMT or getattr(h, "_newbee_proxy", False)


@pytest.fixture(autouse=True)
def _isolate_logger(caplog):
    """每个 case 前清掉可能被污染的测试用 logger + root 状态.

    Note:
        proxy 在子 logger 上设了 `propagate=False`, 避免与 root handler 重复
        输出. 但这会让 pytest caplog (挂在 root 上) 抓不到子 logger 的 logRecord.
        这里在 setUp 阶段把 caplog.handler 也直接挂到被测 logger 上, 让 caplog
        能 capture; teardown 时移除, 恢复 _configure 之前的状态.
    """
    names = (_CALLER_NAME, "alpha_backend.utils.logger")
    for n in names:
        _reset_logger(n)
    # 给被测 logger 直接挂 caplog.handler, 绕过 propagate=False
    target = logging.getLogger(_CALLER_NAME)
    target.addHandler(caplog.handler)
    yield
    target.removeHandler(caplog.handler)
    for n in names:
        _reset_logger(n)


# ---------- Requirement 1: 模块名归属 / 跨模块 / 透传 ----------


def test_caller_module_name_attribution(caplog):
    """本测试模块 (tests.test_logger) 调 logger.info, logRecord.name 应是 tests.test_logger."""
    from alpha_backend.utils import logger

    with caplog.at_level(logging.INFO, logger=_CALLER_NAME):
        logger.info("hello-caller-attr")

    assert len(caplog.records) == 1
    assert caplog.records[0].name == _CALLER_NAME
    assert caplog.records[0].getMessage() == "hello-caller-attr"


def test_cross_module_reuse_same_proxy(caplog):
    """同一 proxy 实例在两个 caller 函数 (各自 __globals__ 不同) 下应归属各自模块名.

    技巧: 用 `types.FunctionType` 动态造两个函数, 它们的 `__globals__` 互相独立,
    各自 `__name__` 不同, 分别调 `logger.info` 后看 logRecord.name.
    """
    from alpha_backend.utils import logger

    code = compile("logger.info('x')", "<fake>", "exec")
    fn_a = types.FunctionType(code, {"__name__": "fake_mod_a", "logger": logger})
    fn_b = types.FunctionType(code, {"__name__": "fake_mod_b", "logger": logger})

    # fake_mod_a / fake_mod_b 是动态 logger, caplog 默认只挂在 root, 这里给它们也挂上
    fa, fb = logging.getLogger("fake_mod_a"), logging.getLogger("fake_mod_b")
    fa.addHandler(caplog.handler)
    fb.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO):
            fn_a()
            fn_b()
    finally:
        fa.removeHandler(caplog.handler)
        fb.removeHandler(caplog.handler)

    names = {rec.name for rec in caplog.records}
    assert "fake_mod_a" in names
    assert "fake_mod_b" in names


def test_proxy_passes_through_all_logger_methods():
    """proxy 透传所有 logging.Logger 公共方法."""
    from alpha_backend.utils import logger

    for method in (
        "info",
        "warning",
        "error",
        "debug",
        "critical",
        "exception",
        "log",
        "setLevel",
        "isEnabledFor",
        "addHandler",
        "removeHandler",
    ):
        assert hasattr(logger, method), f"proxy 缺方法: {method}"
        bound = getattr(logger, method)
        assert callable(bound), f"{method} 不可调用"


def test_bind_raises_not_implemented():
    """`logger.bind` 显式 NotImplementedError (留给后续 change)."""
    from alpha_backend.utils import logger

    with pytest.raises(NotImplementedError, match="bind 留给后续 change"):
        _ = logger.bind  # noqa: B018


# ---------- Requirement 2: 首次挂 handler / 幂等 ----------


def test_first_access_attaches_handler():
    """proxy 第一次访问某 name (即本文件 _CALLER_NAME), 触发 _configure."""
    from alpha_backend.utils import logger

    # 触发
    _ = logger.info
    real = logging.getLogger(_CALLER_NAME)
    proxy_handlers = [h for h in real.handlers if _is_proxy_handler(h)]
    assert len(proxy_handlers) >= 1, (
        f"未挂 proxy handler, 实际 handlers: {real.handlers}"
    )


def test_repeated_access_does_not_duplicate_handler():
    """同一 logger 多次触发, StreamHandler 数量不变."""
    from alpha_backend.utils import logger

    for _ in range(5):
        _ = logger.info
    real = logging.getLogger(_CALLER_NAME)
    proxy_handlers = [h for h in real.handlers if _is_proxy_handler(h)]
    assert len(proxy_handlers) == 1, f"handler 数量 {len(proxy_handlers)} != 1"


# ---------- Requirement 3: 默认格式 / LOG_FORMAT 覆盖 ----------


def test_default_format(caplog):
    """默认格式: logRecord.name = 调用方模块名, message 原样保留."""
    from alpha_backend.utils import logger

    with caplog.at_level(logging.WARNING, logger=_CALLER_NAME):
        logger.warning("hi-default")

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.name == _CALLER_NAME
    assert rec.levelname == "WARNING"
    assert rec.getMessage() == "hi-default"


def test_log_format_override(monkeypatch):
    """LOG_FORMAT 环境变量覆盖 format, 走 _FORMAT 重读 (需 reload logger)."""
    monkeypatch.setenv("LOG_FORMAT", "OVERRIDE %(levelname)s %(message)s")
    # 重置 proxy 注入的 handler
    _reset_logger(_CALLER_NAME)

    # 卸载重载以让 _FORMAT 重新读
    for mod_name in ("alpha_backend.utils.logger", "alpha_backend.utils"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    try:
        from alpha_backend.utils import logger as fresh_logger  # noqa: F401
        # 触发首次配置
        _ = fresh_logger.info
        real = logging.getLogger(_CALLER_NAME)
        proxy_handlers = [h for h in real.handlers if h.formatter]
        assert any(
            h.formatter._fmt == "OVERRIDE %(levelname)s %(message)s"
            for h in proxy_handlers
        ), f"formatter 未被 LOG_FORMAT 覆盖: {[h.formatter._fmt for h in proxy_handlers]}"
    finally:
        # 清掉污染再 reload 恢复默认
        _reset_logger(_CALLER_NAME)
        for mod_name in ("alpha_backend.utils.logger", "alpha_backend.utils"):
            if mod_name in sys.modules:
                del sys.modules[mod_name]
        importlib.import_module("alpha_backend.utils.logger")
        importlib.import_module("alpha_backend.utils")


# ---------- Requirement 4: 不接管 root ----------


def test_root_logger_not_modified():
    """调用 proxy 前后, logging.root 的 level/handlers 不变."""
    from alpha_backend.utils import logger

    root = logging.root
    level_before = root.level
    handlers_before = list(root.handlers)

    _ = logger.info
    _ = logger.warning

    assert root.level == level_before
    assert len(root.handlers) == len(handlers_before)


def test_legacy_logging_getlogger_path_unchanged():
    """其它模块继续 import logging; logger = logging.getLogger(__name__) 行为不变.

    验证: 直接用 logging.getLogger 拿一个未配过 handler 的 logger,
    触发 proxy 不应影响它, 也不应改 root level.
    """
    real = logging.getLogger("legacy_path_test_xyz")
    real.setLevel(logging.INFO)
    n_before = len(real.handlers)

    from alpha_backend.utils import logger

    _ = logger.info
    real_after = logging.getLogger("legacy_path_test_xyz")
    # root level 仍是默认 WARNING (没被改)
    assert logging.root.level == logging.WARNING
    # 那个 legacy logger 上的 handler 数量不变 (proxy 没动它)
    assert len(real_after.handlers) == n_before


# ---------- Requirement 5: 导入约束 ----------


def test_module_all_export():
    """`alpha_backend.utils.logger` 模块的 `__all__` 仅含 `logger`.

    Note:
        `import alpha_backend.utils.logger` 在 `__init__.py` 末尾会被 proxy binding
        覆盖,返回 `_LoggerProxy` 而非子 module;访问子 module 须经 `sys.modules`.
    """
    import sys

    mod = sys.modules["alpha_backend.utils.logger"]
    ns = {k: getattr(mod, k) for k in mod.__all__}
    assert set(ns.keys()) == {"logger"}


def test_top_level_logger_alias():
    """from alpha_backend.utils import logger 与 from alpha_backend.utils.logger import logger 是同一对象."""
    from alpha_backend.utils import logger as top_logger
    from alpha_backend.utils.logger import logger as direct_logger

    assert top_logger is direct_logger
