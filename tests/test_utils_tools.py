"""tests/test_utils_tools.py — coverage for src/utils/tools.py helpers."""

from __future__ import annotations

from datetime import date

import pytest

from utils.tools import (
    check_trade_date,
    format_iso_date,
    is_trade_date,
    parse_iso_date,
)


# ---- parse_iso_date ----


def test_parse_iso_date_valid() -> None:
    assert parse_iso_date("2024-01-10") == date(2024, 1, 10)


def test_parse_iso_date_leap_day() -> None:
    assert parse_iso_date("2024-02-29") == date(2024, 2, 29)


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "2024/01/10",  # wrong separator
        "24-01-10",  # 2-digit year
        "2024-1-10",  # 1-digit month
        "2024-01-10T00:00:00",  # datetime form
        "not-a-date",
    ],
)
def test_parse_iso_date_invalid_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_iso_date(bad)


# ---- format_iso_date ----


def test_format_iso_date_roundtrip() -> None:
    assert format_iso_date(date(2024, 1, 10)) == "2024-01-10"


def test_format_iso_date_leap_day() -> None:
    assert format_iso_date(date(2024, 2, 29)) == "2024-02-29"


def test_parse_format_roundtrip() -> None:
    original = "2024-12-31"
    assert format_iso_date(parse_iso_date(original)) == original


# ---- Existing is_trade_date / check_trade_date stay unchanged ----


def test_is_trade_date_valid() -> None:
    assert is_trade_date("2024-01-10") is True


def test_is_trade_date_invalid() -> None:
    assert is_trade_date("2024/01/10") is False
    assert is_trade_date("") is False


def test_check_trade_date_passes() -> None:
    assert check_trade_date("2024-01-10") == "2024-01-10"


def test_check_trade_date_raises() -> None:
    with pytest.raises(ValueError):
        check_trade_date("not-a-date")
