from __future__ import annotations

from enum import StrEnum

__all__ = ["DataAdapter"]


class DataAdapter(StrEnum):
    Akshare = "akshare"
    Baostock = "baostock"
    ExchangeCalendar = "exchange_calendar"
