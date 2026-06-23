"""pytest 共用 fixtures + 全局 hook.

放在 tests/ 根下 → 任何 test_*.py 自动加载, 无需在测试里显式 import.
"""
from __future__ import annotations


def pytest_configure(config):  # noqa: D401
    """session 启动时一次性: 解决 pandas 3 + pyarrow 24 的 ArrowKeyError.

    pandas.core.arrays.arrow.extension_types 在 `pd.read_parquet` 时会 lazy import
    并调用 `pyarrow.register_extension_type(_period_type)`. 如果该类型已被注册 (例如
    上一条测试触发了 import), 第二次 register 会抛 ArrowKeyError.
    这里在 session 开始时先 import pandas + pyarrow 让 pandas 完成注册, 之后
    read_parquet 触发 lazy import 时 try/except 会吞掉重复键.
    """
    import pyarrow as pa
    try:
        from pandas.core.arrays.arrow.extension_types import _period_type  # type: ignore
    except Exception:  # noqa: BLE001
        return
    try:
        pa.register_extension_type(_period_type)
    except pa.lib.ArrowKeyError:
        # already registered by another import path — that's fine, the lazy re-registration
        # in pandas' parquet module also catches the duplicate.
        pass
