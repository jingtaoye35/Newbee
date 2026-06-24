"""test_populate_adj_factor.py — `newbee.datasource.migration.populate_adj_factor` 单元测试."""
from __future__ import annotations

from dataclasses import replace as dc_replace
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from newbee.datasource.migration.populate_adj_factor import (
    apply_adj_factor_to_stock_basic,
    compute_adj_factor_from_kdata,
)
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile


# ---------- helpers: 写一个合成 KData / Stock_Basic_Data parquet ----------


def _write_kdata(path: Path, rows: list[dict]) -> None:
    """rows 元素: {trading_date, stock_code, open, high, low, close, amount, volume, close_adj}."""
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(path))


def _write_sbd(
    path: Path,
    rows: list[dict],
) -> None:
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(path))


def _sbd_file_at(path: Path) -> DataFile:
    custom_dtype = dc_replace(
        REGISTRY.get("Stock_Basic_Data"),
        storage_path=path,
    )
    return DataFile(custom_dtype, root=path.parent)


# ---------- compute_adj_factor_from_kdata: happy path ----------


def test_compute_adj_factor_happy(tmp_path: Path) -> None:
    kdata_path = tmp_path / "KData.parquet"
    _write_kdata(
        kdata_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "amount": 1e8,
             "volume": 1e7, "close_adj": 20.0},
            {"trading_date": "2024-01-02", "stock_code": "000001.SZ",
             "open": 5.0, "high": 5.5, "low": 4.5, "close": 5.0, "amount": 1e8,
             "volume": 1e7, "close_adj": 10.0},
        ],
    )

    df = compute_adj_factor_from_kdata(kdata_path)
    assert len(df) == 2
    by_code = {row["stock_code"]: float(row["adj_factor"]) for _, row in df.iterrows()}
    assert by_code["600000.SH"] == pytest.approx(2.0)
    assert by_code["000001.SZ"] == pytest.approx(2.0)


def test_compute_adj_factor_close_zero(tmp_path: Path) -> None:
    kdata_path = tmp_path / "KData.parquet"
    _write_kdata(
        kdata_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "amount": 0.0,
             "volume": 0.0, "close_adj": 20.0},
        ],
    )

    df = compute_adj_factor_from_kdata(kdata_path)
    assert pd.isna(df.iloc[0]["adj_factor"])


def test_compute_adj_factor_close_adj_none(tmp_path: Path) -> None:
    kdata_path = tmp_path / "KData.parquet"
    _write_kdata(
        kdata_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "amount": 1e8,
             "volume": 1e7, "close_adj": None},
        ],
    )

    df = compute_adj_factor_from_kdata(kdata_path)
    assert pd.isna(df.iloc[0]["adj_factor"])


def test_compute_adj_factor_close_none(tmp_path: Path) -> None:
    kdata_path = tmp_path / "KData.parquet"
    _write_kdata(
        kdata_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": None, "high": None, "low": None, "close": None, "amount": None,
             "volume": None, "close_adj": 20.0},
        ],
    )

    df = compute_adj_factor_from_kdata(kdata_path)
    assert pd.isna(df.iloc[0]["adj_factor"])


def test_compute_adj_factor_columns_exact_and_sorted(tmp_path: Path) -> None:
    kdata_path = tmp_path / "KData.parquet"
    _write_kdata(
        kdata_path,
        [
            {"trading_date": "2024-01-03", "stock_code": "600000.SH",
             "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "amount": 1e8,
             "volume": 1e7, "close_adj": 20.0},
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "amount": 1e8,
             "volume": 1e7, "close_adj": 30.0},
        ],
    )

    df = compute_adj_factor_from_kdata(kdata_path)
    assert list(df.columns) == ["trading_date", "stock_code", "adj_factor"]
    # sorted by (trading_date, stock_code): 01-02 row first
    assert df.iloc[0]["trading_date"] == "2024-01-02"
    assert df.iloc[1]["trading_date"] == "2024-01-03"


# ---------- apply_adj_factor_to_stock_basic: in-place upsert ----------


def test_apply_preserves_total_share_and_turnover(tmp_path: Path) -> None:
    sbd_path = tmp_path / "Stock_Basic_Data.parquet"
    _write_sbd(
        sbd_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "adj_factor": None, "limit_upper_price": None, "limit_lower_price": None,
             "sw_industry": None, "total_share": 9_500_000.0, "turnover": 0.005},
            {"trading_date": "2024-01-03", "stock_code": "600000.SH",
             "adj_factor": None, "limit_upper_price": None, "limit_lower_price": None,
             "sw_industry": None, "total_share": 9_500_000.0, "turnover": 0.006},
            {"trading_date": "2024-01-02", "stock_code": "000001.SZ",
             "adj_factor": None, "limit_upper_price": None, "limit_lower_price": None,
             "sw_industry": None, "total_share": 20_300_000.0, "turnover": 0.00114},
        ],
    )

    adj_df = pd.DataFrame(
        {
            "trading_date": ["2024-01-02", "2024-01-03"],
            "stock_code": ["600000.SH", "600000.SH"],
            "adj_factor": [2.0, 2.5],
        }
    )

    n = apply_adj_factor_to_stock_basic(adj_df, sbd_path)
    assert n == 3

    reread = _sbd_file_at(sbd_path).read()
    # 3 rows preserved
    assert len(reread) == 3
    # total_share / turnover byte-identical
    row_001 = reread[reread["stock_code"] == "000001.SZ"].iloc[0]
    assert float(row_001["total_share"]) == pytest.approx(20_300_000.0)
    assert float(row_001["turnover"]) == pytest.approx(0.00114)
    assert pd.isna(row_001["adj_factor"])  # not in adj_df
    # 600000.SH rows got adj_factor
    r600 = reread[reread["stock_code"] == "600000.SH"]
    assert sorted(r600["adj_factor"].tolist()) == [2.0, 2.5]
    # total_share preserved
    assert (r600["total_share"] == 9_500_000.0).all()
    assert (r600["turnover"].isin([0.005, 0.006])).all()


def test_apply_left_join_keeps_unmatched_as_none(tmp_path: Path) -> None:
    sbd_path = tmp_path / "Stock_Basic_Data.parquet"
    _write_sbd(
        sbd_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "adj_factor": None, "limit_upper_price": None, "limit_lower_price": None,
             "sw_industry": None, "total_share": 1.0, "turnover": 0.1},
        ],
    )

    # adj_df doesn't have 600000.SH
    adj_df = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["000001.SZ"],
            "adj_factor": [3.0],
        }
    )

    n = apply_adj_factor_to_stock_basic(adj_df, sbd_path)
    assert n == 1

    reread = _sbd_file_at(sbd_path).read()
    assert len(reread) == 1
    assert pd.isna(reread.iloc[0]["adj_factor"])  # not in adj_df → stays None
    assert float(reread.iloc[0]["total_share"]) == pytest.approx(1.0)


def test_apply_round_trip_via_datafile(tmp_path: Path) -> None:
    sbd_path = tmp_path / "Stock_Basic_Data.parquet"
    _write_sbd(
        sbd_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "adj_factor": None, "limit_upper_price": None, "limit_lower_price": None,
             "sw_industry": None, "total_share": 1.0, "turnover": 0.1},
        ],
    )
    adj_df = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["600000.SH"],
            "adj_factor": [1.5],
        }
    )

    apply_adj_factor_to_stock_basic(adj_df, sbd_path)
    reread = _sbd_file_at(sbd_path).read()
    assert len(reread) == 1
    assert float(reread.iloc[0]["adj_factor"]) == pytest.approx(1.5)


def test_apply_idempotent(tmp_path: Path) -> None:
    """第二次运行产生 byte-identical 输出."""
    sbd_path = tmp_path / "Stock_Basic_Data.parquet"
    _write_sbd(
        sbd_path,
        [
            {"trading_date": "2024-01-02", "stock_code": "600000.SH",
             "adj_factor": None, "limit_upper_price": None, "limit_lower_price": None,
             "sw_industry": None, "total_share": 1.0, "turnover": 0.1},
        ],
    )
    adj_df = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["600000.SH"],
            "adj_factor": [1.5],
        }
    )

    apply_adj_factor_to_stock_basic(adj_df, sbd_path)
    first = pd.read_parquet(sbd_path)
    apply_adj_factor_to_stock_basic(adj_df, sbd_path)
    second = pd.read_parquet(sbd_path)

    # 排序后比较 (可能因 sort_values 的 stable 性产生不同 row order, 但 set-wise 应一致)
    assert sorted(first.columns.tolist()) == sorted(second.columns.tolist())
    assert len(first) == len(second) == 1
    assert float(first.iloc[0]["adj_factor"]) == float(second.iloc[0]["adj_factor"]) == pytest.approx(1.5)
