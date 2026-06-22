from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["FinancialReportBalance"]


class FinancialReportBalance(BaseModel):
    """资产负债表 (long format, quarterly, primary_key = stock_code + report_date)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Financial_Report_Balance"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "quarterly"
    storage_path: ClassVar[str] = "Financial_Report_Balance.parquet"
    primary_key: ClassVar[Tuple[str, ...]] = ("stock_code", "report_date")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    adapters: ClassVar[List[DataAdapter]] = [DataAdapter.Akshare]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    report_date: str  # YYYY-MM-DD — 报告期截止日 (季末).
    total_assets: float | None  # CNY — 资产总计.
    total_current_assets: float | None  # CNY — 流动资产合计.
    cash_equivalent: float | None  # CNY — 货币资金.
    account_receivable: float | None  # CNY — 应收账款.
    inventory: float | None  # CNY — 存货.
    total_noncurrent_assets: float | None  # CNY — 非流动资产合计.
    fixed_asset: float | None  # CNY — 固定资产.
    goodwill: float | None  # CNY — 商誉.
    total_liability: float | None  # CNY — 负债合计.
    total_current_liability: float | None  # CNY — 流动负债合计.
    short_loan: float | None  # CNY — 短期借款.
    account_payable: float | None  # CNY — 应付账款.
    total_noncurrent_liability: float | None  # CNY — 非流动负债合计.
    long_loan: float | None  # CNY — 长期借款.
    bond_payable: float | None  # CNY — 应付债券.
    total_equity: float | None  # CNY — 所有者权益合计.
    parent_equity: float | None  # CNY — 归属于母公司股东权益合计.
    minority_equity: float | None  # CNY — 少数股东权益.
    capital_reserve: float | None  # CNY — 资本公积.
    surplus_reserve: float | None  # CNY — 盈余公积.
    retained_earnings: float | None  # CNY — 未分配利润.
    update_time: Optional[str]  # ISO 8601 — 东财接口 update_time (诊断用, 不参与主键).

    _check_stock_code = field_validator("stock_code")(check_stock_code)
    _check_report_date = field_validator("report_date")(check_trade_date)
