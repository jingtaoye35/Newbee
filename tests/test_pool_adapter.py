"""`alpha_backend.datasource.storage.pool_adapter.StockPool` 单元测试."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.datasource.storage.pool_adapter import StockPool  # noqa: E402


@pytest.fixture
def tmp_universe(tmp_path: Path) -> Path:
    """写一个 3 行的 Universe.parquet 到 tmp_path."""
    df = pd.DataFrame(
        {
            "stock_index": [0, 1, 2],
            "stock_code": ["000001.SZ", "000002.SZ", "600000.SH"],
            "ipo_date": ["1991-01-01", "1991-04-03", "2020-01-01"],
        }
    )
    path = tmp_path / "Universe.parquet"
    df.to_parquet(path, index=False)
    return path


def test_load_returns_pool_with_correct_size(tmp_universe: Path):
    pool = StockPool.load(tmp_universe)
    assert pool.size() == 3
    assert pool.stock_ids == ["000001.SZ", "000002.SZ", "600000.SH"]


def test_load_missing_returns_empty_pool(tmp_path: Path):
    """不存在的路径 → 空 pool (size 0, 不是抛错)."""
    pool = StockPool.load(tmp_path / "nope.parquet")
    assert pool.size() == 0
    assert pool.stock_ids == []


def test_export_columns_and_types(tmp_universe: Path):
    pool = StockPool.load(tmp_universe)
    df = pool.export()
    assert list(df.columns) == ["stock_index", "stock_code", "ipo_date"]
    assert df["stock_code"].tolist() == ["000001.SZ", "000002.SZ", "600000.SH"]


def test_idx_of_and_stock_of(tmp_universe: Path):
    pool = StockPool.load(tmp_universe)
    assert pool.idx_of("000002.SZ") == 1
    assert pool.stock_of(2) == "600000.SH"
    assert pool.idx_of("999999.SH") is None
    assert pool.stock_of(99) is None


def test_add_is_idempotent(tmp_universe: Path):
    """add 同一只股票 → 返回相同 idx, 不重复."""
    pool = StockPool.load(tmp_universe)
    first = pool.add("000001.SZ", source="manual")
    second = pool.add("000001.SZ", source="different")
    assert first == second == 0
    assert pool.size() == 3  # 没新增


def test_add_appends_and_saves(tmp_universe: Path):
    pool = StockPool.load(tmp_universe)
    new_idx = pool.add("300750.SZ", source="manual", ipo_date="2018-06-11")
    assert new_idx == 3
    # 重 load 验证落盘
    pool2 = StockPool.load(tmp_universe)
    assert pool2.size() == 4
    assert "300750.SZ" in pool2.stock_ids


def test_add_rejects_malformed_stock_code(tmp_universe: Path):
    pool = StockPool.load(tmp_universe)
    with pytest.raises(ValueError, match="9 字符"):
        pool.add("600000")
    with pytest.raises(ValueError, match="9 字符"):
        pool.add("600000.SHX")


def test_active_mask_respects_ipo_date(tmp_universe: Path):
    """active_mask(asof): 只算 asof 当天及之前上市的."""
    pool = StockPool.load(tmp_universe)
    # 1990-12-31: 全部未上市 (ipo_date 最小是 1991-01-01)
    mask_early = pool.active_mask(date(1990, 12, 31))
    assert mask_early.tolist() == [False, False, False]
    # 2019-12-31: 000001 + 000002 已上市, 600000.SH (2020-01-01) 未上市
    mask_2019 = pool.active_mask(date(2019, 12, 31))
    assert mask_2019.tolist() == [True, True, False]
    # 2020-06-01: 全部已上市
    mask_full = pool.active_mask(date(2020, 6, 1))
    assert mask_full.tolist() == [True, True, True]


def test_active_mask_default_uses_today(tmp_universe: Path, monkeypatch):
    """asof=None → 内部用 date.today()."""
    pool = StockPool.load(tmp_universe)
    from datetime import date as _date

    class FakeDate(_date):
        @classmethod
        def today(cls):
            return _date(2019, 6, 1)

    monkeypatch.setattr("alpha_backend.datasource.storage.pool_adapter.date", FakeDate)
    mask = pool.active_mask()  # no asof
    assert mask.tolist() == [True, True, False]


def test_universe_sha_is_stable(tmp_universe: Path):
    """相同 stock_code 列表 → 相同 sha (顺序无关)."""
    pool = StockPool.load(tmp_universe)
    sha1 = pool.universe_sha()
    pool2 = StockPool.load(tmp_universe)
    sha2 = pool2.universe_sha()
    assert sha1 == sha2
    # sha 是 hex 字符串, 16 字符
    assert len(sha1) == 16
    assert all(c in "0123456789abcdef" for c in sha1)