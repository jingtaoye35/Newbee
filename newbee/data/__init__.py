"""DEPRECATED 兼容层 — 旧 newbee.data.* 入口.

M2 起数据已迁至 newbee.datasource. 本包仅作为过渡期 compat shim:
- universe / sources.akshare / storage / fetch_state / incremental / pit / calendar
  仍保留原实现 (用于旧测试和 scripts)
- 新代码请改用 newbee.datasource.{registry,storage,sources,service,calendar}
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "newbee.data is deprecated; use newbee.datasource instead.",
    DeprecationWarning,
    stacklevel=2,
)

# re-export datasource APIs as legacy names
from newbee.datasource import registry as _registry  # noqa: F401
from newbee.datasource.calendar import (  # noqa: F401
    align_to_trading_day,
    is_trading_day,
    latest_trading_day,
    next_trading_day,
    prev_trading_day,
    sessions_between,
)
from newbee.datasource.storage.io import DataFile, CoverageStats  # noqa: F401
from newbee.datasource.storage.state import StateTracker, DataTypeState  # noqa: F401
from newbee.datasource.registry import REGISTRY, DataType, DataRegistry  # noqa: F401

# legacy modules — re-exported for compat
from newbee.data import (  # noqa: F401
    fetch_state as _fetch_state,
    incremental as _incremental,
    pit as _pit,
)
from newbee.data.universe import StockPool  # noqa: F401
from newbee.data.storage import (  # noqa: F401
    Bars,
    DEFAULT_DATA_ROOT,
    infer_first_date_global,
    infer_last_date_global,
    load_bars_from_parquet,
)


__all__ = [
    "Bars",
    "CoverageStats",
    "DEFAULT_DATA_ROOT",
    "DataFile",
    "DataRegistry",
    "DataType",
    "DataTypeState",
    "REGISTRY",
    "StateTracker",
    "StockPool",
    "_fetch_state",
    "_incremental",
    "_pit",
    "align_to_trading_day",
    "infer_first_date_global",
    "infer_last_date_global",
    "is_trading_day",
    "latest_trading_day",
    "load_bars_from_parquet",
    "next_trading_day",
    "prev_trading_day",
    "sessions_between",
]