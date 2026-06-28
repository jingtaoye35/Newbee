"""test_state_tracker.py — Data_State.json + StateTracker 测试."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.datasource.storage.errors import SchemaVersionError, StateCorruptedError
from alpha_backend.datasource.storage.io import CoverageStats, DataFile
from alpha_backend.datasource.storage.state import (
    DEFAULT_RESUME_START,
    StateTracker,
)


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "datas" / "_Manifest" / "Data_State.json"


@pytest.fixture
def tracker(state_path: Path) -> StateTracker:
    return StateTracker(state_path)


def _make_stats(type_name: str = "KData", **overrides) -> CoverageStats:
    base = dict(
        type_name=type_name,
        schema_version="1.0",
        frequency="daily",
        first_date="2024-01-02",
        last_date="2024-06-19",
        row_count=6000,
        stock_count=1000,
        file_size_bytes=1024,
        file_sha256="abcdef0123456789",
        updated_at="2024-06-19T12:00:00+00:00",
    )
    base.update(overrides)
    return CoverageStats(**base)


# ---------- read on missing ----------


def test_read_on_missing_returns_empty(tracker: StateTracker) -> None:
    assert tracker.read() == {}


def test_read_full_on_missing_returns_default(tracker: StateTracker) -> None:
    full = tracker.read_full()
    assert full["version"] == "1.0"
    assert full["types"] == {}
    assert full["universe_sha"] is None


# ---------- update + round-trip ----------


def test_update_creates_file(tracker: StateTracker, state_path: Path) -> None:
    assert not state_path.exists()
    tracker.update("KData", _make_stats())
    assert state_path.exists()


def test_update_round_trip(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(last_date="2026-06-19"))
    states = tracker.read()
    assert "KData" in states
    assert states["KData"].last_date == "2026-06-19"
    assert states["KData"].schema_version == "1.0"


def test_update_only_targeted_type(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats("KData"))
    tracker.update("Trade_Status", _make_stats("Trade_Status"))
    tracker.update("KData", _make_stats("KData", row_count=999))
    states = tracker.read()
    assert states["KData"].row_count == 999
    assert states["Trade_Status"].row_count == 6000


def test_update_bumps_top_level_updated_at(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats())
    full1 = tracker.read_full()
    import time

    time.sleep(0.01)
    tracker.update("Trade_Status", _make_stats("Trade_Status"))
    full2 = tracker.read_full()
    assert full2["updated_at"] >= full1["updated_at"]


# ---------- schema_version regression ----------


def test_update_rejects_schema_regression(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(schema_version="1.0"))
    with pytest.raises(SchemaVersionError):
        tracker.update("KData", _make_stats(schema_version="0.9"))


def test_update_accepts_higher_schema(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(schema_version="1.0"))
    tracker.update("KData", _make_stats(schema_version="1.1"))


# ---------- resume_range ----------


def test_resume_range_after_last_date(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(last_date="2026-06-19"))
    start, end = tracker.resume_range("KData", latest="2026-06-23")
    assert (start, end) == ("2026-06-20", "2026-06-23")


def test_resume_range_up_to_date(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(last_date="2026-06-23"))
    start, end = tracker.resume_range("KData", latest="2026-06-23")
    assert start > end  # start = latest+1, end = latest


def test_resume_range_no_state(tracker: StateTracker) -> None:
    start, end = tracker.resume_range("KData", latest="2026-06-23")
    assert start == DEFAULT_RESUME_START
    assert end == "2026-06-23"


def test_resume_range_unknown_type(tracker: StateTracker) -> None:
    start, end = tracker.resume_range("NotARealType", latest="2026-06-23")
    assert start == DEFAULT_RESUME_START
    assert end == "2026-06-23"


# ---------- universe_sha ----------


def test_universe_sha_round_trip(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(), universe_sha="abc123def456")
    assert tracker.get_universe_sha() == "abc123def456"


def test_is_universe_stale(tracker: StateTracker) -> None:
    tracker.update("KData", _make_stats(), universe_sha="abc")
    assert not tracker.is_universe_stale("abc")
    assert tracker.is_universe_stale("xyz")
    # 缺失 cached 不算 stale
    tracker2 = StateTracker(tracker.path.parent / "other.json")
    assert not tracker2.is_universe_stale("anything")


# ---------- corrupted ----------


def test_corrupted_state_raises(tracker: StateTracker, state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{ this is not valid JSON")
    with pytest.raises(StateCorruptedError):
        tracker.read()


# ---------- integration with DataFile ----------


def test_state_tracker_via_data_file(tmp_path: Path) -> None:
    """完整闭环: append → stats → tracker.update → tracker.read."""
    state_path = tmp_path / "datas" / "_Manifest" / "Data_State.json"
    tracker = StateTracker(state_path)
    df_obj = DataFile(REGISTRY.get("KData"), root=tmp_path)

    df = df_obj.read  # noqa: B018 (only sanity: method exists)
    assert callable(df)

    df_obj.append(__import__("pandas").DataFrame(
        {
            "trading_date": ["2024-01-02", "2024-01-02"],
            "stock_code": ["600000.SH", "000012.SZ"],
            "open": [10.0, 20.0],
            "high": [10.5, 20.5],
            "low": [9.8, 19.8],
            "close": [10.3, 20.3],
            "amount": [1e8, 2e8],
            "volume": [1e7, 2e7],
            "close_adj": [10.3, 20.3],
        }
    ))
    tracker.update("KData", df_obj.stats())
    states = tracker.read()
    assert states["KData"].row_count == 2
    assert states["KData"].first_date == "2024-01-02"