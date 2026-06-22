from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["FinancialReportCashflow"]


class FinancialReportCashflow(BaseModel):
    """现金流量表 (long format, quarterly, primary_key = stock_code + report_date)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Financial_Report_Cashflow"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "quarterly"
    storage_path: ClassVar[str] = "Financial_Report_Cashflow.parquet"
    primary_key: ClassVar[Tuple[str, ...]] = ("stock_code", "report_date")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    adapters: ClassVar[List[DataAdapter]] = [DataAdapter.Akshare]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    report_date: str  # YYYY-MM-DD — 报告期截止日 (季末).
    operate_cash_flow_net: float | None  # CNY — 经营活动产生的现金流量净额.
    sale_service_cash: float | None  # CNY — 销售商品、提供劳务收到的现金.
    buy_service_cash: float | None  # CNY — 购买商品、接受劳务支付的现金.
    invest_cash_flow_net: float | None  # CNY — 投资活动产生的现金流量净额.
    invest_pay_cash: float | None  # CNY — 投资支付的现金.
    finance_cash_flow_net: float | None  # CNY — 筹资活动产生的现金流量净额.
    borrow_cash: float | None  # CNY — 取得借款收到的现金.
    repay_debt_cash: float | None  # CNY — 偿还债务支付的现金.
    dividend_pay_cash: float | None  # CNY — 分配股利、利润或偿付利息支付的现金.
    cash_net_increase: float | None  # CNY — 现金及现金等价物净增加额.
    cash_end_period: float | None  # CNY — 期末现金及现金等价物余额.
    cash_begin_period: float | None  # CNY — 期初现金及现金等价物余额.
    update_time: Optional[str]  # ISO 8601 — 东财接口 update_time (诊断用, 不参与主键).

    _check_stock_code = field_validator("stock_code")(check_stock_code)
    _check_report_date = field_validator("report_date")(check_trade_date)
