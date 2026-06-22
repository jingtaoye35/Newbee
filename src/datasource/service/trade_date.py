from __future__ import annotations

import bisect
import threading
import time as _time_module
import pandas as pd

from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional, Set, List


from datasource.schema.trade_date import TradeDate
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker
from logger import logger
from common import DataAdapter
from utils.tools import parse_iso_date, now_date, next_date

DEFAULT_FULL_INIT_START = "2010-01-01"

@dataclass
class UpdateSummary:
    """Trade_Date 增量 / 全量更新摘要."""
    type_name: str
    rows_added: int
    elapsed_sec: float
    first_date: Optional[str]
    last_date: Optional[str]
    row_count: int


class TradeDateService:
    """
    交易日历服务, 用法:
        svc = TradeDateService()
        svc.full_init(start="2010-01-01")          # 全量初始化
        svc.daily_update(today=date.today())       # 每日增量 (no-op when up-to-date)
    """

    forward_horizon_days: int = 20

    def __init__(self, *, root: Optional[str] = None):
        self.root = Path(root) if root else None
        self.dtype = TradeDate
        self.file_ = DataFile(self.dtype, root=self.root) if self.root else DataFile(self.dtype)
        if self.root:
            self.state = StateTracker(self.file_.path.parent / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()  # 走 default_state_path() → datasource_dir/_Manifest/

    def _fetch_end(self, today: str) -> str:
        return next_date(today, self.forward_horizon_days)

    def _sessions_between(self, start: str, end: str) -> pd.DataFrame:
        if start > end:
            return pd.DataFrame(columns=["trade_date"], dtype="string")

        last_err: Optional[BaseException] = None

        for adapter_type in TradeDate.adapters:
            if adapter_type == DataAdapter.ExchangeCalendar:
                try:
                    from datasource.adapter.exchange_calendar import fetch_calendar_sessions
                    result = fetch_calendar_sessions(start=start, end=end)
                except Exception as e:
                    logger.error(f"[{adapter_type}] [Trade_Date] source failed: {e!r}, try next")
                    last_err = e
                    continue

            elif adapter_type == DataAdapter.Baostock:
                try:
                    from datasource.adapter.baostock import fetch_trade_dates_baostock
                    result = fetch_trade_dates_baostock(start=start, end=end)
                except Exception as e:
                    logger.error(f"[{adapter_type}] [Trade_Date] source failed: {e!r}, try next")
                    last_err = e
                    continue

            else:
                logger.error(f"used unknown [{adapter_type}] [Trade_Date]")
                last_err = RuntimeError(f"{adapter_type} [Trade_Date] used unknown")
                continue

            return result

        raise RuntimeError(
            f"Trade_Date fetch failed with all adapters {TradeDate.adapters}: last_err={last_err!r}"
        ) from last_err 


    def full_init(self, *, start: str = DEFAULT_FULL_INIT_START, today: Optional[str] = None) -> UpdateSummary:
        t0 = _time_module.monotonic()
        if today is None:
            today = now_date()
        fetch_end = self._fetch_end(today)
        new_df = self._sessions_between(start, fetch_end)
        
        logger.info(f"[TradeDate full_init] XSHG sessions {start}..{fetch_end}: {len(new_df)} rows")

        # 读取已有 (如果有); 合并去重
        existing = self._read_existing()
        if existing is not None and not existing.empty:
            merged = pd.concat([existing, new_df], ignore_index=True)
        else:
            merged = new_df
        # 排序 + 去重 (按 trade_date)
        merged = (
            merged.drop_duplicates(subset=["trade_date"], keep="last")
            .sort_values("trade_date")
            .reset_index(drop=True)
        )
        rows_added = 0 if existing is None or existing.empty else (len(merged) - len(existing))

        # 写入 (upsert ignore, 确保不会因为 start 之前已有数据而炸)
        if len(merged) == 0:
            elapsed = _time_module.monotonic() - t0
            return UpdateSummary(
                type_name="TradeDate",
                rows_added=0,
                elapsed_sec=elapsed,
                first_date=None,
                last_date=None,
                row_count=0,
            )

        if existing is None or existing.empty:
            self.file_.upsert(merged, conflict="ignore")
        else:
            self.file_.upsert(merged, conflict="replace")

        stats = self.file_.stats()
        self.state.update("TradeDate", stats)
        refresh_index(root=self.root)
        elapsed = _time_module.monotonic() - t0
        return UpdateSummary(
            type_name="TradeDate",
            rows_added=rows_added,
            elapsed_sec=elapsed,
            first_date=stats.first_date,
            last_date=stats.last_date,
            row_count=stats.row_count,
        )


    def daily_update(self, *, today: Optional[str] = None) -> UpdateSummary:
        t0 = _time_module.monotonic()
        if today is None:
            today = now_date()

        existing = self._read_existing()
        if existing is None or existing.empty:
            logger.info(
                f"[TradeDate daily_update] no existing CSV, falling back to full_init from {DEFAULT_FULL_INIT_START}"
            )
        return self.full_init()

        last_date = str(existing["trade_date"].max())
        if last_date >= today:
            elapsed = _time_module.monotonic() - t0
            stats = self.file_.stats()
            return UpdateSummary(
                type_name="TradeDate",
                rows_added=0,
                elapsed_sec=elapsed,
                first_date=stats.first_date,
                last_date=stats.last_date,
                row_count=stats.row_count,
            )

        fetch_end = self._fetch_end(today)
        new_df = self._sessions_between(next_date(last_date, 1), fetch_end)
        if new_df.empty:
            elapsed = _time_module.monotonic() - t0
            stats = self.file_.stats()
            return UpdateSummary(
                type_name="TradeDate",
                rows_added=0,
                elapsed_sec=elapsed,
                first_date=stats.first_date,
                last_date=stats.last_date,
                row_count=stats.row_count,
            )

        self.file_.upsert(new_df, conflict="ignore")
        stats = self.file_.stats()
        self.state.update("TradeDate", stats)
        refresh_index(root=self.root)
        elapsed = _time_module.monotonic() - t0
        return UpdateSummary(
            type_name="TradeDate",
            rows_added=len(new_df),
            elapsed_sec=elapsed,
            first_date=stats.first_date,
            last_date=stats.last_date,
            row_count=stats.row_count,
        )


    def _read_existing(self) -> pd.DataFrame | None:
        if not self.file_.exists():
            return None
        try:
            df = self.file_.read()
        except FileNotFoundError:
            return None
        if df is None or df.empty or "trade_date" not in df.columns:
            return None
        return df[["trade_date"]].copy()


_TRADE_CALENDER_SET_: Optional[Set[str]] = None
_TRADE_CALENDER_: Optional[List[str]] = None
_refresh_lock = threading.Lock()


def refresh_index(*, root: Optional[Path] = None) -> None:
    global _TRADE_CALENDER_SET_, _TRADE_CALENDER_
    with _refresh_lock:
        file_ = DataFile(TradeDate, root=root)
        if not file_.exists():
            _TRADE_CALENDER_SET_ = set()
            _TRADE_CALENDER_ = []
            return
        try:
            df = file_.read()
        except FileNotFoundError:
            _TRADE_CALENDER_SET_ = set()
            _TRADE_CALENDER_ = []
            return
        if df is None or df.empty or "trade_date" not in df.columns:
            _TRADE_CALENDER_SET_ = set()
            _TRADE_CALENDER_ = []
            return
        dates = df["trade_date"].dropna().astype(str).unique().tolist()
        _TRADE_CALENDER_SET_ = set(dates)
        _TRADE_CALENDER_ = sorted(dates)


def _ensure_loaded():
    if _TRADE_CALENDER_ is None or _TRADE_CALENDER_SET_ is None:
        refresh_index()


def _ensure_coverage(end: str):
    global _TRADE_CALENDER_SET_
    global _TRADE_CALENDER_

    if _TRADE_CALENDER_ is None or len(_TRADE_CALENDER_) == 0:
        return

    last_known = _TRADE_CALENDER_[-1]
    if end <= last_known:
        return

    with _refresh_lock:
        if _TRADE_CALENDER_ is not None and len(_TRADE_CALENDER_) > 0 and end <= _TRADE_CALENDER_[-1]:
            return
        fetch_start = next_date(_TRADE_CALENDER_[-1], 1)
        new_df = fetch_sessions(start=fetch_start, end=end)
        if new_df.empty or "trade_date" not in new_df.columns:
            return
        new_dates = new_df["trade_date"].dropna().astype(str).tolist()
        new_dates = [d for d in new_dates if d not in _TRADE_CALENDER_SET_]
        if not new_dates:
            return
        # Append to CSV so refresh_index() (called later) sees them.
        file_ = DataFile(TradeDate)
        append_df = pd.DataFrame({"trade_date": new_dates}, dtype="string")
        file_.upsert(append_df, conflict="ignore")
        _TRADE_CALENDER_SET_.update(new_dates)
        _TRADE_CALENDER_ = sorted(_TRADE_CALENDER_SET_)


def is_trade_date(d: str) -> bool:
    _ensure_loaded()
    if d in _TRADE_CALENDER_SET_:
        return True

    if _TRADE_CALENDER_ is not None and len(_TRADE_CALENDER_) > 0 and d > _TRADE_CALENDER_[-1]:
        _ensure_coverage(next_date(d, 30))
    return d in _TRADE_CALENDER_SET_

def next_trade_date(d: str, *, shift: int = 1) -> str:
    """
    从 d 起 (含 d) 之后第 shift 个交易日 (shift=1 即下一个交易日)
    - d 是交易日: 返回 d 之后第一个交易日
    - d 不是交易日: 返回 d 之后第一个交易日
    - shift=k (k>0): 在上述基础上再向后跳 k-1 个交易日
    """
    if shift < 1:
        raise ValueError(f"shift 必须 >= 1, 得到 {shift}")
    _ensure_loaded()
    _ensure_coverage_for_next(d, shift)
    idx = bisect.bisect_right(_TRADE_CALENDER_, d)  # type: ignore[arg-type]
    if idx >= len(_TRADE_CALENDER_):  # type: ignore[arg-type]
        raise ValueError(f"在 {d} 之后找不到交易日 (shift={shift})")
    result_idx = idx + shift - 1
    if result_idx >= len(_TRADE_CALENDER_):  # type: ignore[arg-type]
        raise ValueError(
            f"在 {d} 之后找不到第 {shift} 个交易日 (index {result_idx} >= {len(_TRADE_CALENDER_)})"  # type: ignore[arg-type]
        )
    return _TRADE_CALENDER_[result_idx]  # type: ignore[index]


def _ensure_coverage_for_next(d: str, shift: int) -> None:
    """Ensure the index covers at least ``shift`` sessions after ``d``."""
    if _TRADE_CALENDER_ is None or len(_TRADE_CALENDER_) == 0:
        return
    idx = bisect.bisect_right(_TRADE_CALENDER_, d)
    needed = idx + shift
    if needed <= len(_TRADE_CALENDER_):
        return

    _ensure_coverage(
        next_date(_TRADE_CALENDER_[-1], 30 + shift)
    )


def prev_trade_date(d: str, *, shift: int = 1) -> str:
    """
    从 d 起 (含 d) 之前第 shift 个交易日 (shift=1 即上一个交易日)
    - d 是交易日: 返回 d 之前第一个交易日
    - d 不是交易日: 返回 d 之前第一个交易日
    """
    if shift < 1:
        raise ValueError(f"shift 必须 >= 1, 得到 {shift}")
    _ensure_loaded()
    _ensure_coverage_for_prev(d, shift)
    idx = bisect.bisect_left(_TRADE_CALENDER_, d)  # type: ignore[arg-type]
    if idx == 0:
        raise ValueError(f"在 {d} 之前找不到交易日")
    result_idx = idx - shift
    if result_idx < 0:
        raise ValueError(f"在 {d} 之前找不到第 {shift} 个交易日 (index {result_idx} < 0)")
    return _TRADE_CALENDER_[result_idx]  # type: ignore[index]


def _ensure_coverage_for_prev(d: str, shift: int) -> None:
    """Ensure the index covers at least ``shift`` sessions before ``d``."""
    if _TRADE_CALENDER_ is None or len(_TRADE_CALENDER_) == 0:
        return
    idx = bisect.bisect_left(_TRADE_CALENDER_, d)
    needed = shift
    if idx >= needed:
        return
    # Need earlier sessions — extend backwards.
    # Since full_init starts from 2010-01-01, this should rarely trigger.
    # Fetch a wide window; the fetch will return whatever the vendor has.
    if len(_TRADE_CALENDER_) > 0:
        _ensure_coverage(next_date(d, 30))


def between_trade_dates(start: str, end: str) -> list[str]:
    _ensure_loaded()
    if start > end:
        return []
    _ensure_coverage(end)
    if _TRADE_CALENDER_ is None or len(_TRADE_CALENDER_) == 0:
        return []
    lo = bisect.bisect_left(_TRADE_CALENDER_, start)
    hi = bisect.bisect_right(_TRADE_CALENDER_, end)
    return list(_TRADE_CALENDER_[lo:hi])


def in_trade_dates(dates: list[str]) -> list[str]:
    """过滤一个日期列表, 只保留交易日 (保持输入顺序)."""
    _ensure_loaded()
    out = [d for d in dates if d in _TRADE_CALENDER_SET_]  # type: ignore[operator]
    return out


def align_to_trade_date(d: str, *, how: str = "next") -> str:
    """把任意日期 (ISO 字符串) 对齐到最近的交易日.

    Args:
        how: 'next' (d 是交易日则返回 d, 否则下一个) /
             'prev' (d 是交易日则返回 d, 否则上一个) /
             'nearest' (取更近的一边, 平局时取 next)
    """
    _ensure_loaded()
    if d in _TRADE_CALENDER_SET_:  # type: ignore[operator]
        return d
    if how == "next":
        return next_trade_date(d)
    if how == "prev":
        return prev_trade_date(d)
    if how == "nearest":
        nxt_str = next_trade_date(d)
        prv_str = prev_trade_date(d)
        nxt_date = parse_iso_date(nxt_str)
        prv_date = parse_iso_date(prv_str)
        d_date = parse_iso_date(d)
        d_next = (nxt_date - d_date).days
        d_prev = (d_date - prv_date).days
        return nxt_str if d_next <= d_prev else prv_str
    raise ValueError(f"how 必须是 'next'/'prev'/'nearest', 得到 {how!r}")


def add_trade_dates(d: str, n: int) -> str:
    """加 n 个交易日 (n 可负)."""
    if n == 0:
        return d
    sign = 1 if n > 0 else -1
    cur = d
    for _ in range(abs(n)):
        cur = next_trade_date(cur) if sign > 0 else prev_trade_date(cur)
    return cur


def month_end_trade_day(year: int, month: int) -> str:
    """某月最后一个交易日 (月末的自然日可能是交易日也可能不是)."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return align_to_trade_date(last.isoformat(), how="prev")


def latest_trade_day(
    today: Optional[str] = None,
    *,
    now: datetime | None = None,
    close_hour: int = 16,
) -> str:
    """返回"最近一个已收盘的交易日".

    规则:
    - 若 today 是交易日 且 当前时间 >= close_hour → 返回 today
    - 否则返回 today 之前的最近一个交易日
    """
    if today is None:
        today = date.today().isoformat()
    if now is None:
        now = datetime.now()

    if is_trade_date(today):
        if now.time() >= dt_time(close_hour, 0):
            return today
    return prev_trade_date(today)


# ---------- 导出 ----------

__all__ = [
    "is_trade_date",
    "next_trade_date",
    "prev_trade_date",
    "between_trade_dates",
    "in_trade_dates",
    "align_to_trade_date",
    "add_trade_dates",
    "month_end_trade_day",
    "latest_trade_day",
    "refresh_index",
    "UpdateSummary",
]
