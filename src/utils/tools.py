from __future__ import annotations

import pytz
from datetime import date, datetime, timedelta

shanghai_tz = pytz.timezone('Asia/Shanghai')

__all__ = [
    "check_stock_code",
    "is_stock_code",
    "is_trade_date",
    "check_trade_date",
    "is_time_point",
    "check_time_point",
    "parse_iso_date",
    "format_iso_date",
]


def parse_iso_date(s: str) -> date:
    """ISO 'YYYY-MM-DD' string → datetime.date.

    Internal-only helper used at the boundary when a callee genuinely
    needs arithmetic with ``timedelta``; the public signature still
    uses ``str``. Strict: invalid format raises ``ValueError``.
    """
    return date.fromisoformat(s)


def format_iso_date(d: date) -> str:
    """datetime.date → ISO 'YYYY-MM-DD' string.

    Internal-only formatter (reverse of :func:`parse_iso_date`). Use
    this (not inline ``.isoformat()``) so a future rule can audit /
    intercept the conversion in one place.
    """
    return d.isoformat()


def check_stock_code(code: str) -> str:
    if not is_stock_code(code):
        raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ 格式, 得到 {code!r}")
    return code


def is_stock_code(code: str) -> bool:
    if not isinstance(code, str) or len(code) != 9:
        return False
    if code[6] != "." or code[7:] not in ("SH", "SZ"):
        return False
    return True


def to_full_stock_code(code6: str) -> str:
    """
    6 位代码 → 9 字符 '600000.SH' / '000012.SZ'.
    6 开头 → 上海 (.SH); 0/3 开头 → 深圳 (.SZ).
    """
    code6 = str(code6).strip().zfill(6)
    if not code6.isdigit() or len(code6) != 6:
        raise ValueError(f"stock_code 必须是 6 位数字, 得到 {code6!r}")
    if code6[0] in ("6"):
        return f"{code6}.SH"
    if code6[0] in ("0", "3"):
        return f"{code6}.SZ"
    raise ValueError(f"无法识别交易所, stock_code={code6!r} (应为 6/0/3 开头)")


def check_trade_date(s: str) -> str:
    if not is_trade_date(s):
        raise ValueError(f"trade_date 必须是 ISO YYYY-MM-DD 格式, 得到 {s!r}")
    return s


def is_trade_date(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 10:
        return False
    if s[4] != "-" or s[7] != "-":
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


def check_time_point(s: str) -> str:
    if not is_time_point(s):
        raise ValueError(f"time_point 必须是 HH:MM 格式, 得到 {s!r}")
    return s


def is_time_point(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 5:
        return False
    if s[2] != ":":
        return False
    try:
        datetime.strptime(s, "%H:%M")
    except ValueError:
        return False
    return True


def now_date() -> str:
    return datetime.now(shanghai_tz).strftime("%Y-%m-%d")


def now_timpoint() -> str:
    return datetime.now(shanghai_tz).strftime("%H:%M")


def prev_date(d_str: str, shift=1) -> str:
    prev_d = date.fromisoformat(d_str) + timedelta(days=-shift)
    return prev_d.strftime("%Y-%m-%d")


def next_date(d_str: str, shift=1) -> str:
    next_d = date.fromisoformat(d_str) + timedelta(days=shift)
    return next_d.strftime("%Y-%m-%d")

