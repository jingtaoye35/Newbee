"""`newbee.datasource.storage.bars_adapter` 单元测试."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.datasource.registry import REGISTRY  # noqa: E402
from newbee.datasource.schemas.kdata import KData  # noqa: E402
from newbee.datasource.storage.bars_adapter import Bars, load_bars  # noqa: E402
from newbee.datasource.storage.io import DataFile  # noqa: E402


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """tmp_root 模拟 PROJECT_ROOT (data/ 在其下).

    load_bars 的 root 参数 = PROJECT_ROOT (与 DataFile 的约定一致).
    """
    (tmp_path / "data").mkdir()
    return tmp_path


def _seed_kdata(path: Path, rows: list[dict]) -> None:
    """写一个 KData 兼容的 parquet (用于 read 测试)."""
    import pandas as pd

    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 直接写 parquet, 绕过 DataFile 的 schema 校验 (unit test 不走完整 state)
    df.to_parquet(path, index=False)


def test_load_bars_empty_returns_empty_bars(tmp_root: Path):
    """KData 不存在 → 抛 FileNotFoundError (DataFile.read 的行为)."""
    with pytest.raises(FileNotFoundError):
        load_bars(
            stock_codes=["000001.SZ"],
            start=date(2024, 1, 1),
            end=date(2024, 1, 5),
            root=tmp_root,
        )


def test_load_bars_pivot_shape(tmp_root: Path):
    """3 只股票 × 5 天 → Bars shape (T=5, N=3)."""
    rows = []
    for d in ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]:
        for code, base_open, base_close in [
            ("000001.SZ", 10.0, 10.5),
            ("000002.SZ", 20.0, 21.0),
            ("000003.SZ", 30.0, 30.3),
        ]:
            rows.append({
                "trading_date": d,
                "stock_code": code,
                "open": base_open,
                "high": max(base_open, base_close) + 0.1,
                "low": min(base_open, base_close) - 0.1,
                "close": base_close,
                "amount": 1_000_000.0,
                "volume": 100_000.0,
                "close_adj": base_close * 0.5,
            })
    _seed_kdata(tmp_root / "data" / "KData.parquet", rows)
    bars = load_bars(
        stock_codes=["000001.SZ", "000002.SZ", "000003.SZ"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
        root=tmp_root,
    )
    assert bars.T == 5
    assert bars.N == 3
    assert bars.stock_ids == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert bars.open.shape == (5, 3)
    assert bars.close.shape == (5, 3)
    assert bars.volume.shape == (5, 3)
    assert bars.adj_close.shape == (5, 3)


def test_load_bars_adj_uses_close_adj(tmp_root: Path):
    """kind='adj' → adj_close == close_adj (不取 close)."""
    rows = [
        {"trading_date": "2024-01-01", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 2.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 99.0},
        {"trading_date": "2024-01-02", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 2.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 99.0},
    ]
    _seed_kdata(tmp_root / "data" / "KData.parquet", rows)
    bars = load_bars(
        stock_codes=["000001.SZ"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        root=tmp_root,
    )
    assert bars.adj_close[0, 0] == 99.0
    assert bars.close[0, 0] == 2.0  # close != adj_close


def test_load_bars_raw_uses_close_for_adj_close(tmp_root: Path):
    """kind='raw' → adj_close == close (不复权)."""
    rows = [
        {"trading_date": "2024-01-01", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 5.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 99.0},
    ]
    _seed_kdata(tmp_root / "data" / "KData.parquet", rows)
    bars = load_bars(
        stock_codes=["000001.SZ"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        kind="raw",
        root=tmp_root,
    )
    assert bars.adj_close[0, 0] == 5.0  # == close, 不是 99.0


def test_load_bars_missing_cell_is_nan(tmp_root: Path):
    """某只股票某天缺失 → pivot 后为 NaN, 但 T/N 形状不变 (按 input stock_codes 顺序)."""
    rows = [
        {"trading_date": "2024-01-01", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 1.0},
        # 000002.SZ 在 2024-01-01 缺失
        {"trading_date": "2024-01-02", "stock_code": "000001.SZ",
         "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1,
         "amount": 0.0, "volume": 0.0, "close_adj": 1.1},
        {"trading_date": "2024-01-02", "stock_code": "000002.SZ",
         "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 2.0},
    ]
    _seed_kdata(tmp_root / "data" / "KData.parquet", rows)
    bars = load_bars(
        stock_codes=["000001.SZ", "000002.SZ"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        root=tmp_root,
    )
    assert bars.T == 2 and bars.N == 2
    # 000002.SZ 在 2024-01-01 缺失
    assert np.isnan(bars.close[0, 1])
    assert bars.close[1, 1] == 2.0


def test_load_bars_dates_are_sorted_asc(tmp_root: Path):
    """dates 按 trading_date 升序."""
    rows = [
        {"trading_date": "2024-01-03", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 1.0},
        {"trading_date": "2024-01-01", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 1.0},
        {"trading_date": "2024-01-02", "stock_code": "000001.SZ",
         "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
         "amount": 0.0, "volume": 0.0, "close_adj": 1.0},
    ]
    _seed_kdata(tmp_root / "data" / "KData.parquet", rows)
    bars = load_bars(
        stock_codes=["000001.SZ"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 3),
        root=tmp_root,
    )
    assert bars.dates == [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]


def test_bars_matrix_shape_and_columns():
    """Bars.matrix 堆叠 [open, high, low, close, volume, adj_close]."""
    bars = Bars(
        dates=[date(2024, 1, 1)],
        stock_ids=["000001.SZ"],
        open=np.array([[10.0]]),
        high=np.array([[11.0]]),
        low=np.array([[9.0]]),
        close=np.array([[10.5]]),
        volume=np.array([[1000.0]]),
        adj_close=np.array([[10.5]]),
    )
    m = bars.matrix
    assert m.shape == (1, 1, 6)
    # 列序: 0=open, 1=high, 2=low, 3=close, 4=volume, 5=adj_close
    assert m[0, 0, 0] == 10.0
    assert m[0, 0, 1] == 11.0
    assert m[0, 0, 2] == 9.0
    assert m[0, 0, 3] == 10.5
    assert m[0, 0, 4] == 1000.0
    assert m[0, 0, 5] == 10.5


def test_bars_returns_simple():
    """returns(kind='simple') 第一行 NaN, 后续 r[t] = close[t]/close[t-1] - 1."""
    p = np.array([
        [10.0, 100.0],
        [11.0, 110.0],
        [12.0, 99.0],
    ])
    bars = Bars(
        dates=[date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        stock_ids=["A", "B"],
        open=p, high=p, low=p, close=p, volume=p,
        adj_close=p,
    )
    r = bars.returns("simple")
    assert np.isnan(r[0, 0]) and np.isnan(r[0, 1])
    assert r[1, 0] == pytest.approx(0.1)
    # r[2,0] = 12/11 - 1 ≈ 0.0909
    assert r[2, 0] == pytest.approx(12 / 11 - 1)
    # 第 1 列: 100 -> 110 -> 99, 第二行 r=0.1, 第三行 r=99/110-1=-0.1
    assert r[1, 1] == pytest.approx(0.1)
    assert r[2, 1] == pytest.approx(99 / 110 - 1)


def test_bars_invalid_kind_raises():
    bars = Bars(
        dates=[date(2024, 1, 1)], stock_ids=["A"],
        open=np.array([[1.0]]), high=np.array([[1.0]]),
        low=np.array([[1.0]]), close=np.array([[1.0]]),
        volume=np.array([[0.0]]), adj_close=np.array([[1.0]]),
    )
    with pytest.raises(ValueError, match="kind 必须是"):
        bars.returns("bogus")