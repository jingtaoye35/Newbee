"""交易日历判断接口 (A 股, 上交所, 读侧).

默认从本地 `datas/Trading_Date.csv` (由 `Trading_DateService` 写入) 读取,
通过内存懒加载 + mtime 失效提供 O(1) 重复查询. 当查询日期超出本地缓存
范围, 或本地 CSV 缺失时, 自动兜底到 `exchange_calendars` (默认 XSHG)
并把远端结果合并回内存缓存.

业务代码不直接接触 exchange_calendars — 走本模块.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable

import exchange_calendars as ecals
import pandas as pd

from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.utils import logger

# A 股收盘参考时间 (T+1 数据可用节点)
DEFAULT_CLOSE_HOUR = 16

# 默认 A 股日历 (上交所, 与深交所同步交易日)
DEFAULT_CALENDAR = "XSHG"


# ---------- 本地缓存 ----------


@dataclass(frozen=True)
class LocalCacheEntry:
    """本地 CSV 缓存条目 (进程内, mtime 失效)."""

    mtime_ns: int
    size: int
    dates: tuple[date, ...]  # 升序
    as_set: frozenset[date]
    min_date: date
    max_date: date


# key = (root_path_str_or_None, calendar_name)
_LOCAL_CACHE: dict[tuple[str | None, str], LocalCacheEntry | None] = {}

# 仅用于 `get_calendar` 直通的 ExchangeCalendar 缓存
_CAL_CACHE: dict[str, ecals.ExchangeCalendar] = {}


def reload(calendar: str = DEFAULT_CALENDAR, *, root: str | None = None) -> None:
    """显式清空本地缓存. 下次判断调用会重新读 CSV."""
    key = (root, calendar)
    _LOCAL_CACHE.pop(key, None)


# ---------- 本地加载 ----------


def _csv_path(root: str | Path | None) -> Path:
    """解析本地 CSV 路径 (default root = DataFile 默认根)."""
    if root is None:
        # 与 DataFile 默认行为一致: 项目根 / storage_path
        from alpha_backend.datasource.storage.io import PROJECT_ROOT

        return PROJECT_ROOT / REGISTRY.get("Trading_Date").storage_path
    return Path(root) / REGISTRY.get("Trading_Date").storage_path


def _load_local(calendar: str = DEFAULT_CALENDAR, *, root: str | None = None) -> LocalCacheEntry | None:
    """读本地 CSV, 命中 mtime/size 时复用缓存. 缺失返回 None."""
    key = (root, calendar)
    cached = _LOCAL_CACHE.get(key, _SENTINEL)
    if cached is not _SENTINEL:
        return cached  # type: ignore[return-value]

    path = _csv_path(root)
    if not path.exists():
        _LOCAL_CACHE[key] = None
        return None

    stat = path.stat()
    mtime_ns = stat.st_mtime_ns
    size = stat.st_size

    # 检查是否需要重读
    existing_entry = _LOCAL_CACHE.get(key)
    if (
        existing_entry is not None
        and existing_entry.mtime_ns == mtime_ns
        and existing_entry.size == size
    ):
        return existing_entry

    df = pd.read_csv(path)
    if "trading_date" not in df.columns or df.empty:
        entry: LocalCacheEntry | None = None
    else:
        parsed = pd.to_datetime(df["trading_date"], format="ISO8601", errors="raise").dt.date
        unique_sorted = sorted(set(parsed.tolist()))
        if not unique_sorted:
            entry = None
        else:
            entry = LocalCacheEntry(
                mtime_ns=mtime_ns,
                size=size,
                dates=tuple(unique_sorted),
                as_set=frozenset(unique_sorted),
                min_date=unique_sorted[0],
                max_date=unique_sorted[-1],
            )
    _LOCAL_CACHE[key] = entry
    return entry


_SENTINEL: object = object()  # 与 None 区分: 未加载 vs 加载过但为空


# ---------- 远端兜底 ----------


def _resolve_remote(calendar: str) -> ecals.ExchangeCalendar:
    """拿 ExchangeCalendar; 失败时抛 RuntimeError 同时点名 CSV + calendar."""
    try:
        if calendar not in _CAL_CACHE:
            _CAL_CACHE[calendar] = ecals.get_calendar(calendar)
        return _CAL_CACHE[calendar]
    except Exception as e:
        from alpha_backend.datasource.storage.io import PROJECT_ROOT

        csv_path = PROJECT_ROOT / REGISTRY.get("Trading_Date").storage_path
        raise RuntimeError(
            f"交易日历远端兜底失败: calendar={calendar!r} (CSV: {csv_path}): {e}"
        ) from e


def _extend_local_cache(extra: Iterable[date], root: str | None = None) -> None:
    """把远端结果合并进内存缓存 (mtime 不变, dates 扩展)."""
    key = (root, DEFAULT_CALENDAR)
    entry = _LOCAL_CACHE.get(key)
    if entry is None:
        # 之前没有本地数据 — 直接构造一个不含 mtime 的临时条目
        new_dates = tuple(sorted(set(extra)))
        if not new_dates:
            return
        _LOCAL_CACHE[key] = LocalCacheEntry(
            mtime_ns=-1,  # -1 = 不绑定文件, 避免 stat 复用
            size=0,
            dates=new_dates,
            as_set=frozenset(new_dates),
            min_date=new_dates[0],
            max_date=new_dates[-1],
        )
        return
    merged = set(entry.dates) | set(extra)
    if not merged:
        return
    new_dates = tuple(sorted(merged))
    # 复用 entry, 只换 dates/as_set/min/max (mtime/size 保留)
    _LOCAL_CACHE[key] = LocalCacheEntry(
        mtime_ns=entry.mtime_ns,
        size=entry.size,
        dates=new_dates,
        as_set=frozenset(new_dates),
        min_date=new_dates[0],
        max_date=new_dates[-1],
    )


# ---------- ExchangeCalendar 直通 (供少数 ad-hoc 用户) ----------


def get_calendar(name: str = DEFAULT_CALENDAR) -> ecals.ExchangeCalendar:
    """获取 exchange_calendars 对象 (缓存). 仅在显式需要时使用."""
    return _resolve_remote(name)


# ---------- 判断 API (默认走本地, 远端兜底) ----------


def is_trading_day(d: date, calendar: str = DEFAULT_CALENDAR) -> bool:
    """判断某日是否为交易日."""
    if calendar == DEFAULT_CALENDAR:
        entry = _load_local(calendar)
        if entry is not None and entry.min_date <= d <= entry.max_date:
            return d in entry.as_set
    # 远端兜底
    cal = _resolve_remote(calendar)
    return bool(cal.is_session(pd.Timestamp(d)))


def _local_next(entry: LocalCacheEntry, d: date) -> date | None:
    """本地缓存中 d 之后第一个 session (含 d 时, d 的下一项)."""
    # bisect_right: d 不在时返回插入点, 在时返回 d 之后第一个位置
    idx = bisect.bisect_right(entry.dates, d)
    if idx < len(entry.dates):
        return entry.dates[idx]
    return None


def _local_prev(entry: LocalCacheEntry, d: date) -> date | None:
    """本地缓存中 d 之前第一个 session (含 d 时, d 的前一项)."""
    # bisect_left: d 不在时返回插入点, 在时返回 d 的位置
    idx = bisect.bisect_left(entry.dates, d) - 1
    if idx >= 0:
        return entry.dates[idx]
    return None


def _local_range(entry: LocalCacheEntry, start: date, end: date) -> list[date] | None:
    """本地 [start, end] 闭区间内的 sessions. 若区间在本地范围内则返回, 否则 None."""
    if start < entry.min_date or end > entry.max_date:
        return None
    lo = bisect.bisect_left(entry.dates, start)
    hi = bisect.bisect_right(entry.dates, end)
    return list(entry.dates[lo:hi])


def next_trading_day(
    d: date, calendar: str = DEFAULT_CALENDAR, *, shift: int = 1
) -> date:
    """从 d 起 (含 d) 之后第 shift 个交易日 (shift=1 即下一个交易日)."""
    if shift < 1:
        raise ValueError(f"shift 必须 >= 1, 得到 {shift}")

    if calendar == DEFAULT_CALENDAR:
        entry = _load_local(calendar)
        if entry is not None and d <= entry.max_date:
            cur = _local_next(entry, d)
            if cur is None:
                # d >= entry.max_date, 不在本地可达域内 → 远端兜底
                cal = _resolve_remote(calendar)
                ts = pd.Timestamp(d)
                if cal.is_session(ts):
                    cur_ts = cal.next_session(ts)
                else:
                    end = ts + pd.Timedelta(days=60)
                    future = cal.sessions_in_range(ts, end)
                    if len(future) == 0:
                        raise ValueError(f"在 {d} 之后 60 天内找不到交易日")
                    cur_ts = future[0]
                cur = cur_ts.date() if hasattr(cur_ts, "date") else cur_ts
            else:
                # 把第一个远端值也尝试扩展到本地 (让 bisect 后续更准)
                pass
            for _ in range(shift - 1):
                # 尽量走本地; 若已到本地边缘, 切换远端
                nxt = _local_next(entry, cur)
                if nxt is not None:
                    cur = nxt
                    continue
                cal = _resolve_remote(calendar)
                cur = cal.next_session(pd.Timestamp(cur)).date()
                _extend_local_cache([cur])
            return cur

    # 远端兜底全路径
    cal = _resolve_remote(calendar)
    ts = pd.Timestamp(d)
    if cal.is_session(ts):
        nxt = cal.next_session(ts)
    else:
        end = ts + pd.Timedelta(days=60)
        future = cal.sessions_in_range(ts, end)
        if len(future) == 0:
            raise ValueError(f"在 {d} 之后 60 天内找不到交易日")
        nxt = future[0]
    for _ in range(shift - 1):
        nxt = cal.next_session(nxt)
    return nxt.date() if hasattr(nxt, "date") else nxt


def prev_trading_day(
    d: date, calendar: str = DEFAULT_CALENDAR, *, shift: int = 1
) -> date:
    """从 d 起 (含 d) 之前第 shift 个交易日 (shift=1 即上一个交易日)."""
    if shift < 1:
        raise ValueError(f"shift 必须 >= 1, 得到 {shift}")

    if calendar == DEFAULT_CALENDAR:
        entry = _load_local(calendar)
        if entry is not None and d >= entry.min_date:
            cur = _local_prev(entry, d)
            if cur is None:
                # d <= entry.min_date, 远端兜底
                cal = _resolve_remote(calendar)
                ts = pd.Timestamp(d)
                if cal.is_session(ts):
                    cur = cal.previous_session(ts).date()
                else:
                    start = ts - pd.Timedelta(days=60)
                    past = cal.sessions_in_range(start, ts)
                    if len(past) == 0:
                        raise ValueError(f"在 {d} 之前 60 天内找不到交易日")
                    cur = (past[-1]).date() if hasattr(past[-1], "date") else past[-1]
            else:
                pass
            for _ in range(shift - 1):
                prv = _local_prev(entry, cur)
                if prv is not None:
                    cur = prv
                    continue
                cal = _resolve_remote(calendar)
                cur = cal.previous_session(pd.Timestamp(cur)).date()
                _extend_local_cache([cur])
            return cur

    # 远端兜底全路径
    cal = _resolve_remote(calendar)
    ts = pd.Timestamp(d)
    if cal.is_session(ts):
        prv = cal.previous_session(ts)
    else:
        start = ts - pd.Timedelta(days=60)
        past = cal.sessions_in_range(start, ts)
        if len(past) == 0:
            raise ValueError(f"在 {d} 之前 60 天内找不到交易日")
        prv = past[-1]
    for _ in range(shift - 1):
        prv = cal.previous_session(prv)
    return prv.date() if hasattr(prv, "date") else prv


def sessions_between(
    start: date, end: date, calendar: str = DEFAULT_CALENDAR
) -> list[date]:
    """[start, end] 闭区间内的所有交易日 (升序)."""
    if start > end:
        return []
    if calendar == DEFAULT_CALENDAR:
        entry = _load_local(calendar)
        if entry is not None:
            local = _local_range(entry, start, end)
            if local is not None:
                return local
    # 远端兜底 (区间越界 或 cache 空)
    cal = _resolve_remote(calendar)
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    sessions = cal.sessions_in_range(s, e)
    out = [d_.date() if hasattr(d_, "date") else d_ for d_ in sessions]
    if calendar == DEFAULT_CALENDAR and out:
        _extend_local_cache(out)
    return out


def trading_days_in(
    dates: Iterable[date], calendar: str = DEFAULT_CALENDAR
) -> list[date]:
    """过滤一个日期列表, 只保留交易日 (保持输入顺序)."""
    if calendar == DEFAULT_CALENDAR:
        entry = _load_local(calendar)
        if entry is not None:
            return [d for d in dates if entry.min_date <= d <= entry.max_date and d in entry.as_set]
    cal = _resolve_remote(calendar)
    return [d for d in dates if cal.is_session(pd.Timestamp(d))]


def align_to_trading_day(
    d: date, calendar: str = DEFAULT_CALENDAR, *, how: str = "next"
) -> date:
    """把任意日期对齐到最近的交易日.

    Args:
        how: 'next' (d 是交易日则返回 d, 否则下一个) /
             'prev' (d 是交易日则返回 d, 否则上一个) /
             'nearest' (取更近的一边, 平局时取 next)
    """
    if calendar == DEFAULT_CALENDAR:
        entry = _load_local(calendar)
        if entry is not None and entry.min_date <= d <= entry.max_date:
            if d in entry.as_set:
                return d
            if how == "next":
                nxt = _local_next(entry, d)
                if nxt is not None:
                    return nxt
            elif how == "prev":
                prv = _local_prev(entry, d)
                if prv is not None:
                    return prv
            elif how == "nearest":
                nxt = _local_next(entry, d)
                prv = _local_prev(entry, d)
                if nxt is None and prv is None:
                    pass  # fall through to remote
                else:
                    if nxt is None:
                        return prv  # type: ignore[misc]
                    if prv is None:
                        return nxt
                    d_next = (nxt - d).days
                    d_prev = (d - prv).days
                    return nxt if d_next <= d_prev else prv
            else:
                raise ValueError(f"how 必须是 'next'/'prev'/'nearest', 得到 {how!r}")

    # 远端兜底
    ts = pd.Timestamp(d)
    if calendar == DEFAULT_CALENDAR:
        entry2 = _load_local(calendar)
        if entry2 is not None and entry2.min_date <= ts.date() <= entry2.max_date:
            # 上面已经处理过 in-range 情况, 走到这说明 how == nearest 且 nxt/prv 都为 None
            # 不应发生, 但兜底
            pass
    if is_trading_day(d, calendar):
        return d
    if how == "next":
        return next_trading_day(d, calendar)
    if how == "prev":
        return prev_trading_day(d, calendar)
    if how == "nearest":
        nxt = next_trading_day(d, calendar)
        prv = prev_trading_day(d, calendar)
        d_next = (pd.Timestamp(nxt) - ts).days
        d_prev = (ts - pd.Timestamp(prv)).days
        return nxt if d_next <= d_prev else prv
    raise ValueError(f"how 必须是 'next'/'prev'/'nearest', 得到 {how!r}")


def add_business_days(d: date, n: int, calendar: str = DEFAULT_CALENDAR) -> date:
    """加 n 个交易日 (n 可负)."""
    if n == 0:
        return d
    sign = 1 if n > 0 else -1
    cur = d
    for _ in range(abs(n)):
        cur = next_trading_day(cur, calendar) if sign > 0 else prev_trading_day(cur, calendar)
    return cur


def month_end_trading_day(
    year: int, month: int, calendar: str = DEFAULT_CALENDAR
) -> date:
    """某月最后一个交易日 (cal 月末的自然日可能是交易日也可能不是)."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return align_to_trading_day(last, calendar, how="prev")


def latest_trading_day(
    today: date | None = None,
    *,
    now: datetime | None = None,
    close_hour: int = DEFAULT_CLOSE_HOUR,
    calendar: str = DEFAULT_CALENDAR,
) -> date:
    """返回"最近一个已收盘的交易日".

    规则:
    - 若 today 是交易日 且 当前时间 >= close_hour → 返回 today
    - 否则返回 today 之前的最近一个交易日

    Args:
        today: 基准日期 (默认今天)
        now: 当前时间 (默认 `datetime.now()`, 注入便于测试)
        close_hour: 收盘小时 (默认 16)
        calendar: 日历名

    Returns:
        date 对象
    """
    if today is None:
        today = date.today()
    if now is None:
        now = datetime.now()

    ts_today = pd.Timestamp(today)
    is_session = is_trading_day(today, calendar)
    if is_session and now.time() >= time(close_hour, 0):
        return today
    return prev_trading_day(today, calendar)


__all__ = [
    "DEFAULT_CALENDAR",
    "DEFAULT_CLOSE_HOUR",
    "get_calendar",
    "is_trading_day",
    "next_trading_day",
    "prev_trading_day",
    "sessions_between",
    "trading_days_in",
    "align_to_trading_day",
    "add_business_days",
    "month_end_trading_day",
    "latest_trading_day",
    "reload",
]
