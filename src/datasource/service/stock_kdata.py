"""KDataService: 日 K 线 full_init + daily_update + read_window."""

from __future__ import annotations

import time
import pandas as pd

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Tuple, Optional

from datasource.schema.stock_kdata import StockKData
from datasource.service.universe import UniverseService
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker, DEFAULT_RESUME_START
from logger import logger
from utils.parallel_run import parallel
from utils.tools import now_date
from common import DataAdapter

# 每轮并发拉取的股票数上限. 拉取在子进程并行, 结果由 parallel_run 物化为 list,
# 故按 code 分块以约束峰值内存 (尤其 full_init 单只 history 较大时).
_FETCH_CHUNK_CODES = 256

# akshare 子源列表 (K-line 专用). auto 模式下依次尝试, 全部失败后切 baostock.
_AKSHARE_KLINE_SOURCES: Tuple[str, ...] = ("sina", "em", "tx")


@parallel
def _fetch_one_kdata(code: str, start: str, end: Optional[str], source: str) -> pd.DataFrame:
    """worker 进程内拉取单只股票日 K 线, 返回 DataFrame (可能为空).

    必须是模块级函数: @parallel 在 spawn 下要求 worker 重新 import 本模块以重新
    注册. 此处只做网络拉取, 写盘仍在主进程串行完成 (parquet 单文件, 不可并发写).

    Source dispatch (由 StockKData.adapters 驱动):
    - 'sina' / 'em' / 'tx' → 直接走 akshare fetch_stock_hist(source=...) 单一源.
    - 'bs'                → 直接走 baostock fetch_stock_hist_baostock (无 fallback).
    - 'auto' 或其他        → 遍历 akshare 子源 (sina→em→tx), 再切 baostock.
    """
    last_err: BaseException | None = None
    for adapter_type in StockKData.adapters:
        if adapter_type == DataAdapter.Akshare:
            for src in _AKSHARE_KLINE_SOURCES:
                try:
                    from datasource.adapter.akshare import fetch_stock_hist
                    result = fetch_stock_hist(code, start=start, end=end, source=src)
                    return result
                except Exception as e:
                    logger.error(f"[Stock_KData][{adapter_type}][{src}] failed. e:[{e}]")
                    last_err = e
                    continue
        elif adapter_type == DataAdapter.Baostock:
            try:
                from datasource.adapter.baostock import fetch_stock_hist_baostock
                result = fetch_stock_hist_baostock(code, start=start, end=end)
                return result
            except Exception as e:
                logger.error(f"[Stock_KData][{adapter_type}] failed. e:[{e}]")
                last_err = e
                continue
        else:
            last_err = RuntimeError(f"Used unknown DataAdapter {adapter_type}")
            logger.error(f"[Stock_KData][{adapter_type}] failed. e:[{last_err}]")

    raise RuntimeError(
        f"Stock_KData [{code}] fetch failed with all adapters {StockKData.adapters}: last_err={last_err!r}"
    ) from last_err 


@dataclass
class UpdateSummary:
    """Stock_KData 增量更新摘要."""

    type_name: str
    success: int
    failed: list[str]
    elapsed_sec: float
    first_date: Optional[str]
    last_date: Optional[str]
    row_count: int


class KDataService:
    """
    K 线服务. 用法:
        svc = KDataService()
        svc.full_init(start=DEFAULT_RESUME_START)             # 全量初始化
        svc.daily_update(today=date.today())           # 每日增量
        df = svc.read_window("2024-01-01", "2024-12-31")
    """

    def __init__(self, *, root: Optional[str] = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = StockKData
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        # state 路径应与 Stock_KData.parquet 同根 (datas/_Manifest/Data_State.json)
        if root:
            self.state = StateTracker(self.file_.path.parent / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()  # 走 default_state_path() → datasource_dir/_Manifest/
        self.universe = UniverseService(root=str(self.root) if self.root else None).stock_pool()

    # ---------- 全量 ----------

    def full_init(
        self, *, start: str = DEFAULT_RESUME_START, source: str = "auto", batch_size: int = 1_000_000, progress: bool = True,
    ) -> UpdateSummary:
        """全量拉取所有 universe 股票的日 K 线.

        Args:
            start: 起始日期 ISO string.
            source: 'auto' (默认, 走 4-tier fallback) / 'sina' / 'em' / 'tx' / 'bs'.
            batch_size: 累积 batch 大小 (行数 >= batch_size 时落盘).
                默认 1e6, 配合 `_FETCH_CHUNK_CODES=256` 让每个 chunk 只 flush
                一次, 避免每 100 行就触发整文件 merge + 重写. 调小可手动控制
                内存峰值.
            progress: 是否打 tqdm.
        """
        if not self.universe:
            raise RuntimeError("universe 为空, 请先跑 UniverseService.full_init")

        logger.info(f"[Stock_KData] full_init: {len(self.universe)} stocks from {start}")
        return self._fetch_and_write(
            stock_codes=self.universe,
            start=start,
            end=None,
            source=source,
            batch_size=batch_size,
            progress=progress,
            allow_existing=False,
        )

    # ---------- 每日增量 ----------

    def daily_update(
        self,
        *,
        today: Optional[str] = None,
        source: str = "auto",
        batch_size: int = 1_000_000,
        progress: bool = True,
    ) -> UpdateSummary:
        """根据 StateTracker 推断缺口, 拉缺失区间 → 写入 → 更新 state.

        Args:
            today: 截止日期, 默认今天.
            source: 'auto' (默认, 走 4-tier fallback) / 'sina' / 'em' / 'tx' / 'bs'.
            batch_size: 累积 batch 大小 (行数 >= batch_size 时落盘). 默认 1e6,
                配合 `_FETCH_CHUNK_CODES=256` 让 daily_update 的小批新数据每个
                chunk 只 flush 一次.
            progress: 是否打 tqdm.
        """
        today = today or now_date()
        if not self.file_.exists():
            logger.info(
                f"[StockKData daily_update] no existing parquet, falling back to full_init from {DEFAULT_RESUME_START}"
            )
            return self.full_init()

        start, end = self.state.resume_range("Stock_KData", latest=today)
        if start > end:
            stats = self.file_.stats()
            return UpdateSummary(
                type_name="Stock_KData",
                success=0,
                failed=[],
                elapsed_sec=0.0,
                first_date=stats.first_date,
                last_date=stats.last_date,
                row_count=stats.row_count,
            )

        logger.info(f"[Stock_KData] daily_update: resume {start} ~ {end}")
        return self._fetch_and_write(
            stock_codes=self.universe,
            start=start,
            end=end,
            source=source,
            batch_size=batch_size,
            progress=progress,
            allow_existing=True,
        )

    # ---------- 读窗口 ----------

    def read_window(
        self,
        start: str,
        end: str,
        stock_codes: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """读 [start, end] 区间 + 可选 stock_codes 白名单.

        调用前先 _assert_schema_fresh().
        """
        self._assert_schema_fresh()
        return self.file_.read(start=start, end=end, stock_codes=stock_codes)

    # ---------- helpers ----------

    def _assert_schema_fresh(self) -> None:
        """Data_State.json 中 Stock_KData 的 schema_version 与 dtype 一致."""
        from utils.errors import SchemaVersionError

        state = self.state.read().get("Stock_KData")
        if state is None:
            return
        if state.schema_version != self.dtype.schema_version:
            raise SchemaVersionError(
                "Stock_KData", disk=state.schema_version, code=self.dtype.schema_version
            )

    def _fetch_and_write(
        self,
        *,
        stock_codes: list[str],
        start: str,
        end: Optional[str],
        source: str,
        batch_size: int,
        progress: bool,
        allow_existing: bool,
    ) -> UpdateSummary:
        """并发拉取每只股票的日 K, 累积 batch_size 行后 upsert 一次.

        拉取在子进程池并行 (`_fetch_one_kdata`), 写盘在主进程串行. 按
        `_FETCH_CHUNK_CODES` 分块, 每块并发拉取后立即消费 / 落盘, 约束峰值内存.
        """
        failed: list[str] = []
        batch: list[pd.DataFrame] = []
        batch_rows = 0
        t0 = time.time()

        for offset in range(0, len(stock_codes), _FETCH_CHUNK_CODES):
            chunk = stock_codes[offset : offset + _FETCH_CHUNK_CODES]
            payloads = [(code, start, end, source) for code in chunk]
            results = _fetch_one_kdata.run_star(
                payloads,
                desc=f"[Stock_KData] {offset + 1}-{offset + len(chunk)}",
                disable_progress=not progress,
            )
            # parallel_run ORDERED=True → results 与 chunk 同序, 可按位对齐 code.
            for code, res in zip(chunk, results):
                if not res.success:
                    logger.error(f"[Stock_KData] {code} 拉取失败: {res.error}")
                    failed.append(code)
                    continue
                df_one = res.value
                if df_one is None or df_one.empty:
                    continue
                batch.append(df_one)
                batch_rows += len(df_one)
                if batch_rows >= batch_size:
                    self._flush(batch, allow_existing)
                    batch = []
                    batch_rows = 0

        if batch:
            self._flush(batch, allow_existing)
        logger.info("flushed")
        elapsed = time.time() - t0
        stats = self.file_.stats()
        self.state.update("Stock_KData", stats)
        return UpdateSummary(
            type_name="Stock_KData",
            success=len(stock_codes) - len(failed),
            failed=failed,
            elapsed_sec=elapsed,
            first_date=stats.first_date,
            last_date=stats.last_date,
            row_count=stats.row_count,
        )

    def _flush(self, batch: list[pd.DataFrame], allow_existing: bool) -> None:
        """合并 batch → upsert."""
        merged = pd.concat(batch, ignore_index=True)
        if allow_existing:
            # 增量更新时允许覆盖
            self.file_.upsert(merged, conflict="replace")
        else:
            # 全量初始化时, 用 append (冲突会抛错, 但首次不会)
            try:
                self.file_.append(merged)
            except Exception:
                # 已有数据 → 退到 upsert(replace)
                self.file_.upsert(merged, conflict="replace")


__all__ = ["KDataService", "UpdateSummary"]
