from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["DividendHistory"]


class DividendHistory(BaseModel):
    """历史分红送转记录 (long format, primary_key = stock_code + ex_date; 由 stock_history_dividend 全市场一次性拉取)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Dividend_History"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "static"
    storage_path: ClassVar[str] = "Dividend_History.parquet"
    primary_key: ClassVar[Tuple[str, ...]] = ("stock_code", "ex_date")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    adapters: ClassVar[List[DataAdapter]] = [DataAdapter.Akshare]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    ex_date: str  # 除权除息日 (ISO) - YYYY-MM-DD.
    record_date: Optional[str]  # 股权登记日 (nullable) - YYYY-MM-DD.
    pay_date: Optional[str]  # 派息日 (nullable) - YYYY-MM-DD.
    report_date: Optional[str]  # 报告年度 (对应年度财报) - YYYY-MM-DD.
    dividend_per_share: float | None  # 每股派息(税前).
    share_bonus_per_share: float | None  # 每股送股 - shares.
    share_dividend_per_share: float | None  # 每股转增 - shares.
    update_time: Optional[str]  # 接口数据时间戳 (诊断用) - ISO 8601.

    _check_stock_code = field_validator("stock_code")(check_stock_code)
    _check_ex_date = field_validator("ex_date")(check_trade_date)
    _check_record_date = field_validator("record_date")(check_trade_date)
    _check_pay_date = field_validator("pay_date")(check_trade_date)
    _check_report_date = field_validator("report_date")(check_trade_date)
