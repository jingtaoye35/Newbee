from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code, check_trade_date

__all__ = ["FinancialIndicator"]


class FinancialIndicator(BaseModel):
    """主要财务指标 (long format, quarterly snapshot, primary_key = stock_code + report_date)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Financial_Indicator"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "quarterly"
    storage_path: ClassVar[str] = "Financial_Indicator.parquet"
    primary_key: ClassVar[Tuple[str, ...]] = ("stock_code", "report_date")
    format: ClassVar[str] = "parquet"

    # ---- 数据源适配器 ----
    # 东财 EM 主源 → 非 EM 回退 (均为 akshare).
    adapters: ClassVar[List[List[DataAdapter]]] = [
        [DataAdapter.Akshare],
        [DataAdapter.Akshare],
    ]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    report_date: str  # YYYY-MM-DD — 报告期截止日 (季末).
    eps: float | None  # CNY — 基本每股收益 (TTM 近似).
    bvps: float | None  # CNY — 每股净资产 (最新).
    roe_weighted: float | None  # ratio — 加权平均净资产收益率 (ROE).
    gross_margin: float | None  # ratio — 毛利率 (累计).
    net_margin: float | None  # ratio — 净利率 (累计).
    revenue_yoy: float | None  # ratio — 营业总收入同比增长.
    netprofit_yoy: float | None  # ratio — 归母净利润同比增长.
    debt_asset_ratio: float | None  # ratio — 资产负债率.
    pe_ttm: float | None  # ratio — 市盈率 TTM (东财填充时).
    pb: float | None  # ratio — 市净率.
    update_time: Optional[str]  # ISO 8601 — 东财接口 update_time (诊断用, 不参与主键).

    _check_stock_code = field_validator("stock_code")(check_stock_code)
    _check_report_date = field_validator("report_date")(check_trade_date)
