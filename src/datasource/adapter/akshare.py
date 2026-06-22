"""AkShare 数据源适配器 (新契约: long-format, 9 字符 stock_code).

设计要点:
- 字段重命名: akshare 字段统一成内部契约 `trade_date`/`stock_code`/`open`/`high`/`low`/
  `close`/`volume`/`amount`
- stock_code 9 字符 .SH/.SZ 后缀 (Shanghai 6/9 开头, Shenzhen 0/3 开头)
- OHLCV 为 nullable float32
- 指数成分股: 返回 9 字符 stock_code 列表
- 业务代码不直接 import akshare

注: 不再产出 `close_adj` 字段 (M2 移除); 复权口径统一由 Stock_Basic_Data.adj_factor
承担, 后端按需 join 计算. 各 source 拉的 close 复权状态不同 (sina 前复权, em/tx 后复权),
调用方按业务需求自行处理.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, TypeVar

import pandas as pd

from logger import logger
from utils.tools import parse_iso_date, to_full_stock_code

# ---------- 配置 ----------

# 复权模式
ADJUST_POST = ""  # 后复权
ADJUST_NONE = ""  # 不复权 (用于 raw)

# 指数代码映射 (中证)
INDEX_CODE_MAP = {
    "csi1000": "000852",
    "csi500": "000905",
    "csi300": "000300",
    "csi100": "000903",
    "csi_all": "000985",  # 中证全指
}

# 重试参数
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.5
DEFAULT_RETRY_BASE_DELAY = 2.0

T = TypeVar("T")


# ---------- 重试装饰器 ----------


def with_retry(
    func: Callable[..., T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: float = DEFAULT_RETRY_BACKOFF,
    base_delay: float = DEFAULT_RETRY_BASE_DELAY,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """带指数退避的重试. 抛出最后一次异常."""
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return func()
        except exceptions as e:
            last_exc = e
            if attempt == max_retries - 1:
                break
            delay = base_delay * (backoff**attempt)
            logger.warning(
                f"[akshare] retry attempt {attempt + 1}/{max_retries} failed: {e!r}, sleep {delay:.1f}s"
            )
            time.sleep(delay)
    if last_exc is None:
        raise RuntimeError("with_retry: no exception recorded")
    raise last_exc


# ---------- stock_code 工具 ----------
def _strip_suffix(stock_code: str) -> str:
    """9 字符 '600000.SH' → '600000'."""
    return stock_code.split(".")[0]


# ---------- 归一化 ----------


def _normalize_stock_hist(raw: pd.DataFrame, stock_code_9: str) -> pd.DataFrame:
    """归一化 akshare 输出到内部契约.

    输入可能是 stock_zh_a_daily (sina) 或 stock_zh_a_hist (东财), 字段名各异.
    输出列: trade_date / stock_code / open / high / low / close / amount / volume

    注: 不再产出 `close_adj` (M2 移除). 各 source 的 `close` 复权口径不同,
    后端需要时按 Stock_Basic_Data.adj_factor 自行 join 计算.
    """
    rename = {
        "日期": "trade_date",
        "date": "trade_date",  # sina 源
        "股票代码": "_stock_id_raw",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = raw.rename(columns=rename)

    # trade_date: 转 ISO "YYYY-MM-DD" 字符串
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    else:
        raise ValueError(f"akshare 输出缺少日期列: {df.columns.tolist()}")

    # stock_code 9 字符 (东财源含原 6 位, 用其生成; sina 源不含, 用参数注入)
    if "_stock_id_raw" in df.columns:
        df["stock_code"] = df["_stock_id_raw"].astype(str).str.zfill(6).map(to_full_stock_code)
    else:
        df["stock_code"] = stock_code_9

    # 数值列: 转 float (允许 NaN = 停牌)
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")
        else:
            df[col] = pd.Series([None] * len(df), dtype="float32")

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


# ---------- 公开 API ----------


def fetch_stock_hist(
    stock_code: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    source: str = "sina",
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """拉单只股票日 K 线 (后复权, long-format).

    Args:
        stock_code: 9 字符 '600000.SH' / '000012.SZ' 或 6 位 '600000' / '000012'.
        start / end: ISO date string "YYYY-MM-DD" (None 表示不限).
        source: 'sina' (默认, 稳定) / 'em' (东财, 后复权支持) / 'tx' (腾讯).

    Returns:
        DataFrame columns: trade_date / stock_code / open / high / low / close /
                          amount / volume (全部 nullable float32).

    Note:
        各 source 拉的 close 复权口径不同 (sina 前复权, em/tx 后复权).
        业务层需要精确复权时, 按 Stock_Basic_Data.adj_factor join 计算.
    """
    # 归一化 stock_code
    if "." not in stock_code:
        stock_code_9 = to_full_stock_code(stock_code)
    else:
        stock_code_9 = stock_code
    code6 = _strip_suffix(stock_code_9)

    # 内部把 ISO 字符串解析成 date, 仅用于 vendor API 期望的 date 形式
    start_d = parse_iso_date(start) if start else None
    end_d = parse_iso_date(end) if end else None

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak  # 延迟 import, 业务代码不直接依赖

        if source == "sina":
            sym = f"sh{code6}" if code6.startswith(("6", "9")) else f"sz{code6}"
            raw = ak.stock_zh_a_daily(symbol=sym, adjust="qfq")
        elif source == "tx":
            sym = f"sh{code6}" if code6.startswith(("6", "9")) else f"sz{code6}"
            raw = ak.stock_zh_a_hist_tx(
                symbol=sym,
                start_date=start_d.strftime("%Y%m%d") if start_d else "20200101",
                end_date=end_d.strftime("%Y%m%d") if end_d else "20991231",
                adjust="qfq",
            )
        elif source == "em":
            raw = ak.stock_zh_a_hist(
                symbol=code6,
                period="daily",
                start_date=start_d.strftime("%Y%m%d") if start_d else "20200101",
                end_date=end_d.strftime("%Y%m%d") if end_d else "20991231",
                adjust="hfq",  # 后复权
            )
        else:
            raise ValueError(f"未知 source: {source}, 可选 'sina' / 'em' / 'tx'")
        if raw is None or raw.empty:
            raise RuntimeError(f"akshare 返回空数据: {stock_code_9}")
        return raw

    raw = with_retry(_do_fetch, max_retries=max_retries)
    df = _normalize_stock_hist(raw, stock_code_9)

    if start_d is not None:
        df = df[df["trade_date"] >= start]
    if end_d is not None:
        df = df[df["trade_date"] <= end]
    return df.reset_index(drop=True)


def fetch_index_constituents(universe: str, *, max_retries: int = DEFAULT_MAX_RETRIES) -> list[str]:
    """
    Args:
        universe: 指数名 ('csi1000' / 'csi500' / ...).
    """
    if universe not in INDEX_CODE_MAP:
        raise ValueError(f"未知 universe: {universe}, 可选: {list(INDEX_CODE_MAP.keys())}")
    code = INDEX_CODE_MAP[universe]
    logger.info(f"[akshare] fetching constituents of {universe} ({code})...")

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak

        return ak.index_stock_cons_csindex(symbol=code)

    df = with_retry(_do_fetch, max_retries=max_retries)
    if df is None or df.empty:
        raise RuntimeError(f"akshare 返回空成分股: {universe}")

    if "成分券代码" not in df.columns:
        raise RuntimeError(f"akshare 返回字段意外: {df.columns.tolist()}")
    if "成分券名称" not in df.columns:
        raise RuntimeError(f"akshare 返回字段意外: {df.columns.tolist()}")
    df["成分券代码"] = df["成分券代码"].astype(str)
    df["成分券代码"] = df["成分券代码"].apply(lambda x : to_full_stock_code(str(x).zfill(6)))
    df = df.rename(columns={"成分券代码": "stock_code", "成分券名称": "stock_name"})
    return df[["stock_code", "stock_name"]]


def fetch_with_fallback(
    stock_code: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """带 fallback: 先 sina, 失败 em, 再 tx."""
    last_err: BaseException | None = None
    for source in ("sina", "em", "tx"):
        try:
            return fetch_stock_hist(stock_code, start=start, end=end, source=source)
        except Exception as e:
            last_err = e
            logger.warning(f"[akshare] source={source} failed for {stock_code}: {e!r}, try next")
    raise RuntimeError(f"所有源都失败: {stock_code}: {last_err!r}")


def fetch_ipo_date(stock_code: str) -> str | None:
    """Best-effort: 拉单只股票的 IPO 日期 (YYYY-MM-DD). 失败返回 None.

    用于 UniverseService.full_init; 失败时用 '1990-01-01' 占位 (几乎所有股票都晚于此).
    """
    code6 = _strip_suffix(stock_code)
    try:
        import akshare as ak

        # stock_individual_info_em 提供 stock 上市日期
        df = ak.stock_individual_info_em(symbol=code6)
        if df is None or df.empty:
            return None
        # 找 "上市日期" 行
        for col in df.columns:
            mask = df[col].astype(str).str.contains("上市", na=False)
            if mask.any():
                rows = df[mask]
                # 取第二个列 (value)
                value_col = [c for c in df.columns if c != col][0]
                date_str = str(rows.iloc[0][value_col])
                # 解析 "1991-04-03" 格式
                if date_str and len(date_str) >= 10:
                    return date_str[:10]
    except Exception as e:
        logger.warning(f"[akshare] fetch_ipo_date({stock_code}) failed: {e!r}")
    return None


def fetch_adj_factor(
    stock_code: str,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> pd.DataFrame:
    """从 akshare 后复权日线推算 adj_factor.

    适用场景: bs query_adjust_factor 不可达时的 fallback. 精度不如 bs (四舍五入误差累积),
    但与 KData service 用的复权口径一致.

    Args:
        stock_code: 9 字符 '600000.SH' / '000012.SZ' 或 6 位.
        start / end: ISO date string "YYYY-MM-DD".

    Returns:
        DataFrame columns: trade_date / stock_code / adj_factor (float64).
        失败 raise (调用方 stock_basic_data service 捕获后 fallback).

    注: 优先用 akshare 的 `adj_factor` 列; 缺失时退化为 `close_hfq / close` 兜底.
        当 close=0 (停牌) 或 NaN → adj_factor 留 NaN.
    """
    if "." not in stock_code:
        stock_code_9 = to_full_stock_code(stock_code)
    else:
        stock_code_9 = stock_code
    code6 = _strip_suffix(stock_code_9)

    start_d = parse_iso_date(start) if start else None
    end_d = parse_iso_date(end) if end else None

    def _do_fetch() -> pd.DataFrame:
        import akshare as ak

        # stock_zh_a_daily adjust='hfq' 返回 close (不复权) + adj_factor? 列名因版本而异.
        # 经验上包含 'close' + 'adj_factor' 两列; 如列名漂移, 抛 KeyError 让上层 fallback.
        return ak.stock_zh_a_daily(symbol=code6, adjust="hfq")

    try:
        raw = with_retry(_do_fetch, max_retries=max_retries)
    except Exception as exc:
        raise RuntimeError(f"akshare fetch_adj_factor({stock_code}) failed: {exc!r}") from exc

    if raw is None or raw.empty:
        raise RuntimeError(f"akshare fetch_adj_factor({stock_code}) 返回空")

    # akshare 列名 (因版本): 'date', 'close', 'adj_factor' 或 'hfq_close'.
    # 兼容两种命名: 优先找 'adj_factor', 否则算 close_hfq / close.
    df = raw.copy()
    if "date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    elif "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    else:
        raise RuntimeError(f"akshare fetch_adj_factor: 找不到 date 列, cols={list(df.columns)}")

    if "adj_factor" in df.columns:
        adj = pd.to_numeric(df["adj_factor"], errors="coerce").astype("float64")
    else:
        # close_hfq / close 兜底
        close_col = "close" if "close" in df.columns else None
        adj_col = (
            "hfq_close"
            if "hfq_close" in df.columns
            else ("close_hfq" if "close_hfq" in df.columns else None)
        )
        if close_col is None or adj_col is None:
            raise RuntimeError(
                f"akshare fetch_adj_factor: 缺 adj_factor/close_hfq 列, cols={list(df.columns)}"
            )
        close = pd.to_numeric(df[close_col], errors="coerce")
        adj = pd.to_numeric(df[adj_col], errors="coerce") / close.replace(0, pd.NA)
        adj = adj.astype("float64")

    out = pd.DataFrame(
        {
            "trade_date": df["trade_date"],
            "stock_code": stock_code_9,
            "adj_factor": adj,
        }
    )

    # 区间过滤
    if start_d is not None:
        out = out[pd.to_datetime(out["trade_date"]) >= pd.Timestamp(start_d)]
    if end_d is not None:
        out = out[pd.to_datetime(out["trade_date"]) <= pd.Timestamp(end_d)]

    return out.reset_index(drop=True)


# ---------- 批量拉取 ----------


@dataclass
class FetchSummary:
    """批量拉取的统计摘要."""

    total: int
    success: int
    failed: list[str]
    elapsed_sec: float

    def __repr__(self) -> str:
        return (
            f"FetchSummary(total={self.total}, success={self.success}, "
            f"failed={len(self.failed)}, elapsed={self.elapsed_sec:.1f}s)"
        )


def fetch_stock_panel(
    stock_codes: list[str],
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    source: str = "sina",
    progress: bool = True,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> FetchSummary:
    """批量拉取多只股票日 K 线 (long-format, 9 字符 stock_code).

    返回的 DataFrame 不在这里聚合 — 调用方需要时自行 concat.
    本函数只汇报每只股票的成功/失败统计.
    """
    iter_ids = stock_codes
    if progress:
        try:
            from tqdm import tqdm

            iter_ids = tqdm(stock_codes, desc="[fetch]")
        except ImportError:
            pass

    failed: list[str] = []
    t0 = time.time()
    for sid in iter_ids:
        try:
            fetch_stock_hist(sid, start=start, end=end, source=source, max_retries=max_retries)
        except Exception as e:
            logger.error(f"[akshare] {sid} 拉取失败: {e!r}")
            failed.append(sid)
    elapsed = time.time() - t0
    summary = FetchSummary(
        total=len(stock_codes),
        success=len(stock_codes) - len(failed),
        failed=failed,
        elapsed_sec=elapsed,
    )
    logger.info(f"[akshare] {summary}")
    return summary


__all__ = [
    "ADJUST_NONE",
    "ADJUST_POST",
    "FetchSummary",
    "fetch_adj_factor",
    "fetch_index_constituents",
    "fetch_ipo_date",
    "fetch_stock_hist",
    "fetch_stock_panel",
    "fetch_with_fallback",
    "with_retry",
]
