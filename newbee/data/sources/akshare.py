"""AkShare 数据源适配器 (加厚版).

设计要点:
- 字段重命名: 把 akshare 字段统一成内部契约 `stock_id`/`date`/`open`/`high`/`low`/`close`/`volume`/`adj_close`
- 断点续传: 落盘带 hash, 重复运行不重下
- retry + 异常 fallback
- 换源路径: 本模块是数据源隔离层, 业务代码不直接 import akshare

M1 起步: 实现单只股票日 K 线 (前复权) 的下载 + 落盘接口,
         加上若干指数成分股的拉取 (用于 init_universe).
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, TypeVar

import pandas as pd

logger = logging.getLogger(__name__)

# ---------- 配置 ----------

# 默认日 K 线落盘目录
DEFAULT_RAW_DIR = Path("data/raw")
DEFAULT_ADJ_DIR = Path("data/adj")

# AkShare 字段映射 (东财版日 K 线)
# 原始字段: 日期, 股票代码, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
# 前复权: open / high / low / close / volume 都已复权, 无 adj_factor
_AK_STOCK_HIST_COLUMNS = ["date", "stock_id", "open", "close", "high", "low", "volume"]

# 重试参数
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.5  # 退避乘子
DEFAULT_RETRY_BASE_DELAY = 2.0  # 第一次重试前等多少秒

# 指数代码映射 (中证)
INDEX_CODE_MAP = {
    "csi1000": "000852",
    "csi500": "000905",
    "csi300": "000300",
    "csi100": "000903",
    "csi_all": "000985",  # 中证全指
}

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
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func()
        except exceptions as e:
            last_exc = e
            if attempt == max_retries - 1:
                break
            delay = base_delay * (backoff ** attempt)
            logger.warning(
                f"[retry] attempt {attempt+1}/{max_retries} failed: {e!r}, sleep {delay:.1f}s"
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ---------- Hash & 落盘 ----------


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _normalize_stock_hist(df: pd.DataFrame, stock_id: str | None = None) -> pd.DataFrame:
    """统一字段名 + dtype + 排序.

    ak.stock_zh_a_hist 前复权 (adjust='qfq') 输出列:
        日期, 股票代码, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
    ak.stock_zh_a_daily (新浪) 输出列:
        date, open, high, low, close, volume, amount, outstanding_share, turnover
        (无 stock_id, 需传入注入)
    """
    rename = {
        "日期": "date",
        "股票代码": "stock_id",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns=rename)

    # 内部契约保留列
    keep = ["date", "stock_id", "open", "high", "low", "close", "volume"]
    if "amount" in df.columns:
        keep.append("amount")
    if "outstanding_share" in df.columns:
        keep.append("outstanding_share")
    if "turnover" in df.columns:
        keep.append("turnover")

    # 注入 stock_id (新浪源无此列)
    if "stock_id" not in df.columns:
        if stock_id is None:
            raise ValueError("数据不含 stock_id 列, 需传 stock_id 参数")
        df["stock_id"] = stock_id

    df = df[keep].copy()

    # dtype
    df["date"] = pd.to_datetime(df["date"])
    df["stock_id"] = df["stock_id"].astype(str).str.zfill(6)
    for col in ["open", "high", "low", "close", "volume", "amount", "outstanding_share", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 复权后 close 即为 adj_close
    df["adj_close"] = df["close"]

    return df.sort_values("date").reset_index(drop=True)


# ---------- 公开 API ----------


def fetch_stock_hist(
    stock_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
    adjust: str = "qfq",
    source: str = "sina",  # 'sina' / 'em' / 'tx'
    use_cache: bool = True,
    raw_dir: Path = DEFAULT_RAW_DIR,
    max_retries: int = DEFAULT_MAX_RETRIES,
    append: bool = False,
) -> pd.DataFrame:
    """拉单只股票日 K 线 (默认前复权).

    Args:
        stock_id: 6 位股票代码 (如 "600000")
        start / end: 区间 (None 表示不限)
        adjust: 'qfq' (前复权, 默认) / 'hfq' (后复权) / '' (不复权)
        source: 数据源 'sina' (新浪, 默认, 稳定全量) / 'em' (东财, 实时但易被 GFW 拦) / 'tx' (腾讯)
        use_cache: True 时落盘带 hash, 已有文件不重下
        raw_dir: 落盘目录
        append: True 时执行"读-拼-写"模式 — 若已有 parquet, 读旧 + 拉新 + dedup + 重写;
                用于每日增量拉取, 保证跨日运行不重复行.
                默认 False, 行为与原先一致.

    Returns:
        DataFrame with columns: date / stock_id / open / high / low / close / volume / adj_close
        (amount / outstanding_share / turnover 可选)
    """
    stock_id = str(stock_id).zfill(6)

    # cache 文件路径 (per-stock 一个 parquet)
    cache_path = raw_dir / f"{stock_id}.parquet"

    # ---------- append 模式 ----------
    if append:
        old_df: pd.DataFrame | None = None
        if cache_path.exists():
            try:
                old_df = pd.read_parquet(cache_path)
                logger.info(
                    f"[akshare] append: cache hit {stock_id} "
                    f"({len(old_df)} rows, last={old_df['date'].max()})"
                )
            except Exception as e:
                logger.warning(f"[akshare] append: 读旧 {stock_id} 失败, 当 fresh 处理: {e!r}")
                old_df = None

        # 拉新区间. append 模式下 start/end 由调用方传入, 不能为 None.
        if start is None or end is None:
            raise ValueError(
                "append=True 时必须显式传入 start 与 end (增量区间)"
            )

        new_df = _fetch_fresh(
            stock_id=stock_id,
            start=start,
            end=end,
            adjust=adjust,
            source=source,
            max_retries=max_retries,
        )

        # 拼 + dedup + sort
        if old_df is not None and not old_df.empty:
            merged = pd.concat([old_df, new_df], ignore_index=True)
            # keep='last' 让新拉的行覆盖旧值 (复权因子更新场景)
            merged = merged.drop_duplicates(subset=["date"], keep="last")
        else:
            merged = new_df
        merged = merged.sort_values("date").reset_index(drop=True)

        # 原子写
        raw_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_parquet(merged, cache_path)
        logger.info(
            f"[akshare] append: saved {stock_id}: {len(merged)} rows "
            f"(+{len(new_df)} new) → {cache_path}"
        )
        # 返回区间内的视图
        out = merged[
            (merged["date"] >= pd.Timestamp(start))
            & (merged["date"] <= pd.Timestamp(end))
        ]
        return out.reset_index(drop=True)

    # ---------- 普通模式 (原行为不变) ----------
    if use_cache and cache_path.exists():
        logger.info(f"[akshare] cache hit: {stock_id}")
        df = pd.read_parquet(cache_path)
    else:
        df = _fetch_fresh(
            stock_id=stock_id,
            start=start,
            end=end,
            adjust=adjust,
            source=source,
            max_retries=max_retries,
        )
        raw_dir.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)
        logger.info(f"[akshare] saved {stock_id}: {len(df)} rows → {cache_path}")

    # 区间过滤 (cache hit 时也可能需要裁剪)
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["date"] <= pd.Timestamp(end)]

    return df.reset_index(drop=True)


def _fetch_fresh(
    *,
    stock_id: str,
    start: date | None,
    end: date | None,
    adjust: str,
    source: str,
    max_retries: int,
) -> pd.DataFrame:
    """直接调 akshare 拉新区间, 不读 cache. 返回归一化后的 DataFrame."""
    import akshare as ak  # 延迟 import, 业务代码不直接依赖

    logger.info(
        f"[akshare] fetching {stock_id} (source={source}, adjust={adjust}, "
        f"{start}~{end})..."
    )

    def _do_fetch():
        if source == "sina":
            sym = f"sh{stock_id}" if stock_id.startswith(("6", "9")) else f"sz{stock_id}"
            return ak.stock_zh_a_daily(symbol=sym, adjust=adjust)
        elif source == "tx":
            sym = f"sh{stock_id}" if stock_id.startswith(("6", "9")) else f"sz{stock_id}"
            return ak.stock_zh_a_hist_tx(
                symbol=sym,
                start_date=start.strftime("%Y%m%d") if start else "20200101",
                end_date=end.strftime("%Y%m%d") if end else "20991231",
                adjust=adjust,
            )
        elif source == "em":
            return ak.stock_zh_a_hist(
                symbol=stock_id,
                period="daily",
                start_date=start.strftime("%Y%m%d") if start else "20200101",
                end_date=end.strftime("%Y%m%d") if end else "20991231",
                adjust=adjust,
            )
        else:
            raise ValueError(f"未知 source: {source}, 可选 'sina' / 'em' / 'tx'")

    raw = with_retry(_do_fetch, max_retries=max_retries)
    if raw is None or raw.empty:
        raise RuntimeError(f"akshare 返回空数据: {stock_id}")

    df = _normalize_stock_hist(raw, stock_id=stock_id)
    # 若指定区间, 提前过滤 (akshare 默认会按 start/end 拉, 但为了防御性再裁一次)
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df.reset_index(drop=True)


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    """原子写 parquet (tempfile + os.replace). 防止写一半崩溃损坏文件."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".parquet_", suffix=".tmp", dir=str(path.parent)
    )
    os.close(fd)  # pyarrow 要自己开文件
    try:
        df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def fetch_index_constituents(
    universe: str, *, max_retries: int = DEFAULT_MAX_RETRIES
) -> list[str]:
    """拉指数成分股 stock_id 列表.

    Args:
        universe: 指数名 ('csi1000' / 'csi500' / ...)
    """
    if universe not in INDEX_CODE_MAP:
        raise ValueError(f"未知 universe: {universe}, 可选: {list(INDEX_CODE_MAP.keys())}")

    code = INDEX_CODE_MAP[universe]
    logger.info(f"[akshare] fetching constituents of {universe} ({code})...")

    def _do_fetch():
        import akshare as ak

        return ak.index_stock_cons_csindex(symbol=code)

    df = with_retry(_do_fetch, max_retries=max_retries)
    if df is None or df.empty:
        raise RuntimeError(f"akshare 返回空成分股: {universe}")

    # 字段: '成分券代码' (6 位字符串)
    if "成分券代码" not in df.columns:
        raise RuntimeError(f"akshare 返回字段意外: {df.columns.tolist()}")

    ids = df["成分券代码"].astype(str).str.zfill(6).tolist()
    logger.info(f"[akshare] {universe}: {len(ids)} constituents")
    return ids


def fetch_with_fallback(
    stock_id: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """带 fallback 的拉取: 先 sina, 失败 em, 再 tx.

    适用场景: 默认源不可用时, 退化到备选源.
    """
    last_err = None
    for source in ("sina", "em", "tx"):
        try:
            return fetch_stock_hist(
                stock_id, start=start, end=end, adjust="qfq", source=source
            )
        except Exception as e:
            last_err = e
            logger.warning(
                f"[akshare] source={source} failed for {stock_id}: {e!r}, try next"
            )
    raise RuntimeError(f"所有源都失败: {stock_id}: {last_err!r}")


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
    stock_ids: list[str],
    *,
    start: date | None = None,
    end: date | None = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
    progress: bool = True,
) -> FetchSummary:
    """批量拉取多只股票日 K 线.

    Args:
        stock_ids: stock_id 列表
        start / end: 区间
        raw_dir: 落盘目录
        progress: 是否打 tqdm 进度条

    Returns:
        FetchSummary 包含成功/失败统计
    """
    iter_ids = stock_ids
    if progress:
        try:
            from tqdm import tqdm

            iter_ids = tqdm(stock_ids, desc="[fetch]")
        except ImportError:
            pass

    failed: list[str] = []
    t0 = time.time()
    for sid in iter_ids:
        try:
            fetch_stock_hist(sid, start=start, end=end, use_cache=True, raw_dir=raw_dir)
        except Exception as e:
            logger.error(f"[akshare] {sid} 拉取失败: {e!r}")
            failed.append(sid)
    elapsed = time.time() - t0
    summary = FetchSummary(
        total=len(stock_ids),
        success=len(stock_ids) - len(failed),
        failed=failed,
        elapsed_sec=elapsed,
    )
    logger.info(f"[akshare] {summary}")
    return summary