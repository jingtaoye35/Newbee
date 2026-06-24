"""test_storage_io.py — DataFile + CoverageStats 测试."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.errors import (
    PrimaryKeyConflictError,
    SchemaValidationError,
    SchemaVersionError,
)
from newbee.datasource.storage.io import DataFile


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """临时项目根 (DataFile 默认相对 PROJECT_ROOT 解析; 这里显式传 root)."""
    return tmp_path


@pytest.fixture
def kdata_file(tmp_root: Path) -> DataFile:
    return DataFile(REGISTRY.get("KData"), root=tmp_root)


def _sample_kdata_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trading_date": ["2024-01-02", "2024-01-02", "2024-01-03"],
            "stock_code": ["600000.SH", "000012.SZ", "600000.SH"],
            "open": [10.0, 20.0, 10.5],
            "high": [10.5, 20.5, 11.0],
            "low": [9.8, 19.8, 10.3],
            "close": [10.3, 20.3, 10.8],
            "amount": [1e8, 2e8, 1.1e8],
            "volume": [1e7, 2e7, 1.1e7],
            "close_adj": [10.3, 20.3, 10.8],
        }
    )


# ---------- exists / read on missing ----------


def test_read_missing_file_raises(kdata_file: DataFile) -> None:
    assert not kdata_file.exists()
    with pytest.raises(FileNotFoundError):
        kdata_file.read()


def test_exists(kdata_file: DataFile, tmp_root: Path) -> None:
    assert not kdata_file.exists()
    df = _sample_kdata_rows()
    kdata_file.append(df)
    assert kdata_file.exists()


# ---------- append + read ----------


def test_append_then_read(kdata_file: DataFile) -> None:
    df = _sample_kdata_rows()
    n = kdata_file.append(df)
    assert n == 3

    out = kdata_file.read()
    assert len(out) == 3
    # 默认按 (trading_date, stock_code) 排序
    assert list(out["stock_code"]) == ["000012.SZ", "600000.SH", "600000.SH"]


def test_read_with_start_end(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    out = kdata_file.read(start="2024-01-03", end="2024-01-03")
    assert len(out) == 1
    assert out.iloc[0]["trading_date"] == "2024-01-03"


def test_read_with_stock_codes(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    out = kdata_file.read(stock_codes=["600000.SH"])
    assert (out["stock_code"] == "600000.SH").all()
    assert len(out) == 2


def test_read_with_columns(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    out = kdata_file.read(columns=["trading_date", "close"])
    assert list(out.columns) == ["trading_date", "close"]
    assert len(out) == 3


# ---------- validation ----------


def test_append_rejects_invalid_rows(kdata_file: DataFile) -> None:
    bad = _sample_kdata_rows()
    # 强制把 volume 转成 object, 这样可以塞入非数字, 让 Pydantic 校验失败
    bad = bad.astype(object)
    bad.loc[0, "volume"] = "abc"
    with pytest.raises(SchemaValidationError):
        kdata_file.append(bad)
    # 文件不应被创建
    assert not kdata_file.exists()


def test_append_rejects_malformed_stock_code(kdata_file: DataFile) -> None:
    bad = _sample_kdata_rows()
    bad.loc[0, "stock_code"] = "600000"  # 6 字符, 无 .SH/.SZ
    with pytest.raises(SchemaValidationError):
        kdata_file.append(bad)


# ---------- primary key conflict ----------


def test_append_raises_on_conflict(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    df2 = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["600000.SH"],
            "open": [11.0],
            "high": [11.5],
            "low": [10.8],
            "close": [11.3],
            "amount": [1.2e8],
            "volume": [1.2e7],
            "close_adj": [11.3],
        }
    )
    with pytest.raises(PrimaryKeyConflictError):
        kdata_file.append(df2)


# ---------- upsert policies ----------


def test_upsert_replace(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    df_new = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["600000.SH"],
            "open": [99.0],
            "high": [99.0],
            "low": [99.0],
            "close": [99.0],
            "amount": [1.0],
            "volume": [1.0],
            "close_adj": [99.0],
        }
    )
    kdata_file.upsert(df_new, conflict="replace")
    out = kdata_file.read(stock_codes=["600000.SH"], start="2024-01-02", end="2024-01-02")
    assert len(out) == 1
    assert float(out.iloc[0]["close"]) == 99.0


def test_upsert_ignore(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    df_new = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["600000.SH"],
            "open": [99.0],
            "high": [99.0],
            "low": [99.0],
            "close": [99.0],
            "amount": [1.0],
            "volume": [1.0],
            "close_adj": [99.0],
        }
    )
    kdata_file.upsert(df_new, conflict="ignore")
    out = kdata_file.read(stock_codes=["600000.SH"], start="2024-01-02", end="2024-01-02")
    assert len(out) == 1
    assert float(out.iloc[0]["close"]) == 10.3  # 原值保留


def test_upsert_error(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    df_new = pd.DataFrame(
        {
            "trading_date": ["2024-01-02"],
            "stock_code": ["600000.SH"],
            "open": [99.0],
            "high": [99.0],
            "low": [99.0],
            "close": [99.0],
            "amount": [1.0],
            "volume": [1.0],
            "close_adj": [99.0],
        }
    )
    with pytest.raises(PrimaryKeyConflictError):
        kdata_file.upsert(df_new, conflict="error")


def test_upsert_on_empty_file(kdata_file: DataFile) -> None:
    df = _sample_kdata_rows().iloc[:1]
    n = kdata_file.upsert(df, conflict="replace")
    assert n == 1
    assert kdata_file.exists()


# ---------- stats ----------


def test_stats_on_populated_file(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    s = kdata_file.stats()
    assert s.type_name == "KData"
    assert s.row_count == 3
    assert s.first_date == "2024-01-02"
    assert s.last_date == "2024-01-03"
    assert s.stock_count == 2  # 600000.SH + 000012.SZ
    assert s.file_size_bytes > 0
    assert s.file_sha256 != "missing"


def test_stats_on_missing_file(kdata_file: DataFile) -> None:
    s = kdata_file.stats()
    assert s.row_count == 0
    assert s.first_date is None
    assert s.last_date is None
    assert s.file_size_bytes == 0
    assert s.file_sha256 == "missing"


# ---------- truncate ----------


def test_truncate(kdata_file: DataFile) -> None:
    kdata_file.append(_sample_kdata_rows())
    assert kdata_file.exists()
    kdata_file.truncate()
    assert not kdata_file.exists()
    # truncate 后 append 应能成功
    n = kdata_file.append(_sample_kdata_rows().iloc[:1])
    assert n == 1


# ---------- schema_version guard ----------


def test_schema_version_mismatch_raises(kdata_file: DataFile, tmp_root: Path) -> None:
    """Data_State.json 写入旧版本 schema_version, 读时应拒绝."""
    from newbee.datasource.storage.state import StateTracker

    kdata_file.append(_sample_kdata_rows())  # 写一次, 触发 schema 存在
    # 写一个旧版本 state
    tracker = StateTracker(tmp_root / "data" / "_Manifest" / "Data_State.json")
    fake_stats = kdata_file.stats()
    # 模拟历史版本
    fake_stats.schema_version = "0.9"
    tracker.update("KData", fake_stats)

    with pytest.raises(SchemaVersionError):
        kdata_file.read()