"""交易日历 (A 股, 上交所).

基于 `exchange_calendars` 包, M1 固定用 XSHG (上交所) 日历.
提供统一的 API, 业务代码不直接接触 exchange_calendars.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

import exchange_calendars as ecals
import pandas as pd

# A 股收盘参考时间 (T+1 数据可用节点)
DEFAULT_CLOSE_HOUR = 16

# 默认 A 股日历 (上交所, 含深交所同步交易日)
DEFAULT_CALENDAR = "XSHG"

# 全局缓存, 单进程复用
_CAL_CACHE: dict[str, ecals.ExchangeCalendar] = {}


def get_calendar(name: str = DEFAULT_CALENDAR) -> ecals.ExchangeCalendar:
    """获取 exchange_calendars 对象 (缓存)."""
    if name not in _CAL_CACHE:
        _CAL_CACHE[name] = ecals.get_calendar(name)
    return _CAL_CACHE[name]


def is_trading_day(d: date, calendar: str = DEFAULT_CALENDAR) -> bool:
    """判断某日是否为交易日."""
    cal = get_calendar(calendar)
    ts = pd.Timestamp(d)
    return bool(cal.is_session(ts))


def next_trading_day(
    d: date, calendar: str = DEFAULT_CALENDAR, *, shift: int = 1
) -> date:
    """从 d 起 (含 d) 之后第 shift 个交易日 (shift=1 即下一个交易日).

    - d 是交易日: 返回 d 之后第一个交易日 (即下一天 bd)
    - d 不是交易日: 返回 d 之后第一个交易日
    - shift=k (k>0): 在上述基础上再向后跳 k-1 个交易日
    """
    if shift < 1:
        raise ValueError(f"shift 必须 >= 1, 得到 {shift}")
    cal = get_calendar(calendar)
    ts = pd.Timestamp(d)
    if cal.is_session(ts):
        # d 是交易日, "下一个" = next_session(d)
        nxt = cal.next_session(ts)
    else:
        # d 不是交易日, 用 sessions_in_range 找 ts 之后第一个 session
        end = ts + pd.Timedelta(days=60)
        future = cal.sessions_in_range(ts, end)
        if len(future) == 0:
            raise ValueError(f"在 {d} 之后 60 天内找不到交易日")
        nxt = future[0]
    for _ in range(shift - 1):
        nxt = cal.next_session(nxt)
    return nxt.date()


def prev_trading_day(
    d: date, calendar: str = DEFAULT_CALENDAR, *, shift: int = 1
) -> date:
    """从 d 起 (含 d) 之前第 shift 个交易日 (shift=1 即上一个交易日).

    - d 是交易日: 返回 d 之前第一个交易日
    - d 不是交易日: 返回 d 之前第一个交易日
    """
    if shift < 1:
        raise ValueError(f"shift 必须 >= 1, 得到 {shift}")
    cal = get_calendar(calendar)
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
    return prv.date()


def sessions_between(
    start: date, end: date, calendar: str = DEFAULT_CALENDAR
) -> list[date]:
    """[start, end] 闭区间内的所有交易日 (升序)."""
    cal = get_calendar(calendar)
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    if s > e:
        return []
    # 用 sessions_in_range (要求两端都是 session, 但允许稍宽区间)
    # 我们用 [start-1day, end] 拉宽一点, 再裁掉 < start 的
    wide_end = e
    sessions = cal.sessions_in_range(s, wide_end)
    return [d.date() for d in sessions if pd.Timestamp(start) <= d <= pd.Timestamp(end)]


def trading_days_in(
    dates: Iterable[date], calendar: str = DEFAULT_CALENDAR
) -> list[date]:
    """过滤一个日期列表, 只保留交易日 (保持输入顺序)."""
    cal = get_calendar(calendar)
    out = []
    for d in dates:
        if cal.is_session(pd.Timestamp(d)):
            out.append(d)
    return out


def align_to_trading_day(
    d: date, calendar: str = DEFAULT_CALENDAR, *, how: str = "next"
) -> date:
    """把任意日期对齐到最近的交易日.

    Args:
        how: 'next' (d 是交易日则返回 d, 否则下一个) /
             'prev' (d 是交易日则返回 d, 否则上一个) /
             'nearest' (取更近的一边, 平局时取 next)
    """
    cal = get_calendar(calendar)
    ts = pd.Timestamp(d)
    if cal.is_session(ts):
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
    # 当月最后一天
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    # 如果 last 是交易日直接返回, 否则找前一个交易日
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

    cal = get_calendar(calendar)
    ts_today = pd.Timestamp(today)

    if cal.is_session(ts_today):
        # 交易日: 看时间
        if now.time() >= time(close_hour, 0):
            return today
    # 非交易日或未到收盘 → 找前一个交易日
    return prev_trading_day(today, calendar)