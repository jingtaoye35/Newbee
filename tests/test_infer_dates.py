"""storage 日期推断 helper 测试."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.storage import (  # noqa: E402
    infer_first_date_global,
    infer_last_date,
    infer_last_date_global,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir()
    (tmp_path / "adj").mkdir()
    return tmp_path


# ---------- infer_last_date (per-stock) ----------


def test_infer_last_date_returns_max_date(tmp_root: Path):
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-15", "2026-06-16", "2026-06-19"]),
            "open": [1.0, 1.1, 1.2],
            "high": [1.0, 1.1, 1.2],
            "low": [1.0, 1.1, 1.2],
            "close": [1.0, 1.1, 1.2],
            "volume": [100, 200, 300],
        }
    )
    df.to_parquet(tmp_root / "adj" / "600000.parquet", index=False)
    assert infer_last_date("600000", kind="adj", root=tmp_root) == date(2026, 6, 19)


def test_infer_last_date_returns_none_on_missing_file(tmp_root: Path):
    assert infer_last_date("999999", kind="adj", root=tmp_root) is None


def test_infer_last_date_returns_none_on_empty_file(tmp_root: Path):
    df = pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume"])
    df.to_parquet(tmp_root / "adj" / "600000.parquet", index=False)
    assert infer_last_date("600000", kind="adj", root=tmp_root) is None


def test_infer_last_date_with_raw_kind(tmp_root: Path):
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-19"]),
            "open": [1.0],
            "close": [1.0],
            "high": [1.0],
            "low": [1.0],
            "volume": [100],
        }
    )
    df.to_parquet(tmp_root / "raw" / "600000.parquet", index=False)
    assert infer_last_date("600000", kind="raw", root=tmp_root) == date(2026, 6, 19)


def test_infer_last_date_invalid_kind_raises(tmp_root: Path):
    with pytest.raises(ValueError, match="kind 必须是"):
        infer_last_date("600000", kind="bogus", root=tmp_root)


# ---------- infer_last_date_global ----------


def test_infer_last_date_global_single_file(tmp_root: Path):
    df = pd.DataFrame(
        {"date": pd.to_datetime(["2026-06-19"]), "open": [1.0], "close": [1.0]}
    )
    df.to_parquet(tmp_root / "raw" / "600000.parquet", index=False)
    assert infer_last_date_global("raw", root=tmp_root) == date(2026, 6, 19)


def test_infer_last_date_global_multi_file_takes_max(tmp_root: Path):
    for sid, dates in [
        ("600000", ["2026-06-15", "2026-06-19"]),
        ("600001", ["2026-06-17"]),
        ("600002", ["2026-06-18"]),
    ]:
        df = pd.DataFrame({"date": pd.to_datetime(dates), "open": [1.0] * len(dates)})
        df.to_parquet(tmp_root / "raw" / f"{sid}.parquet", index=False)
    assert infer_last_date_global("raw", root=tmp_root) == date(2026, 6, 19)


def test_infer_last_date_global_empty_dir_returns_none(tmp_root: Path):
    assert infer_last_date_global("raw", root=tmp_root) is None


def test_infer_last_date_global_missing_dir_returns_none(tmp_root: Path):
    other = tmp_root / "nonexistent"
    assert infer_last_date_global("raw", root=other) is None


# ---------- infer_first_date_global ----------


def test_infer_first_date_global_multi_file_takes_min(tmp_root: Path):
    for sid, dates in [
        ("600000", ["2026-06-15", "2026-06-19"]),
        ("600001", ["2026-06-17"]),
        ("600002", ["2026-06-18"]),
    ]:
        df = pd.DataFrame({"date": pd.to_datetime(dates), "open": [1.0] * len(dates)})
        df.to_parquet(tmp_root / "raw" / f"{sid}.parquet", index=False)
    assert infer_first_date_global("raw", root=tmp_root) == date(2026, 6, 15)


def test_infer_first_date_global_empty_dir_returns_none(tmp_root: Path):
    assert infer_first_date_global("raw", root=tmp_root) is None