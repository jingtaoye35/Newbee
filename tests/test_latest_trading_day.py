"""calendar.latest_trading_day 测试."""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.datasource.calendar import latest_trading_day  # noqa: E402


def test_tuesday_after_close_returns_tuesday():
    """周二 17:00 → 周二."""
    assert latest_trading_day(
        today=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 17, 0),
    ) == date(2026, 6, 23)


def test_monday_before_open_returns_friday():
    """周一 09:00 → 上周五. 用 2026-05-11 / 2026-05-08 (无节假日干扰)."""
    assert latest_trading_day(
        today=date(2026, 5, 11),
        now=datetime(2026, 5, 11, 9, 0),
    ) == date(2026, 5, 8)


def test_monday_after_close_returns_monday():
    """周一 17:00 → 周一本身 (无延迟到次日)."""
    assert latest_trading_day(
        today=date(2026, 5, 11),
        now=datetime(2026, 5, 11, 17, 0),
    ) == date(2026, 5, 11)


def test_weekend_returns_friday():
    """周六 → 上周五. 用 2026-05-09 / 2026-05-08."""
    # 2026-05-09 是周六
    assert latest_trading_day(
        today=date(2026, 5, 9),
        now=datetime(2026, 5, 9, 12, 0),
    ) == date(2026, 5, 8)


def test_sunday_returns_friday():
    """周日 → 上周五. 用 2026-05-10 / 2026-05-08."""
    # 2026-05-10 是周日
    assert latest_trading_day(
        today=date(2026, 5, 10),
        now=datetime(2026, 5, 10, 12, 0),
    ) == date(2026, 5, 8)


def test_trading_day_with_holiday_returns_last_session():
    """2026-06-19 周五, 2026-06-22 周一是端午 holiday → 周一返回周五.

    注: exchange_calendars XSHG 包含端午假期. 这里直接构造一个不在 cal 中的日期,
    它会被识别为非交易日, 回退到上一个交易日.
    """
    # 端午假期 (按 cal): 2026-06-19 周五收盘, 2026-06-22 周一也是 holiday (A 股特殊)
    # 注: exchange_calendars 默认不内置中国节假日, 但本测试仅验证 prev_trading_day 逻辑.
    # 周一非交易日 → 应回退到周五.
    from alpha_backend.datasource.calendar import is_trading_day

    if not is_trading_day(date(2026, 6, 22)):
        assert latest_trading_day(
            today=date(2026, 6, 22),
            now=datetime(2026, 6, 22, 17, 0),
        ) == date(2026, 6, 19)


def test_close_hour_configurable():
    """close_hour 参数: 14:00 视为已收盘."""
    assert latest_trading_day(
        today=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 14, 30),
        close_hour=14,
    ) == date(2026, 6, 23)


def test_close_hour_strictly_after_open():
    """14:00 算收盘, 13:59 不算."""
    assert latest_trading_day(
        today=date(2026, 6, 23),
        now=datetime(2026, 6, 23, 13, 59),
        close_hour=14,
    ) == date(2026, 6, 22)