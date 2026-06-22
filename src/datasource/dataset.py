"""datasource.dataset — backend 读访问的统一门面.

**Backend MUST import from this module only.**
禁止 backend 直接 import ``datasource.storage.*`` 或 ``datasource.service.*`` 用于读,
所有 read 走这里. 内部 ``DataFile`` / ``bars_adapter`` / ``pool_adapter`` / ``adapter.calendar``
是 implementation detail, 不属于稳定 API.

每个 loader 接受 ``root: Optional[Path] = None``:
  - ``root is None`` → 从 ``config.paths.datasource_dir`` 解析 (与生成端一致)
  - ``root`` 提供   → override, 用于 test sandbox
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pandas as pd

from datasource.schema.dividend_history import DividendHistory
from datasource.schema.financial_indicator import FinancialIndicator
from datasource.schema.financial_report_balance import FinancialReportBalance
from datasource.schema.financial_report_cashflow import FinancialReportCashflow
from datasource.schema.financial_report_income import FinancialReportIncome
from datasource.schema.stock_basic_data import StockBasicData
from datasource.schema.stock_kdata import StockKData
from datasource.service.trade_date import (
    add_trade_dates,
    align_to_trade_date,
    is_trade_date,
    latest_trade_day,
    month_end_trade_day,
    next_trade_date,
    prev_trade_date,
    between_trade_dates,
    in_trade_dates,
)
from datasource.storage.bars_adapter import Bars, load_bars
from datasource.storage.io import DataFile
from datasource.storage.pool_adapter import StockPool

__all__ = [
    "load_kdata",
    "load_universe",
    "load_stock_basic_data",
    "load_trade_calendar",
    "load_dividend_history",
    "load_financial_report",
    "load_financial_indicator",
    # 交易日工具
    "is_trade_date",
    "next_trade_date",
    "prev_trade_date",
    "between_trade_dates",
    "in_trade_dates",
    "align_to_trade_date",
    "add_trade_dates",
    "month_end_trade_day",
    "latest_trade_day",
]


def _resolve_root(root: Path | None) -> Path | None:
    """root is None → None (让 DataFile 走 PROJECT_ROOT 默认);
    显式给 root → 原样返回. loader 把 root 传给 DataFile 即可.

    注: 当前实现不主动从 config 读, 留 None 让 DataFile 用 PROJECT_ROOT.
    backend 在 _load_parquet() 已经从 config.paths.datasource_dir 取 root 传入;
    dataset.load_*() 不传 root 时用 PROJECT_ROOT (与生成端 cli/service 一致).
    """
    return root


def load_kdata(
    stock_codes: list[str],
    start: str,
    end: str,
    *,
    root: Optional[Path] = None,
) -> Bars:
    """读 Stock_KData.parquet → Bars (T, N, 6) 矩阵化 K 线.

    Args:
        stock_codes: 9 字符 .SH/.SZ 代码列表, 空列表 = 全市场.
        start / end: ISO date string "YYYY-MM-DD".
        root: datas/ 目录 override (默认 PROJECT_ROOT).

    注: M2 移除 `close_adj` 后, Bars.adj_close 等价于 Bars.close
    (vendor 拉的 close 口径: sina 前复权, em/tx/bs 后复权).
    """
    return load_bars(
        stock_codes=stock_codes,
        start=start,
        end=end,
        root=root,
    )


def load_universe(*, root: Optional[Path] = None) -> StockPool:
    """读 Universe.parquet → StockPool (legacy API, 含 .stock_ids 等)."""
    return StockPool.load()


def load_stock_basic_data(
    start: str,
    end: str,
    *,
    stock_codes: Optional[List[str]] = None,
    root: Optional[Path] = None,
) -> pd.DataFrame:
    """读 Stock_Basic_Data.parquet 区间 [start, end] (按 trade_date)."""
    return DataFile(StockBasicData, root=root).read(start=start, end=end, stock_codes=stock_codes)


def load_trade_calendar(
    start: str,
    end: str,
) -> list[str]:
    """返回 [start, end] 闭区间内所有交易日 (升序, ISO 字符串).

    此函数不依赖 schema-backed 文件, 直接走 service.trade_date.
    ``start`` / ``end`` 必填, 无 root 参数 (calendar 是 vendor 元数据).
    """
    return between_trade_dates(start=start, end=end)


def load_dividend_history(
    *,
    stock_codes: Optional[List[str]] = None,
    root: Optional[Path] = None,
) -> pd.DataFrame:
    """读 Dividend_History.parquet 全量 (无时间字段过滤)."""
    return DataFile(DividendHistory, root=root).read(stock_codes=stock_codes)


def load_financial_report(
    kind: Literal["income", "balance", "cashflow"],
    *,
    stock_codes: Optional[List[str]] = None,
    report_dates: Optional[List[str]] = None,
    root: Optional[Path] = None,
) -> pd.DataFrame:
    """读财务三表之一. kind ∈ {'income', 'balance', 'cashflow'}.

    注: schema 是 long format, 主键 (stock_code, report_date). 无 trade_date 字段,
    暂时不支持按时间窗过滤; ``stock_codes`` 和 ``report_dates`` 预留.
    """
    schema_map = {
        "income": FinancialReportIncome,
        "balance": FinancialReportBalance,
        "cashflow": FinancialReportCashflow,
    }
    if kind not in schema_map:
        raise ValueError(f"kind must be one of {set(schema_map)}, got {kind!r}")
    df = DataFile(schema_map[kind], root=root).read(stock_codes=stock_codes)
    if report_dates and "report_date" in df.columns:
        df = df[df["report_date"].isin(report_dates)]
    return df


def load_financial_indicator(
    *,
    stock_codes: Optional[List[str]] = None,
    report_dates: Optional[List[str]] = None,
    root: Optional[Path] = None,
) -> pd.DataFrame:
    """读 Financial_Indicator.parquet. ``report_dates`` 预留过滤."""
    df = DataFile(FinancialIndicator, root=root).read(stock_codes=stock_codes)
    if report_dates and "report_date" in df.columns:
        df = df[df["report_date"].isin(report_dates)]
    return df
