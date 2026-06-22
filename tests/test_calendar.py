"""tests/test_calendar.py — trade-date calendar adapter + service helpers.

Buckets:
  1. Adapter fetch (mocked primary/fallback, empty-range short-circuit).
  2. Tool helpers against a fixture Trade_Date.csv (3-row, 2024-01-02..04).
  3. Refresh-after-update: full_init then is_trade_date reflects new rows.
  4. Out-of-range fetch fallback: between_trade_dates past last known triggers fetch.
  5. Forward horizon: daily_update pulls future sessions past today.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from utils.tools import format_iso_date, parse_iso_date

from datasource.adapter.calendar import (
    _fetch_calendar_sessions,
    fetch_sessions,
    fetch_trade_dates_baostock,
)
from datasource.service.trade_date import (
    _TRADE_CALENDER_SET_,
    _SORTED,
    TradeDateService,
    add_trade_dates,
    align_to_trade_date,
    is_trade_date,
    latest_trade_day,
    month_end_trade_day,
    next_trade_date,
    prev_trade_date,
    refresh_index,
    between_trade_dates,
    in_trade_dates,
)
from datasource.storage.io import DataFile, default_datasource_dir
from logger import logger


# ====================================================================
# Shared helpers
# ====================================================================


def _csv_path() -> Path:
    """Return the Trade_Date.csv path as resolved by default_datasource_dir()."""
    return default_datasource_dir() / "Trade_Date.csv"


def _write_csv(rows: list[str]) -> Path:
    """Write a Trade_Date.csv fixture and return its path."""
    p = _csv_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("trade_date\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def _setup_fixture(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[str],
    isolated_config: Path,
) -> None:
    """Write fixture CSV, reset module globals, reload index.

    ``isolated_config`` is the pytest fixture that loads the temp config;
    used here to reload the config singleton after ``_reset_index``
    teardown may have called ``reset_config()``.
    """
    from config import load_config, reset_config as _reset_cfg

    _reset_cfg()
    load_config(isolated_config)
    monkeypatch.setattr("datasource.service.trade_date._TRADE_CALENDER_SET_", None)
    monkeypatch.setattr("datasource.service.trade_date._SORTED", None)
    _write_csv(rows)
    refresh_index()


# ====================================================================
# Bucket 1 — Adapter fetch
# ====================================================================


class TestAdapterFetch:
    """fetch_sessions uses adapter-driven fallback: calendar primary + baostock fallback."""

    def test_happy_path_returns_calendar_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        expected = pd.DataFrame(
            {"trade_date": ["2024-01-02", "2024-01-03"]},
            dtype="string",
        )

        def mock_calendar(start, end, *, calendar="XSHG"):
            return expected

        def mock_bs(*, start, end):
            raise AssertionError("baostock should not be called when calendar succeeds")

        monkeypatch.setattr("datasource.adapter.calendar._fetch_calendar_sessions", mock_calendar)
        monkeypatch.setattr("datasource.adapter.calendar.fetch_trade_dates_baostock", mock_bs)
        result = fetch_sessions("2024-01-02", "2024-01-31")
        assert result is expected

    def test_calendar_primary_receives_iso_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_fetch_calendar_sessions is called with ISO date strings (no boundary parse)."""
        captured: dict = {}

        def recording_calendar(start, end, *, calendar="XSHG"):
            captured["start"] = start
            captured["end"] = end
            return pd.DataFrame(columns=["trade_date"], dtype="string")

        monkeypatch.setattr(
            "datasource.adapter.calendar._fetch_calendar_sessions", recording_calendar
        )
        monkeypatch.setattr(
            "datasource.adapter.calendar.fetch_trade_dates_baostock",
            lambda **kw: pd.DataFrame(columns=["trade_date"], dtype="string"),
        )
        fetch_sessions("2024-01-02", "2024-01-05")
        # 公共 API 统一 str 透传到 vendor adapter
        assert captured["start"] == "2024-01-02"
        assert captured["end"] == "2024-01-05"

    def test_start_after_end_returns_empty_no_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """start > end short-circuits to empty DataFrame without calling any adapter."""
        calendar_called = []

        def mock_calendar(start, end, *, calendar="XSHG"):
            calendar_called.append(True)
            return pd.DataFrame(columns=["trade_date"], dtype="string")

        monkeypatch.setattr("datasource.adapter.calendar._fetch_calendar_sessions", mock_calendar)
        result = fetch_sessions("2024-01-31", "2024-01-01")
        assert len(result) == 0
        assert "trade_date" in result.columns
        assert calendar_called == []

    def test_calendar_raises_triggers_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _fetch_calendar_sessions raises, fallback to baostock kicks in."""
        bs_calls = []

        def mock_calendar(start, end, *, calendar="XSHG"):
            raise RuntimeError("exchange_calendars is down")

        def mock_bs(*, start, end):
            bs_calls.append((start, end))
            return pd.DataFrame(
                {"trade_date": ["2024-01-02", "2024-01-03"]},
                dtype="string",
            )

        monkeypatch.setattr("datasource.adapter.calendar._fetch_calendar_sessions", mock_calendar)
        monkeypatch.setattr("datasource.adapter.calendar.fetch_trade_dates_baostock", mock_bs)
        result = fetch_sessions("2024-01-02", "2024-01-03")
        assert len(result) == 2
        assert list(result["trade_date"]) == ["2024-01-02", "2024-01-03"]
        assert len(bs_calls) == 1


# ====================================================================
# Bucket 2 — Tool helpers against fixture CSV
# ====================================================================


class TestToolHelpersFixture:
    """All helpers answer correctly from a fixture CSV (no network)."""

    @pytest.fixture(autouse=True)
    def _mock_fetch_sessions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Prevent _ensure_coverage from hitting the real exchange calendar."""
        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            lambda *a, **kw: pd.DataFrame(columns=["trade_date"], dtype="string"),
        )

    def test_is_trading_day_true(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert is_trade_date("2024-01-02") is True
        assert is_trade_date("2024-01-03") is True

    def test_is_trading_day_false(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert is_trade_date("2024-01-01") is False  # Sunday
        assert is_trade_date("2024-01-05") is False  # Friday (not in fixture)

    def test_next_trading_day(self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        # 2024-01-02 (Tue) → next is 2024-01-03 (Wed)
        assert next_trade_date("2024-01-02") == "2024-01-03"
        # shift=2 from 2024-01-02 → 2024-01-04
        assert next_trade_date("2024-01-02", shift=2) == "2024-01-04"
        # Non-trading past fixture end → ValueError (no extension via mock)
        with pytest.raises(ValueError, match="找不到交易日"):
            next_trade_date("2024-01-05")

    def test_prev_trading_day(self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        # 2024-01-04 (Thu) → prev is 2024-01-03 (Wed)
        assert prev_trade_date("2024-01-04") == "2024-01-03"
        # shift=2 from 2024-01-04 → 2024-01-02
        assert prev_trade_date("2024-01-04", shift=2) == "2024-01-02"

    def test_sessions_between(self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        result = between_trade_dates("2024-01-02", "2024-01-04")
        assert result == ["2024-01-02", "2024-01-03", "2024-01-04"]

    def test_sessions_between_empty_range(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert between_trade_dates("2024-01-04", "2024-01-02") == []
        assert between_trade_dates("2024-01-10", "2024-01-15") == []

    def test_trading_days_in(self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        dates = ["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-03"]
        result = in_trade_dates(dates)
        assert result == ["2024-01-02", "2024-01-03"]

    def test_align_to_trading_day_next(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert align_to_trade_date("2024-01-03", how="next") == "2024-01-03"
        assert align_to_trade_date("2024-01-02", how="next") == "2024-01-02"

    def test_align_to_trading_day_prev(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert align_to_trade_date("2024-01-05", how="prev") == "2024-01-04"

    def test_align_to_trading_day_nearest(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(
            monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-08"], isolated_config
        )
        # 2024-01-05 (Sat) not in fixture: next=01-08 (3 days), prev=01-04 (1 day) → prev wins
        assert align_to_trade_date("2024-01-05", how="nearest") == "2024-01-04"

    def test_add_business_days_positive(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert add_trade_dates("2024-01-02", 1) == "2024-01-03"
        assert add_trade_dates("2024-01-02", 2) == "2024-01-04"

    def test_add_business_days_negative(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert add_trade_dates("2024-01-04", -1) == "2024-01-03"
        assert add_trade_dates("2024-01-04", -2) == "2024-01-02"

    def test_add_business_days_zero(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        assert add_trade_dates("2024-01-03", 0) == "2024-01-03"

    def test_month_end_trading_day(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        result = month_end_trade_day(2024, 1)
        # Jan 31 2024 is Wednesday; not in fixture, falls back to last fixture
        assert isinstance(result, str)
        assert result <= "2024-01-31"

    def test_latest_trading_day_after_close(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)
        tuesday_5pm = datetime(2024, 1, 2, 17, 0, 0)
        result = latest_trade_day(today="2024-01-02", now=tuesday_5pm, close_hour=16)
        assert result == "2024-01-02"

    def test_latest_trading_day_before_close(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(
            monkeypatch, ["2023-12-29", "2024-01-02", "2024-01-03", "2024-01-04"], isolated_config
        )
        tuesday_2pm = datetime(2024, 1, 2, 14, 0, 0)
        result = latest_trade_day(today="2024-01-02", now=tuesday_2pm, close_hour=16)
        # Before close → returns previous trading day (2023-12-29)
        assert result == "2023-12-29"


# ====================================================================
# Bucket 3 — Refresh after full_init
# ====================================================================


class TestRefreshAfterUpdate:
    """After full_init adds rows, helper calls see the new sessions."""

    def test_full_init_then_is_trading_day(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)

        new_sessions = pd.DataFrame(
            {"trade_date": ["2024-01-05", "2024-01-08"]},
            dtype="string",
        )

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            return new_sessions

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        svc = TradeDateService()
        svc.full_init(start="2024-01-01", today="2024-01-10")

        csv_p = _csv_path()
        df = pd.read_csv(csv_p)
        assert len(df) == 5

        # is_trade_date for newly added dates returns True
        assert is_trade_date("2024-01-05") is True
        assert is_trade_date("2024-01-08") is True

    def test_daily_update_then_next_trading_day(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)

        new_sessions = pd.DataFrame(
            {"trade_date": ["2024-01-05"]},
            dtype="string",
        )

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            return new_sessions

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        svc = TradeDateService()
        svc.daily_update(today="2024-01-10")

        # next_trade_date("2024-01-04") should return "2024-01-05"
        assert next_trade_date("2024-01-04") == "2024-01-05"


# ====================================================================
# Bucket 4 — Out-of-range fetch fallback
# ====================================================================


class TestOutOfRangeFallback:
    """between_trade_dates past last known session triggers fetch_sessions."""

    def test_sessions_between_extends_index(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)

        future_sessions = pd.DataFrame(
            {"trade_date": ["2026-01-05", "2026-01-08"]},
            dtype="string",
        )
        fetch_calls = []

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            fetch_calls.append((start, end))
            return future_sessions

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        result = between_trade_dates("2026-01-01", "2026-01-31")

        # fetch_sessions should have been called once
        assert len(fetch_calls) == 1
        assert fetch_calls[0][0] == "2024-01-05"  # last_known + 1 day
        assert fetch_calls[0][1] == "2026-01-31"

        # Result should include both the mocked future sessions
        assert result == ["2026-01-05", "2026-01-08"]

        # The CSV should now also have the new rows
        csv_p = _csv_path()
        df = pd.read_csv(csv_p)
        assert set(df["trade_date"].tolist()) == {
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2026-01-05",
            "2026-01-08",
        }

    def test_is_trading_day_out_of_range_triggers_fetch(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)

        future_sessions = pd.DataFrame(
            {"trade_date": ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09"]},
            dtype="string",
        )
        fetch_calls = []

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            fetch_calls.append((start, end))
            return future_sessions

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        # is_trade_date past last known triggers _ensure_coverage
        assert is_trade_date("2026-01-05") is True
        assert is_trade_date("2026-01-06") is True  # in returned sessions
        assert is_trade_date("2026-01-08") is True
        assert len(fetch_calls) == 1


# ====================================================================
# Bucket 5 — Forward horizon
# ====================================================================


class TestForwardHorizon:
    """daily_update and full_init fetch past today up to forward_horizon_days."""

    def test_daily_update_pulls_future_sessions(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)

        future_sessions = pd.DataFrame(
            {"trade_date": ["2024-01-05", "2024-01-08", "2024-01-12"]},
            dtype="string",
        )
        fetch_calls = []

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            fetch_calls.append((start, end))
            return future_sessions

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        svc = TradeDateService()
        svc.forward_horizon_days = 30  # today=2024-01-10 + 30 = 2024-02-09
        result = svc.daily_update(today="2024-01-10")

        # Verify fetch was called with end = today + 30 days
        assert len(fetch_calls) == 1
        assert fetch_calls[0][1] == "2024-02-09"

        # Verify CSV now contains the future sessions
        csv_p = _csv_path()
        df = pd.read_csv(csv_p)
        assert "2024-01-05" in df["trade_date"].values
        assert "2024-01-08" in df["trade_date"].values
        assert "2024-01-12" in df["trade_date"].values

        # Verify the UpdateSummary reflects the added rows
        assert result.rows_added == 3
        assert result.last_date == "2024-01-12"

    def test_full_init_pulls_future_sessions(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        _setup_fixture(monkeypatch, ["2024-01-02", "2024-01-03", "2024-01-04"], isolated_config)

        future_sessions = pd.DataFrame(
            {"trade_date": ["2024-01-05", "2024-01-08", "2024-01-12"]},
            dtype="string",
        )
        fetch_calls = []

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            fetch_calls.append((start, end))
            return future_sessions

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        svc = TradeDateService()
        svc.forward_horizon_days = 30
        svc.full_init(start="2024-01-01", today="2024-01-10")

        # Verify fetch end = today + 30
        assert len(fetch_calls) == 1
        assert fetch_calls[0][1] == "2024-02-09"

        # Verify all 3 future sessions are in the CSV
        csv_p = _csv_path()
        df = pd.read_csv(csv_p)
        assert "2024-01-05" in df["trade_date"].values

    def test_up_to_date_short_circuit_no_fetch(
        self, monkeypatch: pytest.MonkeyPatch, isolated_config: Path
    ) -> None:
        """When last CSV date >= today, daily_update is a no-op (no fetch)."""
        _setup_fixture(
            monkeypatch,
            ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-10"],
            isolated_config,
        )

        fetch_calls = []

        def mock_fetch_sessions(start, end, *, calendar="XSHG"):
            fetch_calls.append((start, end))
            return pd.DataFrame(columns=["trade_date"], dtype="string")

        monkeypatch.setattr(
            "datasource.service.trade_date.fetch_sessions",
            mock_fetch_sessions,
        )

        svc = TradeDateService()
        result = svc.daily_update(today="2024-01-10")

        # No fetch should have been made
        assert fetch_calls == []
        assert result.rows_added == 0


# ====================================================================
# Bucket 6 — Import surface verification
# ====================================================================


class TestImportSurface:
    """Verify the public import paths are correct."""

    def test_dataset_re_exports_all_tools(self) -> None:
        """datasource.dataset exports all 9 tool functions."""
        import datasource.dataset as ds

        expected = [
            "is_trade_date",
            "next_trade_date",
            "prev_trade_date",
            "between_trade_dates",
            "in_trade_dates",
            "align_to_trade_date",
            "add_trade_dates",
            "month_end_trade_day",
            "latest_trade_day",
        ]
        for name in expected:
            assert hasattr(ds, name), f"datasource.dataset missing {name}"
            assert callable(getattr(ds, name))

    def test_adapter_removed_helpers_raise_import_error(self) -> None:
        """Removed helpers from adapter.calendar raise ImportError on from-import."""
        for name in (
            "is_trade_date",
            "next_trade_date",
            "prev_trade_date",
            "between_trade_dates",
            "in_trade_dates",
            "align_to_trade_date",
            "add_trade_dates",
            "month_end_trade_day",
            "latest_trade_day",
        ):
            with pytest.raises(ImportError):
                exec(f"from datasource.adapter.calendar import {name}")
