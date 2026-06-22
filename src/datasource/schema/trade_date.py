from __future__ import annotations

from typing import ClassVar, List, Tuple
from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_trade_date


__all__ = ["TradeDate"]


class TradeDate(BaseModel):
    """A 股交易日历"""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "TradeDate"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "daily"
    storage_path: ClassVar[str] = "Trade_Date.csv"
    primary_key: ClassVar[Tuple[str, ...]] = ("trade_date",)
    format: ClassVar[str] = "csv"

    # ---- 数据源适配器 ----
    # exchange_calendars (primary) → baostock (fallback via router).
    adapters: ClassVar[List[DataAdapter]] = [DataAdapter.ExchangeCalendar, DataAdapter.Baostock]

    model_config = ConfigDict(extra="forbid", frozen=False)

    trade_date: str  # YYYY-MM-DD — 实际交易日 (ISO YYYY-MM-DD), 例如 2024-01-02.

    _check_trade_date = field_validator("trade_date")(check_trade_date)
