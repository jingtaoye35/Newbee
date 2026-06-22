"""AkShare 财务数据源适配器 (5 个 fetch 函数).

设计要点:
- 业务侧禁止直接 import akshare; 财务相关网络拉取统一走本模块.
- 输出 long-format DataFrame, 列名匹配 src/datasource/schemas/<Type>.py 中定义的 BaseModel 字段.
- 9 字符 stock_code 强制 .SH/.SZ 后缀 (与 Stock_KData 约定一致).
- 每个 fetch 用 with_retry 包一层, 默认 3 次重试 / backoff 1.5.
- 输入 stock_code 接受 6 位 "600000" 或 9 位 "600000.SH"; 内部归一化.

已知字段映射:
- 东财 `_em` 接口的中文 / 全大写英文列名 → snake_case 英文列名
- 当源字段缺失时 (AkShare 改字段名), 返回空列 + logger.warn, 不抛错
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, TypeVar

import pandas as pd

from utils.tools import to_full_stock_code
from datasource.adapter.akshare import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_RETRY_BASE_DELAY,
    _strip_suffix,
    with_retry,
)
from logger import logger

T = TypeVar("T")


# ---------- symbol 归一化 ----------


def _normalize_symbol(symbol: str) -> str:
    """9 字符 / 6 位 → 9 字符 .SH/.SZ. 抛 ValueError 当无法识别."""
    if "." in symbol:
        # 已是 9 字符, 校验后缀
        return to_full_stock_code(_strip_suffix(symbol))
    return to_full_stock_code(symbol)


def _to_em_symbol(stock_code_9: str) -> str:
    """9 字符 .SH/.SZ → 东财接口使用的 SH600000 / SZ000012 格式."""
    code6, suffix = stock_code_9.split(".")
    return f"{suffix}{code6}"


# ---------- 内部: 选列 + 重命名 ----------


def _rename_columns(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """根据 mapping 重命名; 源字段缺失时 logger.warn 并跳过."""
    if df.empty:
        return df
    present: dict[str, str] = {}
    for src, dst in mapping.items():
        if src in df.columns:
            present[src] = dst
        else:
            logger.warning(f"[finance] 源字段 {src!r} 不存在, 跳过映射到 {dst!r}")
    return df.rename(columns=present)


def _to_double(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """对指定列做 to_numeric(..., errors='coerce')."""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _ensure_columns(df: pd.DataFrame, required: list[str]) -> pd.DataFrame:
    """保证 DataFrame 含 required 列; 缺失则补 None 列."""
    for col in required:
        if col not in df.columns:
            df[col] = None
    return df


def _normalize_trade_date(d: Any) -> str | None:
    """东财日期字段 (YYYY-MM-DD 或 '2024-09-30 00:00:00') → ISO 10 字符."""
    if d is None or (isinstance(d, float) and pd.isna(d)):
        return None
    try:
        return pd.to_datetime(d).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return str(d)[:10] if d else None


# ---------- 利润表 (Income Statement) ----------


# 东财 stock_profit_sheet_by_report_em 返回的列 (经过内部解析) 包含:
# REPORT_DATE, REPORT_TYPE, TOTAL_OPERATE_INCOME, OPERATE_INCOME,
# TOTAL_OPERATE_COST, OPERATE_COST, SALE_EXPENSE, MANAGE_EXPENSE,
# FINANCE_EXPENSE, RESEARCH_EXPENSE, OPERATE_PROFIT, TOTAL_PROFIT,
# INCOME_TAX, NETPROFIT, PARENT_NETPROFIT, MINORITY_NETPROFIT,
# BASIC_EPS, DILUTED_EPS, ...
_INCOME_RENAME: dict[str, str] = {
    "REPORT_DATE": "report_date",
    "TOTAL_OPERATE_INCOME": "total_operate_income",
    "OPERATE_INCOME": "operate_income",
    "TOTAL_OPERATE_COST": "total_operate_cost",
    "OPERATE_COST": "operate_cost",
    "SALE_EXPENSE": "sale_expense",
    "MANAGE_EXPENSE": "manage_expense",
    "FINANCE_EXPENSE": "finance_expense",
    "RESEARCH_EXPENSE": "research_expense",
    "OPERATE_PROFIT": "operate_profit",
    "TOTAL_PROFIT": "total_profit",
    "INCOME_TAX": "income_tax",
    "NETPROFIT": "netprofit",
    "PARENT_NETPROFIT": "parent_netprofit",
    "MINORITY_NETPROFIT": "minority_netprofit",
    "BASIC_EPS": "basic_eps",
    "DILUTED_EPS": "diluted_eps",
}

_INCOME_NUMERIC_COLS: Tuple[str, ...] = (
    "total_operate_income",
    "operate_income",
    "total_operate_cost",
    "operate_cost",
    "sale_expense",
    "manage_expense",
    "finance_expense",
    "research_expense",
    "operate_profit",
    "total_profit",
    "income_tax",
    "netprofit",
    "parent_netprofit",
    "minority_netprofit",
    "basic_eps",
    "diluted_eps",
)


def fetch_financial_report_income(
    stock_code: str,
    *,
    source: str = "em",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """拉单只股票利润表, long-format.

    Args:
        stock_code: 9 字符 .SH/.SZ 或 6 位.
        source: 'em' (唯一支持, 预留扩展).
        max_retries: with_retry 次数.

    Returns:
        DataFrame columns: stock_code, report_date, <numeric monetary cols>, update_time.
        包含该股票的所有报告期; 列名与 Financial_Report_Income.yaml 一致.
    """
    if source != "em":
        raise ValueError(f"fetch_financial_report_income: source={source!r} 不支持 (仅 'em')")

    sc = _normalize_symbol(stock_code)
    em_symbol = _to_em_symbol(sc)

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak

        return ak.stock_profit_sheet_by_report_em(symbol=em_symbol)

    try:
        raw = with_retry(
            _do_fetch,
            max_retries=max_retries,
            backoff=DEFAULT_RETRY_BACKOFF,
            base_delay=DEFAULT_RETRY_BASE_DELAY,
        )
    except Exception as exc:
        logger.error(f"[finance/income] {sc} 拉取失败: {exc!r}")
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=["stock_code", "report_date"] + list(_INCOME_NUMERIC_COLS) + ["update_time"]
        )

    df = raw.copy()
    df = _rename_columns(df, _INCOME_RENAME)
    if "report_date" in df.columns:
        df["report_date"] = df["report_date"].apply(_normalize_trade_date)
    df = _to_double(df, list(_INCOME_NUMERIC_COLS))
    df["stock_code"] = sc
    if "UPDATE_TIME" in raw.columns:
        df["update_time"] = raw["UPDATE_TIME"].astype(str)
    else:
        df["update_time"] = None
    df = _ensure_columns(
        df, ["stock_code", "report_date"] + list(_INCOME_NUMERIC_COLS) + ["update_time"]
    )
    return df[["stock_code", "report_date"] + list(_INCOME_NUMERIC_COLS) + ["update_time"]]


# ---------- 资产负债表 (Balance Sheet) ----------


_BALANCE_RENAME: dict[str, str] = {
    "REPORT_DATE": "report_date",
    "TOTAL_ASSETS": "total_assets",
    "TOTAL_CURRENT_ASSETS": "total_current_assets",
    "CASH_EQUIVALENT": "cash_equivalent",
    "ACCOUNT_RECEIVABLE": "account_receivable",
    "INVENTORY": "inventory",
    "TOTAL_NONCURRENT_ASSETS": "total_noncurrent_assets",
    "FIXED_ASSET": "fixed_asset",
    "GOODWILL": "goodwill",
    "TOTAL_LIABILITY": "total_liability",
    "TOTAL_CURRENT_LIABILITY": "total_current_liability",
    "SHORT_LOAN": "short_loan",
    "ACCOUNT_PAYABLE": "account_payable",
    "TOTAL_NONCURRENT_LIABILITY": "total_noncurrent_liability",
    "LONG_LOAN": "long_loan",
    "BOND_PAYABLE": "bond_payable",
    "TOTAL_EQUITY": "total_equity",
    "PARENT_EQUITY": "parent_equity",
    "MINORITY_EQUITY": "minority_equity",
    "CAPITAL_RESERVE": "capital_reserve",
    "SURPLUS_RESERVE": "surplus_reserve",
    "RETAINED_EARNINGS": "retained_earnings",
}

_BALANCE_NUMERIC_COLS: Tuple[str, ...] = (
    "total_assets",
    "total_current_assets",
    "cash_equivalent",
    "account_receivable",
    "inventory",
    "total_noncurrent_assets",
    "fixed_asset",
    "goodwill",
    "total_liability",
    "total_current_liability",
    "short_loan",
    "account_payable",
    "total_noncurrent_liability",
    "long_loan",
    "bond_payable",
    "total_equity",
    "parent_equity",
    "minority_equity",
    "capital_reserve",
    "surplus_reserve",
    "retained_earnings",
)


def fetch_financial_report_balance(
    stock_code: str,
    *,
    source: str = "em",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """拉单只股票资产负债表, long-format. 同 income 的列名策略."""
    if source != "em":
        raise ValueError(f"fetch_financial_report_balance: source={source!r} 不支持 (仅 'em')")

    sc = _normalize_symbol(stock_code)
    em_symbol = _to_em_symbol(sc)

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak

        return ak.stock_balance_sheet_by_report_em(symbol=em_symbol)

    try:
        raw = with_retry(
            _do_fetch,
            max_retries=max_retries,
            backoff=DEFAULT_RETRY_BACKOFF,
            base_delay=DEFAULT_RETRY_BASE_DELAY,
        )
    except Exception as exc:
        logger.error(f"[finance/balance] {sc} 拉取失败: {exc!r}")
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=["stock_code", "report_date"] + list(_BALANCE_NUMERIC_COLS) + ["update_time"]
        )

    df = raw.copy()
    df = _rename_columns(df, _BALANCE_RENAME)
    if "report_date" in df.columns:
        df["report_date"] = df["report_date"].apply(_normalize_trade_date)
    df = _to_double(df, list(_BALANCE_NUMERIC_COLS))
    df["stock_code"] = sc
    if "UPDATE_TIME" in raw.columns:
        df["update_time"] = raw["UPDATE_TIME"].astype(str)
    else:
        df["update_time"] = None
    df = _ensure_columns(
        df, ["stock_code", "report_date"] + list(_BALANCE_NUMERIC_COLS) + ["update_time"]
    )
    return df[["stock_code", "report_date"] + list(_BALANCE_NUMERIC_COLS) + ["update_time"]]


# ---------- 现金流量表 (Cash Flow) ----------


_CASHFLOW_RENAME: dict[str, str] = {
    "REPORT_DATE": "report_date",
    "OPERATE_CASH_FLOW_NET": "operate_cash_flow_net",
    "SALE_SERVICE_CASH": "sale_service_cash",
    "BUY_SERVICE_CASH": "buy_service_cash",
    "INVEST_CASH_FLOW_NET": "invest_cash_flow_net",
    "INVEST_PAY_CASH": "invest_pay_cash",
    "FINANCE_CASH_FLOW_NET": "finance_cash_flow_net",
    "BORROW_CASH": "borrow_cash",
    "REPAY_DEBT_CASH": "repay_debt_cash",
    "DIVIDEND_PAY_CASH": "dividend_pay_cash",
    "CASH_NET_INCREASE": "cash_net_increase",
    "CASH_END_PERIOD": "cash_end_period",
    "CASH_BEGIN_PERIOD": "cash_begin_period",
}

_CASHFLOW_NUMERIC_COLS: Tuple[str, ...] = (
    "operate_cash_flow_net",
    "sale_service_cash",
    "buy_service_cash",
    "invest_cash_flow_net",
    "invest_pay_cash",
    "finance_cash_flow_net",
    "borrow_cash",
    "repay_debt_cash",
    "dividend_pay_cash",
    "cash_net_increase",
    "cash_end_period",
    "cash_begin_period",
)


def fetch_financial_report_cashflow(
    stock_code: str,
    *,
    source: str = "em",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """拉单只股票现金流量表, long-format."""
    if source != "em":
        raise ValueError(f"fetch_financial_report_cashflow: source={source!r} 不支持 (仅 'em')")

    sc = _normalize_symbol(stock_code)
    em_symbol = _to_em_symbol(sc)

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak

        return ak.stock_cash_flow_sheet_by_report_em(symbol=em_symbol)

    try:
        raw = with_retry(
            _do_fetch,
            max_retries=max_retries,
            backoff=DEFAULT_RETRY_BACKOFF,
            base_delay=DEFAULT_RETRY_BASE_DELAY,
        )
    except Exception as exc:
        logger.error(f"[finance/cashflow] {sc} 拉取失败: {exc!r}")
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=["stock_code", "report_date"] + list(_CASHFLOW_NUMERIC_COLS) + ["update_time"]
        )

    df = raw.copy()
    df = _rename_columns(df, _CASHFLOW_RENAME)
    if "report_date" in df.columns:
        df["report_date"] = df["report_date"].apply(_normalize_trade_date)
    df = _to_double(df, list(_CASHFLOW_NUMERIC_COLS))
    df["stock_code"] = sc
    if "UPDATE_TIME" in raw.columns:
        # 源若含 NaN, astype(str) 会变字符串 "nan" → 后续 Pydantic 报 invalid string.
        # 先转 object 再把 NaN 映成 None.
        df["update_time"] = (
            raw["UPDATE_TIME"].astype(object).where(raw["UPDATE_TIME"].notna(), None)
        )
    else:
        df["update_time"] = None
    df = _ensure_columns(
        df, ["stock_code", "report_date"] + list(_CASHFLOW_NUMERIC_COLS) + ["update_time"]
    )
    return df[["stock_code", "report_date"] + list(_CASHFLOW_NUMERIC_COLS) + ["update_time"]]


# ---------- 主要财务指标 (Indicator) ----------


_INDICATOR_RENAME: dict[str, str] = {
    "REPORT_DATE": "report_date",
    "EPSJB": "eps",
    "EPSKCJB": "eps",
    "EPSJB_SY": "eps",
    "BPS": "bvps",
    "MGJYXJJE": "bvps",
    "ROEJQ": "roe_weighted",
    "XSMLL": "gross_margin",
    "XSJLL": "net_margin",
    "TOTAL_OPERATE_INCOME_YOY": "revenue_yoy",
    "YYSR_YOY": "revenue_yoy",
    "PARENT_NETPROFIT_YOY": "netprofit_yoy",
    "ZGYSR": "revenue_yoy",
    "ZGMGJYXJJE": "netprofit_yoy",
    "ZCFZL": "debt_asset_ratio",
    "LD": "debt_asset_ratio",
    "PETFMC": "pe_ttm",
    "PBMRQ": "pb",
}

_INDICATOR_NUMERIC_COLS: Tuple[str, ...] = (
    "eps",
    "bvps",
    "roe_weighted",
    "gross_margin",
    "net_margin",
    "revenue_yoy",
    "netprofit_yoy",
    "debt_asset_ratio",
    "pe_ttm",
    "pb",
)


# 非EM 备源 stock_financial_analysis_indicator 的中文列 → schema 字段.
# 注意: 非EM 接口不提供 pe_ttm / pb (fallback 时这两列留 None).
_INDICATOR_NONEM_RENAME: dict[str, str] = {
    "日期": "report_date",
    "加权每股收益(元)": "eps",
    "每股净资产_调整后(元)": "bvps",
    "加权净资产收益率(%)": "roe_weighted",
    "销售毛利率(%)": "gross_margin",
    "销售净利率(%)": "net_margin",
    "主营业务收入增长率(%)": "revenue_yoy",
    "净利润增长率(%)": "netprofit_yoy",
    "资产负债率(%)": "debt_asset_ratio",
}

# 非EM 接口以百分数给出的比率字段, 需 /100 归一为 schema unit=ratio.
_INDICATOR_NONEM_PCT_COLS: Tuple[str, ...] = (
    "roe_weighted",
    "gross_margin",
    "net_margin",
    "revenue_yoy",
    "netprofit_yoy",
    "debt_asset_ratio",
)

# 非EM 接口按 start_year 拉取, 回溯足够覆盖 3~5y 窗口.
_NONEM_LOOKBACK_YEARS = 6


def _empty_indicator_df() -> pd.DataFrame:
    """Financial_Indicator schema 形状的空表 (warn-and-skip 时返回)."""
    return pd.DataFrame(
        columns=["stock_code", "report_date"] + list(_INDICATOR_NUMERIC_COLS) + ["update_time"]
    )


def _fetch_indicator_nonem(sc: str, *, max_retries: int) -> pd.DataFrame:
    """非EM 备源 stock_financial_analysis_indicator, 适配为 Financial_Indicator schema.

    非EM 接口不返回 pe_ttm / pb (这两列留 None); 比率字段以百分数给出, 需 /100.
    """
    code6 = _strip_suffix(sc)
    start_year = str(date.today().year - _NONEM_LOOKBACK_YEARS)

    def _do_fetch() -> pd.DataFrame | None:
        import akshare as ak

        return ak.stock_financial_analysis_indicator(symbol=code6, start_year=start_year)

    try:
        raw = with_retry(
            _do_fetch,
            max_retries=max_retries,
            backoff=DEFAULT_RETRY_BACKOFF,
            base_delay=DEFAULT_RETRY_BASE_DELAY,
        )
    except Exception as exc:
        logger.warning(f"[finance/indicator] {sc} 非EM 备源拉取失败: {exc!r}")
        return _empty_indicator_df()

    if raw is None or raw.empty:
        logger.warning(f"[finance/indicator] {sc} 非EM 备源返回空 (None 或空表)")
        return _empty_indicator_df()

    df = raw.copy()
    df = _rename_columns(df, _INDICATOR_NONEM_RENAME)
    if "report_date" in df.columns:
        df["report_date"] = df["report_date"].apply(_normalize_trade_date)
    df = _to_double(df, list(_INDICATOR_NUMERIC_COLS))
    for col in _INDICATOR_NONEM_PCT_COLS:
        if col in df.columns:
            df[col] = df[col] / 100.0
    df["stock_code"] = sc
    df["update_time"] = None
    df = _ensure_columns(
        df, ["stock_code", "report_date"] + list(_INDICATOR_NUMERIC_COLS) + ["update_time"]
    )
    return df[["stock_code", "report_date"] + list(_INDICATOR_NUMERIC_COLS) + ["update_time"]]


def _fetch_indicator_em(sc: str, *, max_retries: int) -> pd.DataFrame:
    """主源 stock_financial_analysis_indicator_em (按报告期), 适配为 Financial_Indicator schema.

    返回: 已对齐 schema 列的 DataFrame. 失败/空 → 返回空表 (列已对齐), 不抛异常.
    """
    em_symbol = _to_em_symbol(sc)

    def _do_fetch() -> pd.DataFrame | None:
        import akshare as ak

        return ak.stock_financial_analysis_indicator_em(symbol=em_symbol, indicator="按报告期")

    try:
        raw = with_retry(
            _do_fetch,
            max_retries=max_retries,
            backoff=DEFAULT_RETRY_BACKOFF,
            base_delay=DEFAULT_RETRY_BASE_DELAY,
        )
    except Exception as exc:
        logger.warning(f"[finance/indicator] {sc} EM 主源拉取失败: {exc!r}")
        return _empty_indicator_df()

    if raw is None or raw.empty:
        logger.warning(f"[finance/indicator] {sc} EM 主源返回空")
        return _empty_indicator_df()

    df = raw.copy()
    df = _rename_columns(df, _INDICATOR_RENAME)
    if "report_date" in df.columns:
        df["report_date"] = df["report_date"].apply(_normalize_trade_date)
    df = _to_double(df, list(_INDICATOR_NUMERIC_COLS))
    df["stock_code"] = sc
    if "UPDATE_TIME" in raw.columns:
        df["update_time"] = raw["UPDATE_TIME"].astype(str)
    else:
        df["update_time"] = None
    df = _ensure_columns(
        df, ["stock_code", "report_date"] + list(_INDICATOR_NUMERIC_COLS) + ["update_time"]
    )
    return df[["stock_code", "report_date"] + list(_INDICATOR_NUMERIC_COLS) + ["update_time"]]


def fetch_financial_indicator(
    stock_code: str,
    *,
    source: str = "em",
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_sec: float = 1.0,
) -> pd.DataFrame:
    """拉单只股票主要财务指标, long-format.

    跨源 fallback: 主源 stock_financial_analysis_indicator_em → 非EM 备源.
    两源皆失败返回空表 (列已对齐 schema), 由 service 负责 warn-and-skip.

    本函数是 thin wrapper, 业务代码可继续 import 这里.
    """
    if source != "em":
        raise ValueError(f"fetch_financial_indicator: source={source!r} 不支持 (仅 'em')")

    sc = _normalize_symbol(stock_code)

    # EM 主源: 返回空表视为"无数据", 切非EM 备源.
    df = _fetch_indicator_em(sc, max_retries=max_retries)
    if df is not None and not df.empty:
        return df
    logger.warning(f"[finance/indicator] {sc} EM 主源返回空")

    # 跨源边界: EM → 非EM (同属 akshare, 不同 endpoint).
    logger.error(f"[finance/indicator→非EM] {sc} EM 主源死, 切非EM 备源")
    return _fetch_indicator_nonem(sc, max_retries=max_retries)


# ---------- 历史分红 (Dividend History) ----------


# stock_dividend_cninfo 返回的列 (cninfo 巨潮):
# 实施方案公告日期 / 分红类型 / 转增比例 / 送股比例 / 派息比例 /
# 股权登记日 / 除权日 / 派息日 / 股份到账日 / 报告时间
_DIVIDEND_RENAME: dict[str, str] = {
    "实施方案公告日期": "report_date",  # cninfo 给的"报告时间"实际是公告日
    "除权日": "ex_date",
    "股权登记日": "record_date",
    "派息日": "pay_date",
    "派息比例": "dividend_per_share",
    "送股比例": "share_bonus_per_share",
    "转增比例": "share_dividend_per_share",
}

_DIVIDEND_NUMERIC_COLS: Tuple[str, ...] = (
    "dividend_per_share",
    "share_bonus_per_share",
    "share_dividend_per_share",
)


def fetch_dividend_history(
    stock_code: str,
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """拉单只股票历史分红事件 (cninfo 巨潮).

    Args:
        stock_code: 9 字符 .SH/.SZ 或 6 位.
        max_retries: with_retry 次数.

    Returns:
        DataFrame columns: stock_code, ex_date, record_date, pay_date,
        dividend_per_share, share_bonus_per_share, share_dividend_per_share,
        report_date (公告日, nullable), update_time.
        多行 = 多事件 (按时间倒序).

    Note:
        与 fetch_financial_* 不同, 此函数没有 source 参数 — 仅 cninfo 巨潮.
        Per-symbol 调用, 在 service 层用 parallel chunk 循环 universe.
    """
    sc = _normalize_symbol(stock_code)
    code6 = _strip_suffix(sc)

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak

        return ak.stock_dividend_cninfo(symbol=code6)

    try:
        raw = with_retry(
            _do_fetch,
            max_retries=max_retries,
            backoff=DEFAULT_RETRY_BACKOFF,
            base_delay=DEFAULT_RETRY_BASE_DELAY,
        )
    except Exception as exc:
        logger.warning(f"[finance/dividend] {sc} 拉取失败: {exc!r}")
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()
    df = _rename_columns(df, _DIVIDEND_RENAME)
    for col in ("report_date", "ex_date", "record_date", "pay_date"):
        if col in df.columns:
            df[col] = df[col].apply(_normalize_trade_date)
    df = _to_double(df, list(_DIVIDEND_NUMERIC_COLS))
    df["stock_code"] = sc
    df["update_time"] = pd.Timestamp.now().isoformat()
    df = _ensure_columns(
        df,
        [
            "stock_code",
            "ex_date",
            "record_date",
            "pay_date",
            "report_date",
            "dividend_per_share",
            "share_bonus_per_share",
            "share_dividend_per_share",
            "update_time",
        ],
    )
    return df[
        [
            "stock_code",
            "ex_date",
            "record_date",
            "pay_date",
            "report_date",
            "dividend_per_share",
            "share_bonus_per_share",
            "share_dividend_per_share",
            "update_time",
        ]
    ]


__all__ = [
    "fetch_financial_report_income",
    "fetch_financial_report_balance",
    "fetch_financial_report_cashflow",
    "fetch_financial_indicator",
    "fetch_dividend_history",
    "_fetch_indicator_em",
    "_fetch_indicator_nonem",
]
