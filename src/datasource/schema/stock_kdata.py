from __future__ import annotations

from typing import ClassVar
from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["StockKData"]


class StockKData(BaseModel):
    """股票日频 K 线"""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Stock_KData"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "daily"
    storage_path: ClassVar[str] = "Stock_KData.parquet"
    primary_key: ClassVar[tuple[str, ...]] = ("trade_date", "stock_code")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    # akshare (sina/em/tx) → baostock fallback.
    adapters: ClassVar[list[DataAdapter]] = [DataAdapter.Akshare, DataAdapter.Baostock]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    trade_date: str         # YYYY-MM-DD — 交易日 (ISO string, 10 chars).
    stock_code: str         # 9-char .SH/.SZ — 9 字符股票代码, 形如 "600000.SH" / "000012.SZ".
    open: float | None      # 开盘价 (nullable, 停牌/未上市/退市为 NaN).
    high: float | None      # 最高价 (nullable).
    low: float | None       # 最低价 (nullable).
    close: float | None     # 收盘价 (nullable).
    amount: float | None    # 成交额 (nullable, 停牌/未上市/退市为 NaN, float64 — 高精度以支撑大额成交累积).
    volume: float | None    # 成交量 (nullable, 停牌/未上市/退市为 NaN, float64 — 避免长 horizon 累积溢出).

    _check_trade_date = field_validator("trade_date")(check_trade_date)
    _check_stock_code = field_validator("stock_code")(check_stock_code)

