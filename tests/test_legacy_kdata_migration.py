"""test_legacy_kdata_migration.py — `newbee.datasource.migration.legacy_kdata` 单元测试.

所有 fixture 用 `tmp_path` 写小规模合成 parquet, 不依赖真实 data/_Deprecated_*.parquet.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from newbee.datasource.migration.legacy_kdata import (
    _to_stock_code,
    build_kdata_from_legacy,
    build_stock_basic_data_from_legacy,
)
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile


# ---------- helpers: 写一个 legacy per-stock parquet ----------


def _write_legacy_per_stock(
    path: Path,
    stock_id: str,
    dates: list[pd.Timestamp],
    close: list[float] | None = None,
    adj_close: list[float] | None = None,
    outstanding_share: list[float] | None = None,
    turnover: list[float] | None = None,
) -> None:
    """写一个 {stock_id}.parquet, 列: date, stock_id, open, high, low, close, volume, amount, adj_close [, outstanding_share, turnover]."""
    n = len(dates)
    if close is None:
        close = [10.0 + i for i in range(n)]
    if adj_close is None:
        adj_close = [c * 0.5 for c in close]
    if outstanding_share is None:
        outstanding_share = [1.0e7] * n
    if turnover is None:
        turnover = [0.01 * (i + 1) for i in range(n)]
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "stock_id": stock_id,
            "open": [c - 0.5 for c in close],
            "high": [c + 0.5 for c in close],
            "low": [c - 0.2 for c in close],
            "close": close,
            "volume": [1e6 * (i + 1) for i in range(n)],
            "amount": [1e8 * (i + 1) for i in range(n)],
            "adj_close": adj_close,
            "outstanding_share": outstanding_share,
            "turnover": turnover,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(path))


# ---------- _to_stock_code ----------


@pytest.mark.parametrize(
    "stock_id,expected",
    [
        ("600000", "600000.SH"),
        ("900000", "900000.SH"),
        ("000001", "000001.SZ"),
        ("301000", "301000.SZ"),
    ],
)
def test_to_stock_code_valid(stock_id: str, expected: str) -> None:
    assert _to_stock_code(stock_id) == expected


@pytest.mark.parametrize("stock_id", ["400001", "500001", "700001", "800001"])
def test_to_stock_code_unknown_prefix_raises(stock_id: str) -> None:
    with pytest.raises(ValueError, match=stock_id):
        _to_stock_code(stock_id)


@pytest.mark.parametrize("stock_id", ["abc123", "12345", "1234567", "", "60000A"])
def test_to_stock_code_malformed_raises(stock_id: str) -> None:
    with pytest.raises(ValueError):
        _to_stock_code(stock_id)


# ---------- build_kdata_from_legacy: happy path ----------


def test_build_kdata_two_stock_happy(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates_001 = ["2024-01-02", "2024-01-03", "2024-01-04"]
    dates_600 = ["2024-01-02", "2024-01-03", "2024-01-04"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates_001))
    _write_legacy_per_stock(raw / "600000.parquet", "600000", pd.to_datetime(dates_600))
    _write_legacy_per_stock(adj / "000001.parquet", "000001", pd.to_datetime(dates_001))
    _write_legacy_per_stock(adj / "600000.parquet", "600000", pd.to_datetime(dates_600))

    df = build_kdata_from_legacy(raw, adj)

    assert len(df) == 6
    expected_cols = {
        "trading_date", "stock_code", "open", "high", "low", "close",
        "amount", "volume", "close_adj",
    }
    assert set(df.columns) == expected_cols
    # 在同一 trading_date 内, stock_code 按字典序 (000001.SZ < 600000.SH)
    for _, group in df.groupby("trading_date", sort=True):
        codes = group["stock_code"].tolist()
        assert codes == sorted(codes)
    assert "000001.SZ" in df["stock_code"].values
    assert "600000.SH" in df["stock_code"].values


def test_build_kdata_columns_exact(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates = ["2024-01-02"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates))
    _write_legacy_per_stock(adj / "000001.parquet", "000001", pd.to_datetime(dates))

    df = build_kdata_from_legacy(raw, adj)
    assert list(df.columns) == [
        "trading_date", "stock_code", "open", "high", "low", "close",
        "amount", "volume", "close_adj",
    ]


def test_build_kdata_iso_date_format(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates = ["1992-02-28"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates))
    _write_legacy_per_stock(adj / "000001.parquet", "000001", pd.to_datetime(dates))

    df = build_kdata_from_legacy(raw, adj)
    td = df.iloc[0]["trading_date"]
    assert isinstance(td, str)
    assert td == "1992-02-28"
    assert len(td) == 10


def test_build_kdata_close_from_raw_adj_from_adj(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates = ["2024-01-02"]
    _write_legacy_per_stock(
        raw / "600000.parquet",
        "600000",
        pd.to_datetime(dates),
        close=[22.0],
        adj_close=[0.99],  # raw 的 adj_close 不被使用
    )
    _write_legacy_per_stock(
        adj / "600000.parquet",
        "600000",
        pd.to_datetime(dates),
        close=[999.0],  # adj 文件的 close 不被使用
        adj_close=[0.60],
    )

    df = build_kdata_from_legacy(raw, adj)
    assert float(df.iloc[0]["close"]) == pytest.approx(22.0)
    assert float(df.iloc[0]["close_adj"]) == pytest.approx(0.60)


# ---------- build_kdata_from_legacy: missing adj ----------


def test_build_kdata_missing_adj_warns_and_keeps_row(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates = ["2024-01-02"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates))
    _write_legacy_per_stock(raw / "600000.parquet", "600000", pd.to_datetime(dates))
    _write_legacy_per_stock(adj / "000001.parquet", "000001", pd.to_datetime(dates))
    # adj 缺 600000

    with caplog.at_level(logging.WARNING):
        df = build_kdata_from_legacy(raw, adj)

    # 600000.SH 仍然出现, close_adj = None
    sbd_rows = df[df["stock_code"] == "600000.SH"]
    assert len(sbd_rows) == 1
    assert pd.isna(sbd_rows.iloc[0]["close_adj"])
    # WARNING 日志包含 600000.SH
    assert any("600000.SH" in rec.message for rec in caplog.records)


def test_build_kdata_all_in_both_no_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates = ["2024-01-02"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates))
    _write_legacy_per_stock(adj / "000001.parquet", "000001", pd.to_datetime(dates))

    with caplog.at_level(logging.WARNING):
        df = build_kdata_from_legacy(raw, adj)

    assert all("missing" not in rec.message.lower() for rec in caplog.records)
    assert not df["close_adj"].isna().any()


# ---------- build_stock_basic_data_from_legacy ----------


def test_build_sbd_two_stock_happy(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    dates_001 = ["2024-01-02", "2024-01-03", "2024-01-04"]
    dates_600 = ["2024-01-02", "2024-01-03", "2024-01-04"]
    _write_legacy_per_stock(
        raw / "000001.parquet",
        "000001",
        pd.to_datetime(dates_001),
        outstanding_share=[20_300_000.0] * 3,
        turnover=[0.00114, 0.00120, 0.00118],
    )
    _write_legacy_per_stock(
        raw / "600000.parquet",
        "600000",
        pd.to_datetime(dates_600),
        outstanding_share=[9_500_000.0] * 3,
        turnover=[0.005, 0.006, 0.007],
    )

    df = build_stock_basic_data_from_legacy(raw)

    assert len(df) == 6
    expected_cols = {
        "trading_date", "stock_code", "adj_factor",
        "limit_upper_price", "limit_lower_price", "sw_industry",
        "outstanding_share", "turnover",
    }
    assert set(df.columns) == expected_cols

    row_001 = df[df["stock_code"] == "000001.SZ"].iloc[0]
    assert float(row_001["outstanding_share"]) == 20_300_000.0
    assert abs(float(row_001["turnover"]) - 0.00114) < 1e-9

    # adj_factor / limit_* / sw_industry 全部 None
    for col in ("adj_factor", "limit_upper_price", "limit_lower_price", "sw_industry"):
        assert df[col].isna().all()


def test_build_sbd_does_not_need_adj_dir(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"  # 存在但是空
    adj.mkdir(parents=True, exist_ok=True)
    dates = ["2024-01-02"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates))

    df = build_stock_basic_data_from_legacy(raw)
    assert len(df) == 1
    assert df.iloc[0]["stock_code"] == "000001.SZ"


# ---------- round-trip via DataFile.upsert / read ----------


def test_kdata_round_trip_via_datafile(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    adj = tmp_path / "adj"
    dates = ["2024-01-02", "2024-01-03"]
    _write_legacy_per_stock(raw / "000001.parquet", "000001", pd.to_datetime(dates))
    _write_legacy_per_stock(adj / "000001.parquet", "000001", pd.to_datetime(dates))

    df = build_kdata_from_legacy(raw, adj)
    target = tmp_path / "KData.parquet"

    from dataclasses import replace as dc_replace
    custom_dtype = dc_replace(REGISTRY.get("KData"), storage_path=Path(target.name))
    custom_f = DataFile(custom_dtype, root=tmp_path)
    custom_f.upsert(df, conflict="replace")

    reread = custom_f.read()
    assert len(reread) == len(df)
    assert list(reread.columns) == list(df.columns)


def test_sbd_round_trip_via_datafile(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    dates = ["2024-01-02", "2024-01-03"]
    _write_legacy_per_stock(
        raw / "000001.parquet",
        "000001",
        pd.to_datetime(dates),
        outstanding_share=[5_000_000.0, 5_000_000.0],
        turnover=[0.01, 0.02],
    )

    df = build_stock_basic_data_from_legacy(raw)
    target = tmp_path / "Stock_Basic_Data.parquet"

    from dataclasses import replace as dc_replace
    custom_dtype = dc_replace(REGISTRY.get("Stock_Basic_Data"), storage_path=Path(target.name))
    custom_f = DataFile(custom_dtype, root=tmp_path)
    custom_f.upsert(df, conflict="replace")

    reread = custom_f.read()
    assert len(reread) == len(df)
    assert reread["adj_factor"].isna().all()
    assert float(reread["outstanding_share"].iloc[0]) == pytest.approx(5_000_000.0)
