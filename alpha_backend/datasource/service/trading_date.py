"""Trading_DateService: A 股交易日历 full_init + daily_update.

M1 固定使用 XSHG (上交所) 日历, 通过 `exchange_calendars` 枚举实际交易日,
写入 `datas/Trading_Date.csv` (单列 `trading_date`, ISO YYYY-MM-DD).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import exchange_calendars as ecals
import pandas as pd

from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.datasource.storage.io import DataFile
from alpha_backend.datasource.storage.state import StateTracker
from alpha_backend.utils import logger

# 默认 A 股日历 (上交所, 与深交所同步)
DEFAULT_CALENDAR = "XSHG"

# 默认全量初始化起点
DEFAULT_FULL_INIT_START = "2010-01-01"


@dataclass
class UpdateSummary:
    """Trading_Date 增量 / 全量更新摘要."""

    type_name: str
    rows_added: int
    elapsed_sec: float
    first_date: str | None
    last_date: str | None
    row_count: int


class Trading_DateService:
    """交易日历服务.

    用法:
        svc = Trading_DateService()
        svc.full_init(start="2010-01-01")          # 全量初始化
        svc.daily_update(today=date.today())       # 每日增量 (no-op when up-to-date)
    """

    def __init__(self, *, root: str | None = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = REGISTRY.get("Trading_Date")
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        if root:
            self.state = StateTracker(Path(root) / "datas" / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()
        # exchange_calendars 实例懒加载 (不与本地 cache 绑定, 仅作 source-of-truth 写入者)
        self._cal: ecals.ExchangeCalendar | None = None

    # ---------- 内部: XSHG 日历 ----------

    def _get_calendar(self) -> ecals.ExchangeCalendar:
        if self._cal is None:
            self._cal = ecals.get_calendar(DEFAULT_CALENDAR)
        return self._cal

    def _sessions_between(self, start: date, end: date) -> pd.DataFrame:
        """返回 [start, end] 闭区间内 XSHG sessions 的 DataFrame (单列 trading_date, ISO)."""
        cal = self._get_calendar()
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        if s > e:
            return pd.DataFrame(columns=["trading_date"], dtype="string")
        # XSHG 在 [start, end] 通常两端都是 session; 直接拉再裁
        sessions = cal.sessions_in_range(s, e)
        df = pd.DataFrame(
            {"trading_date": [d.date().isoformat() for d in sessions]},
            dtype="string",
        )
        return df

    # ---------- 全量 ----------

    def full_init(
        self,
        *,
        start: str = DEFAULT_FULL_INIT_START,
        today: date | None = None,
    ) -> UpdateSummary:
        """从 start 到 today 枚举 XSHG sessions, 合并已有 CSV, 写回.

        Idempotent: 重跑同一窗口, 行数与排序不变.
        """
        t0 = time.monotonic()
        if today is None:
            today = date.today()
        start_date = date.fromisoformat(start)

        new_df = self._sessions_between(start_date, today)
        logger.info(
            f"[trading-date full_init] XSHG sessions {start}..{today.isoformat()}: "
            f"{len(new_df)} rows"
        )

        # 读取已有 (如果有); 合并去重
        existing = self._read_existing()
        if existing is not None and not existing.empty:
            merged = pd.concat([existing, new_df], ignore_index=True)
        else:
            merged = new_df
        # 排序 + 去重 (按 trading_date)
        merged = (
            merged.drop_duplicates(subset=["trading_date"], keep="last")
            .sort_values("trading_date")
            .reset_index(drop=True)
        )
        rows_added = 0 if existing is None or existing.empty else (
            len(merged) - len(existing)
        )

        # 写入 (upsert ignore, 确保不会因为 start 之前已有数据而炸)
        if len(merged) == 0:
            # 极端情况: start..today 内无 session (例如未来), 跳过
            elapsed = time.monotonic() - t0
            return UpdateSummary(
                type_name="Trading_Date",
                rows_added=0,
                elapsed_sec=elapsed,
                first_date=None,
                last_date=None,
                row_count=0,
            )

        # 当 existing 与 new_df 都没有时, upsert 走 _write_atomic 分支
        if existing is None or existing.empty:
            self.file_.upsert(merged, conflict="ignore")
        else:
            # 已有数据: 用 replace 重建合并后 DataFrame (merged 已 dedup + 排序)
            self.file_.upsert(merged, conflict="replace")

        stats = self.file_.stats()
        self.state.update("Trading_Date", stats)
        elapsed = time.monotonic() - t0
        return UpdateSummary(
            type_name="Trading_Date",
            rows_added=rows_added,
            elapsed_sec=elapsed,
            first_date=stats.first_date,
            last_date=stats.last_date,
            row_count=stats.row_count,
        )

    # ---------- 增量 ----------

    def daily_update(
        self,
        *,
        today: date | None = None,
    ) -> UpdateSummary:
        """追加 (last_existing, today] 内 XSHG sessions. 已最新则 no-op."""
        t0 = time.monotonic()
        if today is None:
            today = date.today()

        existing = self._read_existing()
        if existing is None or existing.empty:
            # 无已有数据 → 退化为全量 (从 2010-01-01 起)
            logger.info(
                "[trading-date daily_update] no existing CSV, "
                f"falling back to full_init from {DEFAULT_FULL_INIT_START}"
            )
            return self.full_init(start=DEFAULT_FULL_INIT_START, today=today)

        last_str = str(existing["trading_date"].max())
        last_date = date.fromisoformat(last_str)
        if last_date >= today:
            # 已最新
            elapsed = time.monotonic() - t0
            stats = self.file_.stats()
            return UpdateSummary(
                type_name="Trading_Date",
                rows_added=0,
                elapsed_sec=elapsed,
                first_date=stats.first_date,
                last_date=stats.last_date,
                row_count=stats.row_count,
            )

        new_df = self._sessions_between(last_date + timedelta(days=1), today)
        if new_df.empty:
            elapsed = time.monotonic() - t0
            stats = self.file_.stats()
            return UpdateSummary(
                type_name="Trading_Date",
                rows_added=0,
                elapsed_sec=elapsed,
                first_date=stats.first_date,
                last_date=stats.last_date,
                row_count=stats.row_count,
            )

        self.file_.upsert(new_df, conflict="ignore")
        stats = self.file_.stats()
        self.state.update("Trading_Date", stats)
        elapsed = time.monotonic() - t0
        return UpdateSummary(
            type_name="Trading_Date",
            rows_added=len(new_df),
            elapsed_sec=elapsed,
            first_date=stats.first_date,
            last_date=stats.last_date,
            row_count=stats.row_count,
        )

    # ---------- helpers ----------

    def _read_existing(self) -> pd.DataFrame | None:
        if not self.file_.exists():
            return None
        try:
            df = self.file_.read()
        except FileNotFoundError:
            return None
        if df is None or df.empty or "trading_date" not in df.columns:
            return None
        return df[["trading_date"]].copy()
