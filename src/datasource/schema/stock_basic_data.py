from __future__ import annotations

from typing import ClassVar
from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["StockBasicData"]


class StockBasicData(BaseModel):
    """股票基础数据 (累积复权因子 + 涨跌停价 + 申万行业, long format, float64 精度)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Stock_Basic_Data"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "daily"
    storage_path: ClassVar[str] = "Stock_Basic_Data.parquet"
    primary_key: ClassVar[Tuple[str, ...]] = ("trade_date", "stock_code")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    adapters: ClassVar[List[DataAdapter]] = [DataAdapter.Baostock]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    trade_date: str  # YYYY-MM-DD — 交易日.
    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    adj_factor: float | None  # ratio — 累积复权因子 (float64 精度, 防长 horizon 漂移).
    limit_upper_price: float | None  # 涨停价 (nullable).
    limit_lower_price: float | None  # 跌停价 (nullable).
    sw_industry: Optional[str]  # 申万一级行业.
    total_share: float | None  # 总股本 (流通股 + 非流通股).
    turnover: float | None  # 日换手率 (volume / total_share).
    is_activate: bool  # bool — True 当日正常交易 (非停牌、非 ST、非退市).

    _check_stock_code = field_validator("stock_code")(check_stock_code)
    _check_trade_date = field_validator("trade_date")(check_trade_date)
