"""KDataService: 日 K 线 full_init + daily_update + read_window."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.datasource.service.universe import UniverseService
from alpha_backend.datasource.sources.akshare import FetchSummary, fetch_stock_hist
from alpha_backend.datasource.storage.io import DataFile
from alpha_backend.datasource.storage.state import StateTracker
from alpha_backend.utils import logger


@dataclass
class UpdateSummary:
    """KData 增量更新摘要."""

    type_name: str
    success: int
    failed: list[str]
    elapsed_sec: float
    first_date: str | None
    last_date: str | None
    row_count: int


class KDataService:
    """K 线服务.

    用法:
        svc = KDataService()
        svc.full_init(start="2020-01-01")             # 全量初始化
        svc.daily_update(today=date.today())           # 每日增量
        df = svc.read_window("2024-01-01", "2024-12-31")
    """

    def __init__(self, *, root: str | None = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = REGISTRY.get("KData")
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        # state 路径应与 KData.parquet 同根 (datas/_Manifest/Data_State.json)
        if root:
            self.state = StateTracker(Path(root) / "datas" / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()
        self.universe = UniverseService(root=str(self.root) if self.root else None)

    # ---------- 全量 ----------

    def full_init(
        self,
        *,
        start: str = "2020-01-01",
        source: str = "sina",
        batch_size: int = 100,
        progress: bool = True,
    ) -> UpdateSummary:
        """全量拉取所有 universe 股票的日 K 线.

        Args:
            start: 起始日期 ISO string.
            source: 'sina' (默认) / 'em' / 'tx'.
            batch_size: 累积 batch 大小 (行数 >= batch_size 时落盘).
            progress: 是否打 tqdm.
        """
        codes = self.universe.all_codes()
        if not codes:
            raise RuntimeError("universe 为空, 请先跑 UniverseService.full_init")

        logger.info(f"[KData] full_init: {len(codes)} stocks from {start}")
        return self._fetch_and_write(
            stock_codes=codes,
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
        today: date | None = None,
        source: str = "sina",
        batch_size: int = 100,
        progress: bool = True,
    ) -> UpdateSummary:
        """根据 StateTracker 推断缺口, 拉缺失区间 → 写入 → 更新 state."""
        today_str = today.isoformat() if today else date.today().isoformat()
        start, end = self.state.resume_range("KData", latest=today_str)
        if start > end:
            # 已 up-to-date
            stats = self.file_.stats()
            return UpdateSummary(
                type_name="KData",
                success=0,
                failed=[],
                elapsed_sec=0.0,
                first_date=stats.first_date,
                last_date=stats.last_date,
                row_count=stats.row_count,
            )

        logger.info(f"[KData] daily_update: resume {start} ~ {end}")
        codes = self.universe.all_codes()
        return self._fetch_and_write(
            stock_codes=codes,
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
        stock_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """读 [start, end] 区间 + 可选 stock_codes 白名单.

        调用前先 _assert_schema_fresh().
        """
        self._assert_schema_fresh()
        return self.file_.read(
            start=start, end=end, stock_codes=stock_codes
        )

    # ---------- helpers ----------

    def _assert_schema_fresh(self) -> None:
        """Data_State.json 中 KData 的 schema_version 与 dtype 一致."""
        from alpha_backend.datasource.storage.errors import SchemaVersionError

        state = self.state.read().get("KData")
        if state is None:
            return
        if state.schema_version != self.dtype.schema_version:
            raise SchemaVersionError(
                "KData", disk=state.schema_version, code=self.dtype.schema_version
            )

    def _fetch_and_write(
        self,
        *,
        stock_codes: list[str],
        start: str,
        end: str | None,
        source: str,
        batch_size: int,
        progress: bool,
        allow_existing: bool,
    ) -> UpdateSummary:
        """对每只股票调 fetch_stock_hist, 累积 batch_size 行后 upsert 一次."""
        iter_codes = stock_codes
        if progress:
            try:
                from tqdm import tqdm

                iter_codes = tqdm(stock_codes, desc="[KData]")
            except ImportError:
                pass

        failed: list[str] = []
        batch: list[pd.DataFrame] = []
        batch_rows = 0
        t0 = time.time()
        for code in iter_codes:
            try:
                df_one = fetch_stock_hist(
                    code, start=start, end=end, source=source
                )
                if df_one.empty:
                    continue
                batch.append(df_one)
                batch_rows += len(df_one)
                if batch_rows >= batch_size:
                    self._flush(batch, allow_existing)
                    batch = []
                    batch_rows = 0
            except Exception as e:
                logger.error(f"[KData] {code} 拉取失败: {e!r}")
                failed.append(code)
        if batch:
            self._flush(batch, allow_existing)

        elapsed = time.time() - t0
        stats = self.file_.stats()
        self.state.update("KData", stats)
        return UpdateSummary(
            type_name="KData",
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