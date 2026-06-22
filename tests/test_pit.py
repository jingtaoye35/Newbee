"""PIT (Point-in-Time) 测试.

覆盖:
  1. 单元: ann_date 解析
  2. 单元: 字段映射
  3. 单元: 未披露财报 fallback
  4. 集成: 披露日边界 (asof 边界)
  5. 集成: 报告期可见性
  6. 集成: NaN 填充
  7. 集成: 多股票批量
  8. 集成: re-statement 支持 (4-key dedup)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.pit import PITStore


@pytest.fixture
def tmp_pit_file():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "pit.parquet"  # PIT 期望 file path


def make_store(tmpdir: Path) -> PITStore:
    return PITStore(tmpdir / "pit.parquet")


# ============ 单元测试 ============


def test_ann_date_parse():
    """ann_date 解析 (date / str)."""
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
                   "revenue", 100.0)
        store.add("000001", date(2023, 9, 30), "2023-10-25",
                   "net_profit", 10.0)
        v = store.get_value("net_profit", "000001", date(2023, 10, 26))
        assert v == 10.0


def test_field_mapping():
    """不同 field 独立存储."""
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
                   "revenue", 100.0)
        store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
                   "net_profit", 10.0)
        assert store.get_value("revenue", "000001", date(2023, 10, 26)) == 100.0
        assert store.get_value("net_profit", "000001", date(2023, 10, 26)) == 10.0


def test_unannounced_fallback():
    """未披露财报时, 取最近一次 (ann_date 之前)."""
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        store.add("000001", date(2023, 6, 30), date(2023, 8, 25),
                   "revenue", 50.0)
        store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
                   "revenue", 100.0)
        v = store.get_value("revenue", "000001", date(2023, 9, 1))
        assert v == 50.0
        v2 = store.get_value("revenue", "000001", date(2023, 10, 26))
        assert v2 == 100.0


def test_no_data():
    """完全没有数据时 get_value 返回 None."""
    with tempfile.TemporaryDirectory() as d:
        store = make_store(Path(d))
        v = store.get_value("revenue", "000001", date(2023, 10, 26))
        assert v is None


# ============ 集成测试 ============


def test_ann_date_boundary(tmp_pit_file):
    """披露日边界: asof == ann_date 应该可见."""
    store = PITStore(tmp_pit_file)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    v = store.get_value("revenue", "000001", date(2023, 10, 25))
    assert v == 100.0
    v_pre = store.get_value("revenue", "000001", date(2023, 10, 24))
    assert v_pre is None


def test_period_visibility(tmp_pit_file):
    """报告期可见性: Q3 报告在 10/25 披露, 12/31 之前都看 Q3 数据."""
    store = PITStore(tmp_pit_file)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    for d in [date(2023, 10, 26), date(2023, 11, 30), date(2023, 12, 31)]:
        assert store.get_value("revenue", "000001", d) == 100.0


def test_nan_fill(tmp_pit_file):
    """get_series: 在 ann_date 之前返回 NaN."""
    store = PITStore(tmp_pit_file)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    series = store.get_series("revenue", "000001",
                              date(2023, 10, 20), date(2023, 10, 27))
    # 10/20-10/24 应该是 NaN
    for d in [date(2023, 10, 20), date(2023, 10, 24)]:
        assert pd.isna(series.loc[pd.Timestamp(d)])
    # 10/25 之后应该是 100
    for d in [date(2023, 10, 25), date(2023, 10, 27)]:
        assert series.loc[pd.Timestamp(d)] == 100.0


def test_multi_stock_batch(tmp_pit_file):
    """多股票批量."""
    store = PITStore(tmp_pit_file)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    store.add("000002", date(2023, 9, 30), date(2023, 10, 26),
              "revenue", 200.0)
    store.add("000003", date(2023, 9, 30), date(2023, 10, 27),
              "revenue", 300.0)
    v1 = store.get_value("revenue", "000001", date(2023, 10, 25))
    v2_pre = store.get_value("revenue", "000002", date(2023, 10, 25))
    v2_post = store.get_value("revenue", "000002", date(2023, 10, 27))
    v3 = store.get_value("revenue", "000003", date(2023, 10, 28))
    assert v1 == 100.0
    assert v2_pre is None
    assert v2_post == 200.0
    assert v3 == 300.0


def test_restatement_preserved(tmp_pit_file):
    """re-statement: 不同 ann_date = 不同披露, 都保留 (4-key dedup)."""
    store = PITStore(tmp_pit_file)
    # 同一财报期, 两次披露 (re-statement)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    store.add("000001", date(2023, 9, 30), date(2023, 11, 15),
              "revenue", 120.0)
    # 10/26 ~ 11/14 看到 100
    assert store.get_value("revenue", "000001", date(2023, 10, 26)) == 100.0
    # 11/15 之后看到 120
    assert store.get_value("revenue", "000001", date(2023, 11, 15)) == 120.0
    assert store.get_value("revenue", "000001", date(2023, 12, 31)) == 120.0


def test_history_method(tmp_pit_file):
    """history 方法: 返回该股票所有披露记录."""
    store = PITStore(tmp_pit_file)
    store.add("000001", date(2023, 6, 30), date(2023, 8, 25),
              "revenue", 50.0)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    h = store.history("revenue", "000001")
    assert len(h) == 2
    assert h.iloc[0]["value"] == 50.0
    assert h.iloc[1]["value"] == 100.0


def test_save_load_roundtrip(tmp_pit_file):
    """save + load 循环."""
    store = PITStore(tmp_pit_file)
    store.add("000001", date(2023, 9, 30), date(2023, 10, 25),
              "revenue", 100.0)
    # 重新 load, 数据应该还在
    store2 = PITStore.load(tmp_pit_file)
    assert store2.get_value("revenue", "000001", date(2023, 12, 31)) == 100.0
