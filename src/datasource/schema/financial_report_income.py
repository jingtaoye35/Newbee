from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["FinancialReportIncome"]


class FinancialReportIncome(BaseModel):
    """利润表 (long format, quarterly, primary_key = stock_code + report_date)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Financial_Report_Income"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "quarterly"
    storage_path: ClassVar[str] = "Financial_Report_Income.parquet"
    primary_key: ClassVar[Tuple[str, ...]] = ("stock_code", "report_date")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    adapters: ClassVar[List[DataAdapter]] = [DataAdapter.Akshare]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    report_date: str  # YYYY-MM-DD — 报告期截止日 (季末, ISO 10 字符).
    total_operate_income: float | None  # CNY — 营业总收入 (累计).
    operate_income: float | None  # CNY — 营业收入 (累计).
    total_operate_cost: float | None  # CNY — 营业总成本 (累计).
    operate_cost: float | None  # CNY — 营业成本 (累计).
    sale_expense: float | None  # CNY — 销售费用 (累计).
    manage_expense: float | None  # CNY — 管理费用 (累计).
    finance_expense: float | None  # CNY — 财务费用 (累计).
    research_expense: float | None  # CNY — 研发费用 (累计).
    operate_profit: float | None  # CNY — 营业利润 (累计).
    total_profit: float | None  # CNY — 利润总额 (累计).
    income_tax: float | None  # CNY — 所得税费用 (累计).
    netprofit: float | None  # CNY — 净利润 (累计).
    parent_netprofit: float | None  # CNY — 归属于母公司股东的净利润 (累计).
    minority_netprofit: float | None  # CNY — 少数股东损益 (累计).
    basic_eps: float | None  # CNY — 基本每股收益 (累计).
    diluted_eps: float | None  # CNY — 稀释每股收益 (累计).
    update_time: Optional[str]  # ISO 8601 — 东财接口 update_time (诊断用, 不参与主键).

    _check_stock_code = field_validator("stock_code")(check_stock_code)
    _check_report_date = field_validator("report_date")(check_trade_date)
