"""fetch_stock_hist(append=True) 测试 — mock akshare 不实际拉.

覆盖 spec/data-ingestion 的 ADDED Requirements:
- append=True merges new rows without touching old rows
- append=True with overlapping dates deduplicates
- append=True with no existing file behaves as fresh write
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.sources import akshare as akshare_mod  # noqa: E402


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    p = tmp_path / "raw"
    p.mkdir()
    return p


def _make_df(stock_id: str, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "stock_id": [stock_id] * len(dates),
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.5] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [1000] * len(dates),
            "adj_close": [10.5] * len(dates),
        }
    )


def test_append_merges_new_rows_without_touching_old(raw_dir: Path):
    """旧文件有 3 行, 追加 2 行 → 总 5 行, 旧数据原样保留."""
    stock_id = "600000"
    old = _make_df(stock_id, ["2026-06-15", "2026-06-16", "2026-06-17"])
    old.to_parquet(raw_dir / f"{stock_id}.parquet", index=False)

    new = _make_df(stock_id, ["2026-06-18", "2026-06-19"])

    # mock akshare.stock_zh_a_daily (sina source) → 返回 new
    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=new):
            result = akshare_mod.fetch_stock_hist(
                stock_id,
                start=date(2026, 6, 18),
                end=date(2026, 6, 19),
                adjust="qfq",
                source="sina",
                use_cache=False,
                raw_dir=raw_dir,
                append=True,
            )

    # 文件总 5 行
    final = pd.read_parquet(raw_dir / f"{stock_id}.parquet")
    assert len(final) == 5
    # 旧行原样保留 (close == 10.5)
    assert (final[final["date"] < pd.Timestamp("2026-06-18")]["close"] == 10.5).all()
    # 新行写入
    assert (final[final["date"] >= pd.Timestamp("2026-06-18")]["close"] == 10.5).all()
    # 返回值是新区间的视图
    assert len(result) == 2


def test_append_with_overlapping_dates_dedup(raw_dir: Path):
    """重叠区间 → 同一天只保留一行, 新值覆盖旧值."""
    stock_id = "600000"
    old = _make_df(stock_id, ["2026-06-15", "2026-06-16", "2026-06-17"])
    old.to_parquet(raw_dir / f"{stock_id}.parquet", index=False)

    # 新拉数据覆盖 06-16 (旧值 10.5 → 新值 20.0)
    new = _make_df(stock_id, ["2026-06-16", "2026-06-17", "2026-06-18"])
    new.loc[new["date"] == pd.Timestamp("2026-06-16"), "close"] = 20.0

    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=new):
            akshare_mod.fetch_stock_hist(
                stock_id,
                start=date(2026, 6, 16),
                end=date(2026, 6, 18),
                adjust="qfq",
                source="sina",
                use_cache=False,
                raw_dir=raw_dir,
                append=True,
            )

    final = pd.read_parquet(raw_dir / f"{stock_id}.parquet")
    # 4 行 (06-15, 06-16, 06-17, 06-18)
    assert len(final) == 4
    # 06-16 是新值 (20.0)
    row_16 = final[final["date"] == pd.Timestamp("2026-06-16")].iloc[0]
    assert row_16["close"] == 20.0


def test_append_without_existing_file_writes_fresh(raw_dir: Path):
    """append=True 但文件不存在 → 走 fresh write 路径."""
    stock_id = "999999"
    new = _make_df(stock_id, ["2026-06-18", "2026-06-19"])

    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=new):
            result = akshare_mod.fetch_stock_hist(
                stock_id,
                start=date(2026, 6, 18),
                end=date(2026, 6, 19),
                adjust="qfq",
                source="sina",
                use_cache=False,
                raw_dir=raw_dir,
                append=True,
            )

    assert (raw_dir / f"{stock_id}.parquet").exists()
    final = pd.read_parquet(raw_dir / f"{stock_id}.parquet")
    assert len(final) == 2
    assert len(result) == 2


def test_append_idempotent_on_double_run(raw_dir: Path):
    """两次相同 append → 行数不翻倍."""
    stock_id = "600000"
    new = _make_df(stock_id, ["2026-06-18", "2026-06-19"])

    with patch.object(akshare_mod, "with_retry", side_effect=lambda fn, **kw: fn()):
        with patch("akshare.stock_zh_a_daily", return_value=new):
            for _ in range(2):
                akshare_mod.fetch_stock_hist(
                    stock_id,
                    start=date(2026, 6, 18),
                    end=date(2026, 6, 19),
                    adjust="qfq",
                    source="sina",
                    use_cache=False,
                    raw_dir=raw_dir,
                    append=True,
                )

    final = pd.read_parquet(raw_dir / f"{stock_id}.parquet")
    assert len(final) == 2


def test_append_requires_start_and_end():
    """append=True 时不传 start/end → ValueError."""
    with pytest.raises(ValueError, match="必须显式传入"):
        akshare_mod.fetch_stock_hist(
            "600000",
            adjust="qfq",
            source="sina",
            use_cache=False,
            raw_dir=Path("/tmp"),
            append=True,
        )