"""财务数据 service 共享 helper.

5 个财务 service (income / balance / cashflow / indicator / dividend) 都需要:
- 并发按 chunk 拉 universe
- 失败/成功聚合
- state 更新

本模块提供:
- `_FETCH_CHUNK_CODES`: chunk 大小 (财务接口 payload 较小, 用 512)
- `FetchSummary` dataclass
- `parallel_chunked_fetch`: 一次性跑完, 返回 (summary, all_dfs)
- `upsert_and_update_state`: 合并 + upsert + state.update 三件套
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker
from logger import logger

# 财务接口 payload 小 (~24 KB JSON per symbol), 用 512 减少 parallel_run
# materialization 次数. 内存峰值 ~24 MB/worker.
_FETCH_CHUNK_CODES = 512


@dataclass
class FetchSummary:
    """chunked fetch 摘要."""

    type_name: str
    success_count: int = 0
    failed: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    rows_fetched: int = 0


def parallel_chunked_fetch(
    type_name: str,
    stock_codes: list[str],
    worker: Any,
    *,
    extra_args: tuple = (),
    chunk_size: int = _FETCH_CHUNK_CODES,
    desc: Optional[str] = None,
) -> tuple[FetchSummary, list[pd.DataFrame]]:
    """按 chunk_size 并发拉取, 收集 DataFrame. 失败 symbol 进入 summary.failed.

    Args:
        type_name: 用于日志.
        stock_codes: 整个 universe 的代码列表.
        worker: 已用 @parallel 装饰的 worker (有 .run_star 方法), 签名 (code, *extra_args) -> DataFrame.
        extra_args: 传给 worker 的额外位置参数 (e.g. (source,)).
        chunk_size: 每 chunk 的 symbol 数.
        desc: tqdm 描述前缀.

    Returns:
        (FetchSummary, all_dfs) 元组; all_dfs 是该批次所有非空 df 列表.
    """
    summary = FetchSummary(type_name=type_name)
    all_dfs: list[pd.DataFrame] = []
    t0 = time.time()

    for offset in range(0, len(stock_codes), chunk_size):
        chunk = stock_codes[offset : offset + chunk_size]
        payloads = [(code, *extra_args) for code in chunk]
        chunk_desc = desc or f"[{type_name}] {offset + 1}-{offset + len(chunk)}"
        results = worker.run_star(payloads, desc=chunk_desc, disable_progress=True)
        # ORDERED=True → results 与 chunk 同序
        # run_star 返回 list[TaskResult], 需要解 .value
        for code, res in zip(chunk, results):
            if not res.success:
                logger.warning(f"[{type_name}] {code} fetch 失败: {res.error}")
                summary.failed.append(code)
                continue
            df = res.value
            if df is None or df.empty:
                summary.failed.append(code)
                continue
            summary.success_count += 1
            summary.rows_fetched += len(df)
            all_dfs.append(df)

    summary.elapsed_sec = time.time() - t0
    logger.info(
        f"[{type_name}] fetch done: success={summary.success_count} "
        f"failed={len(summary.failed)} rows={summary.rows_fetched} "
        f"elapsed={summary.elapsed_sec:.1f}s"
    )
    return summary, all_dfs


def upsert_and_update_state(
    file_: DataFile,
    state: StateTracker,
    type_name: str,
    *,
    all_dfs: list[pd.DataFrame],
) -> dict[str, int]:
    """合并 all_dfs → upsert(replace) → 更新 state."""
    if not all_dfs:
        return {"rows": int(file_.stats().row_count), "skipped": True}
    merged = pd.concat(all_dfs, ignore_index=True)

    # 防御 pd.concat 跨多 df 升级: object 列里 None 会被提升成 float NaN (混合 dtype 时),
    # 走到 Pydantic nullable=str 校验会报 "input_value=nan, input_type=float".
    # 逐 cell 防御, 给 storage._validate_rows 的 iterrows bug 提供最后一道屏障.
    def _to_none_if_nan(v: object) -> object:
        try:
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
                return None
        except (TypeError, ValueError):
            return v
        return v

    merged = merged.map(_to_none_if_nan)
    file_.upsert(merged, conflict="replace")
    stats = file_.stats()
    state.update(type_name, stats)
    return {"rows": int(stats.row_count)}


__all__ = [
    "_FETCH_CHUNK_CODES",
    "FetchSummary",
    "parallel_chunked_fetch",
    "upsert_and_update_state",
]
