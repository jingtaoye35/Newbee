"""test_trade_status_service.py — TradeStatusService + StockBasicDataService 推断测试."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.datasource.service.trade_status import TradeStatusService
from alpha_backend.datasource.service.stock_basic_data import StockBasicDataService
from alpha_backend.datasource.storage.io import DataFile


def _write_kdata(root: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    for col in ("open", "high", "low", "close", "amount", "volume", "close_adj"):
        if col in df.columns:
            df[col] = df[col].astype("float32")
    path = root / "datas" / "KData.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_universe(root: Path, codes: list[str]) -> None:
    df = pd.DataFrame(
        [(i, c, "1990-01-01") for i, c in enumerate(codes)],
        columns=["stock_index", "stock_code", "ipo_date"],
    )
    df["stock_index"] = df["stock_index"].astype("int32")
    path = root / "datas" / "Universe.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


# ---------- TradeStatus ----------


def test_trade_status_infers_suspended(tmp_path: Path) -> None:
    """high==low==close==0 → 停牌."""
    _write_universe(tmp_path, ["600000.SH", "000012.SZ"])
    _write_kdata(
        tmp_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.3, "amount": 1e8,
             "volume": 1e7, "close_adj": 10.3},
            {"trading_date": "2024-01-02", "stock_code": "000012.SZ",
             "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "amount": 0.0,
             "volume": 0.0, "close_adj": 0.0},
        ],
    )
    svc = TradeStatusService(root=str(tmp_path))
    svc.full_init(start="2024-01-01")

    df = pd.read_parquet(tmp_path / "datas" / "Trade_Status.parquet")
    assert len(df) == 2
    suspended = df[df["is_suspended"]]["stock_code"].tolist()
    assert "000012.SZ" in suspended
    assert "600000.SH" not in suspended


def test_trade_status_requires_kdata(tmp_path: Path) -> None:
    svc = TradeStatusService(root=str(tmp_path))
    with pytest.raises(RuntimeError):
        svc.full_init(start="2024-01-01")


def test_trade_status_handles_nan_ohlcv(tmp_path: Path) -> None:
    """OHLCV 全 NaN → 停牌."""
    _write_universe(tmp_path, ["600000.SH"])
    # 写带 NaN 的行
    df = pd.DataFrame(
        [{"trading_date": "2024-01-02", "stock_code": "600000.SH",
          "open": None, "high": None, "low": None, "close": None,
          "amount": None, "volume": None, "close_adj": None}]
    )
    for col in ("open", "high", "low", "close", "amount", "volume", "close_adj"):
        df[col] = df[col].astype("float32")
    path = tmp_path / "datas" / "KData.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    TradeStatusService(root=str(tmp_path)).full_init(start="2024-01-01")
    df_ts = pd.read_parquet(tmp_path / "datas" / "Trade_Status.parquet")
    assert bool(df_ts.iloc[0]["is_suspended"])


# ---------- StockBasicData ----------


def test_stock_basic_data_infer_from_kdata(tmp_path: Path) -> None:
    """adj_factor = close_adj / close."""
    _write_universe(tmp_path, ["600000.SH"])
    _write_kdata(
        tmp_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "amount": 1e8,
             "volume": 1e7, "close_adj": 20.0},  # 后复权 = 2x
        ],
    )
    svc = StockBasicDataService(root=str(tmp_path))
    result = svc.full_init(start="2024-01-01")
    assert result["rows"] == 1

    df = pd.read_parquet(tmp_path / "datas" / "Stock_Basic_Data.parquet")
    assert abs(float(df.iloc[0]["adj_factor"]) - 2.0) < 1e-6


def test_stock_basic_data_skips_zero_close(tmp_path: Path) -> None:
    """close == 0 → 跳过该行."""
    _write_universe(tmp_path, ["600000.SH"])
    _write_kdata(
        tmp_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "amount": 0.0,
             "volume": 0.0, "close_adj": 0.0},
        ],
    )
    result = StockBasicDataService(root=str(tmp_path)).full_init(start="2024-01-01")
    assert result["rows"] == 0
    # 文件不存在 (因为没数据写入)
    assert not (tmp_path / "datas" / "Stock_Basic_Data.parquet").exists()


def test_stock_basic_data_requires_kdata(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        StockBasicDataService(root=str(tmp_path)).full_init(start="2024-01-01")