"""Baostock 数据源适配器 — akshare 失效时的跨源 fallback.

设计要点:
- baostock 唯一允许被 import 的地方: 本文件 (sources 包内唯一).
- 符号约定: 内部 9 字符 "600000.SH" / "000012.SZ" ↔ bs 前缀点 "sh.600000" /
  "sz.000012" (转换器 _to_bs_symbol).
- 登录: 进程内 lazy bs.login(), 模块级 _LOGGED_IN flag (subprocess 间互不影响).
- 字段重命名: bs 的中文列 → 内部契约列, 单一归一化点.
- 失败语义: fetch_*_baostock 在 with_retry 耗尽后 raise; 调用方 (router.py) 负责
  跨源 ERROR 切换.
"""

from __future__ import annotations

from datetime import datetime

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

# ---------- 模块级 lazy login ----------

_LOGGED_IN = False
# 当 bs.login() 持续失败 (e.g. 黑名单用户) 时, 整个进程直接短路不再重试,
# 避免 per-symbol 1000 次 login 风暴. 失败信息仍由 _ensure_login 抛出,
# 上层 service 走 akshare fallback.
_BS_DISABLED_IN_PROCESS = False


def _ensure_login() -> None:
    """每个进程内只调用一次 bs.login(). 失败 → RuntimeError."""
    global _LOGGED_IN, _BS_DISABLED_IN_PROCESS
    if _LOGGED_IN or _BS_DISABLED_IN_PROCESS:
        return
    import baostock as bs

    # 先打 disabled 标记: 即使下面 lg.error_msg 抛错 (会被 import-time fail-fast),
    # 也要保证标记已置, 避免 1000 worker 子进程重复 login.
    _BS_DISABLED_IN_PROCESS = True
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    # 成功 → 撤销 disabled, 标 logged_in
    _BS_DISABLED_IN_PROCESS = False
    _LOGGED_IN = True


def _reset_login_for_tests() -> None:
    """测试用: 重置登录状态, 让下一次 _ensure_login 重新调 bs.login()."""
    global _LOGGED_IN, _BS_DISABLED_IN_PROCESS
    _LOGGED_IN = False
    _BS_DISABLED_IN_PROCESS = False


# ---------- 符号转换 ----------


def _to_bs_symbol(stock_code: str) -> str:
    """内部 9 字符 '600000.SH' / '000012.SZ' → bs 形式 'sh.600000' / 'sz.000012'.

    6/9 开头 → 上海 (sh.); 0/3 开头 → 深圳 (sz.).
    """
    if "." not in stock_code:
        code6 = str(stock_code).strip().zfill(6)
        if not code6.isdigit() or len(code6) != 6:
            raise ValueError(f"stock_code 必须是 6 位数字或 9 字符 .SH/.SZ, 得到 {stock_code!r}")
        stock_code_9 = to_full_stock_code(code6)
    else:
        stock_code_9 = stock_code
    code6 = _strip_suffix(stock_code_9)
    if code6.startswith(("6", "9")):
        return f"sh.{code6}"
    if code6.startswith(("0", "3")):
        return f"sz.{code6}"
    raise ValueError(f"无法识别交易所, stock_code={stock_code_9!r}")


def _format_date(d: str | None, default: str) -> str:
    """ISO str / None → 'YYYYMMDD' 紧凑形式 (bs 端要求).

    Public boundary takes ISO date string; this helper strips the
    '-' separators to satisfy baostock's ``YYYYMMDD`` compact format.
    """
    if d is None:
        return default
    return d.replace("-", "")


# ---------- 归一化 ----------


def _normalize_bs_kdata(raw: pd.DataFrame, stock_code_9: str) -> pd.DataFrame:
    """baostock Stock_KData → 内部契约.

    bs 列: 日期 / 股票代码 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额 / 复权状态
    内部列: trade_date / stock_code / open / high / low / close / volume / amount

    注: 不再产出 `close_adj` (M2 移除). bs query_history_k_data_plus(adjustflag='2')
    拉的 close 是后复权, 后端需要时按 Stock_Basic_Data.adj_factor 自行 join.
    """
    if raw is None or raw.empty:
        raise RuntimeError(f"baostock Stock_KData 返回空: {stock_code_9}")

    rename = {
        "日期": "trade_date",
        "股票代码": "_bs_code_raw",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = raw.rename(columns=rename)

    # trade_date: bs 返回 "2024-09-30" 已 ISO, 直接保留
    if "trade_date" not in df.columns:
        raise ValueError(f"baostock Stock_KData 缺少日期列: {df.columns.tolist()}")
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")

    # stock_code: bs 返回 'sh.600000' → 转回内部 9 字符
    if "_bs_code_raw" in df.columns:
        df["stock_code"] = df["_bs_code_raw"].astype(str).map(lambda s: _bs_to_internal_code(s))
    else:
        df["stock_code"] = stock_code_9

    # 数值列
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")
        else:
            df[col] = pd.Series([None] * len(df), dtype="float32")
    for col in ("volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        else:
            df[col] = pd.Series([None] * len(df), dtype="float64")

    out = df[
        [
            "trade_date",
            "stock_code",
            "open",
            "high",
            "low",
            "close",
            "amount",
            "volume",
        ]
    ].copy()
    out = out.sort_values("trade_date").reset_index(drop=True)
    return out


def _bs_to_internal_code(bs_code: str) -> str:
    """'sh.600000' → '600000.SH'."""
    if "." not in bs_code:
        return bs_code
    prefix, code6 = bs_code.split(".", 1)
    suffix = ".SH" if prefix == "sh" else ".SZ" if prefix == "sz" else f".{prefix.upper()}"
    return f"{code6}{suffix}"


# ---------- 公开 API ----------


def fetch_stock_hist_baostock(
    stock_code: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """拉单只股票日 K 线 (后复权, long-format).

    Args:
        stock_code: 9 字符 '600000.SH' / '000012.SZ' 或 6 位 '600000' / '000012'.
        start / end: ISO date string "YYYY-MM-DD" (None 表示不限).
        max_retries: bs 调用重试次数.

    Returns:
        DataFrame columns: trade_date / stock_code / open / high / low / close /
                          amount / volume (全部 nullable float32 / float64).

    注: bs 在 adjustflag='2' 下拉的 close 已是后复权; 不再单独输出 close_adj 字段.
    """
    _ensure_login()
    import baostock as bs

    bs_code = _to_bs_symbol(stock_code)
    code9 = bs_code_to_internal_9(bs_code)

    start_str = _format_date(start, default="2020-01-01")
    end_str = _format_date(end, default="2099-12-31")

    fields = "date,code,open,high,low,close,volume,amount"

    def _do_fetch() -> pd.DataFrame:
        rs = bs.query_history_k_data_plus(
            code=bs_code,
            fields=fields,
            start_date=start_str,
            end_date=end_str,
            frequency="d",
            adjustflag="2",  # 后复权
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query_history_k_data_plus: {rs.error_msg}")
        return rs.get_data()

    raw = with_retry(_do_fetch, max_retries=max_retries)
    df = _normalize_bs_kdata(raw, code9)

    if start is not None:
        df = df[df["trade_date"] >= start]
    if end is not None:
        df = df[df["trade_date"] <= end]
    return df.reset_index(drop=True)


def bs_code_to_internal_9(bs_code: str) -> str:
    """'sh.600000' → '600000.SH' (公开, 给 _normalize 用)."""
    return _bs_to_internal_code(bs_code)


def fetch_ipo_date_baostock(stock_code: str) -> str | None:
    """拉单只股票的 IPO 日期 (YYYY-MM-DD). 失败返回 None.

    用 bs.query_stock_basic(code=bs_code) 拉该股的静态信息, 取 ipoDate 字段.
    """
    try:
        _ensure_login()
        import baostock as bs

        bs_code = _to_bs_symbol(stock_code)

        def _do_fetch():
            rs = bs.query_stock_basic(code=bs_code)
            if rs.error_code != "0":
                raise RuntimeError(f"baostock query_stock_basic: {rs.error_msg}")
            return rs.get_data()

        df = with_retry(_do_fetch)
        if df is None or df.empty:
            return None
        if "ipoDate" not in df.columns:
            return None
        ipo_raw = str(df.iloc[0]["ipoDate"]).strip()
        if not ipo_raw or ipo_raw.lower() == "nan":
            return None
        # bs 返回 "1991-04-03" 格式
        if len(ipo_raw) >= 10:
            return ipo_raw[:10]
        return None
    except Exception as e:
        logger.warning(f"[baostock] fetch_ipo_date({stock_code}) failed: {e!r}")
        return None


def fetch_adj_factor_baostock(
    stock_code: str,
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    """拉单只股票 adj_factor, 返回 long-format DataFrame (trade_date, stock_code, adj_factor).

    列结构:
      - trade_date: ISO YYYY-MM-DD
      - stock_code: 9 字符 .SH/.SZ
      - adj_factor: float64

    Raises:
        任何 baostock 错误 → 上抛 (调用方 stock_basic_data service 处理 fallback).
        空结果 → 上抛 RuntimeError (区别于"真实空集"还是 vendor 失败).

    Note: 这是 M5.5 重构后的 per-symbol 入口. service 用 parallel_chunked_fetch
    包一层来批量并发拉, 走 warn-and-skip 语义 (与 5 个财务 service 一致).
    """
    _ensure_login()
    import baostock as bs

    bs_code = _to_bs_symbol(stock_code)
    start_str = _format_date(start, default="2020-01-01")
    end_str = _format_date(end, default="2099-12-31")

    def _do_fetch():
        rs = bs.query_adjust_factor(
            code=bs_code,
            start_date=start_str,
            end_date=end_str,
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query_adjust_factor: {rs.error_msg}")
        return rs.get_data()

    # bs 已被本进程禁用 (login 黑名单) → 不走 with_retry 的 3 次重试, 立即抛
    # 让 service 直接 fallback 到 akshare.
    if _BS_DISABLED_IN_PROCESS:
        raw = _do_fetch()
    else:
        raw = with_retry(_do_fetch, max_retries=DEFAULT_MAX_RETRIES)
    if raw is None or raw.empty:
        raise RuntimeError(f"baostock adj_factor 空: {stock_code}")

    # bs 列: code, tradeDate, adjustFactor (含前/后复权标识; 这里只取后复权)
    # 当前 baostock 接口直接返回的是后复权因子
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(raw["tradeDate"]).dt.strftime("%Y-%m-%d"),
            "stock_code": stock_code,
            "adj_factor": pd.to_numeric(raw["adjustFactor"], errors="coerce").astype("float64"),
        }
    )


def fetch_adj_factor_panel(
    stock_codes: list[str],
    *,
    start: str,
    end: str,
) -> pd.DataFrame:
    """批量拉 adj_factor (legacy). 内部循环 fetch_adj_factor_baostock per-symbol.

    Deprecated: service 应直接用 parallel_chunked_fetch + fetch_adj_factor_baostock.
    保留只为 backward compat, 调用方 (e.g. cli populate-stock-basic-adj) 仍引用.
    """
    if not stock_codes:
        return pd.DataFrame(columns=["trade_date", "stock_code", "adj_factor"])

    rows: list[pd.DataFrame] = []
    for code9 in stock_codes:
        try:
            rows.append(fetch_adj_factor_baostock(code9, start=start, end=end))
        except Exception as e:
            logger.warning(
                f"[baostock] fetch_adj_factor({code9}) failed after retries: {e!r}, "
                f"该股票 adj_factor 留 NaN"
            )
            continue

    if not rows:
        return pd.DataFrame(columns=["trade_date", "stock_code", "adj_factor"])
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["trade_date", "stock_code"]).reset_index(drop=True)


def fetch_trade_dates_baostock(*, start: str, end: str) -> pd.DataFrame:
    """拉交易日历 (单列 trade_date ISO 字符串). bs.query_trade_dates 过滤 is_trade_date=1.

    Returns:
        DataFrame columns: trade_date (ISO YYYY-MM-DD string).
    """
    _ensure_login()
    import baostock as bs

    start_str = _format_date(start, default="1990-01-01")
    end_str = _format_date(end, default="2099-12-31")

    def _do_fetch():
        rs = bs.query_trade_dates(start_date=start_str, end_date=end_str)
        if rs.error_code != "0":
            raise RuntimeError(f"baostock query_trade_dates: {rs.error_msg}")
        return rs.get_data()

    raw = with_retry(_do_fetch, max_retries=DEFAULT_MAX_RETRIES)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["trade_date"], dtype="string")

    # bs 列: calendar_date, is_trade_date (0/1)
    if "is_trade_date" not in raw.columns or "calendar_date" not in raw.columns:
        raise RuntimeError(f"baostock query_trade_dates 返回字段意外: {raw.columns.tolist()}")
    df = raw[raw["is_trade_date"].astype(str) == "1"].copy()
    df["trade_date"] = pd.to_datetime(df["calendar_date"]).dt.strftime("%Y-%m-%d")
    return df[["trade_date"]].sort_values("trade_date").reset_index(drop=True)


__all__ = [
    "fetch_stock_hist_baostock",
    "fetch_ipo_date_baostock",
    "fetch_adj_factor_panel",
    "fetch_adj_factor_baostock",
    "fetch_trade_dates_baostock",
]
