"""test_kdata_service.py — KDataService 单元测试 (mock akshare + 手工写 Universe)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from newbee.datasource.service.kdata import KDataService


def _write_universe(root: Path, codes: list[str], ipo_date: str = "1990-01-01") -> None:
    df = pd.DataFrame(
        [(i, c, ipo_date) for i, c in enumerate(codes)],
        columns=["stock_index", "stock_code", "ipo_date"],
    )
    df["stock_index"] = df["stock_index"].astype("int32")
    path = root / "data" / "Universe.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _fake_kdata_df(stock_code: str, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trading_date": dates,
            "stock_code": [stock_code] * len(dates),
            "open": [10.0] * len(dates),
            "high": [10.5] * len(dates),
            "low": [9.5] * len(dates),
            "close": [10.3] * len(dates),
            "amount": [1e7] * len(dates),
            "volume": [1e6] * len(dates),
            "close_post_adj": [10.3] * len(dates),
        }
    )


def test_full_init_requires_universe(tmp_path: Path) -> None:
    """universe 为空 → RuntimeError."""
    svc = KDataService(root=str(tmp_path))
    with pytest.raises(RuntimeError, match="universe 为空"):
        svc.full_init(start="2020-01-01")


def test_full_init_writes_kdata_parquet(tmp_path: Path) -> None:
    _write_universe(tmp_path, ["600000.SH", "000012.SZ"])

    def fake_fetch(stock_code: str, **kwargs) -> pd.DataFrame:
        return _fake_kdata_df(stock_code, ["2024-01-02", "2024-01-03"])

    with patch("newbee.datasource.service.kdata.fetch_stock_hist", side_effect=fake_fetch):
        svc = KDataService(root=str(tmp_path))
        summary = svc.full_init(start="2024-01-01")

    assert summary.success == 2
    assert summary.failed == []
    assert summary.row_count == 4  # 2 stocks × 2 dates

    # 验证 parquet 存在 + 内容
    assert (tmp_path / "data" / "KData.parquet").exists()
    df = pd.read_parquet(tmp_path / "data" / "KData.parquet")
    assert len(df) == 4
    assert set(df["stock_code"]) == {"600000.SH", "000012.SZ"}


def test_daily_update_extends(tmp_path: Path) -> None:
    """已有数据 → daily_update 拉新区间并 upsert."""
    # 先全量初始化 2024-01-02
    _write_universe(tmp_path, ["600000.SH"])
    with patch(
        "newbee.datasource.service.kdata.fetch_stock_hist",
        side_effect=lambda code, **kw: _fake_kdata_df(code, ["2024-01-02"]),
    ):
        KDataService(root=str(tmp_path)).full_init(start="2024-01-01")

    # 现在跑 daily_update, latest=2024-01-05, 应该拉 01-03 ~ 01-05
    with patch(
        "newbee.datasource.service.kdata.fetch_stock_hist",
        side_effect=lambda code, **kw: _fake_kdata_df(code, ["2024-01-03", "2024-01-05"]),
    ):
        summary = KDataService(root=str(tmp_path)).daily_update(today=date(2024, 1, 5))

    assert summary.success == 1
    df = pd.read_parquet(tmp_path / "data" / "KData.parquet")
    assert len(df) == 3
    assert set(df["trading_date"]) == {"2024-01-02", "2024-01-03", "2024-01-05"}


def test_daily_update_up_to_date(tmp_path: Path) -> None:
    """last_date == latest → up-to-date, success=0."""
    _write_universe(tmp_path, ["600000.SH"])
    with patch(
        "newbee.datasource.service.kdata.fetch_stock_hist",
        side_effect=lambda code, **kw: _fake_kdata_df(code, ["2024-01-02", "2024-01-03"]),
    ):
        KDataService(root=str(tmp_path)).full_init(start="2024-01-01")

    # daily_update with latest=2024-01-03 → 已 up-to-date
    summary = KDataService(root=str(tmp_path)).daily_update(today=date(2024, 1, 3))
    assert summary.success == 0
    assert summary.last_date == "2024-01-03"


def test_read_window(tmp_path: Path) -> None:
    _write_universe(tmp_path, ["600000.SH"])
    with patch(
        "newbee.datasource.service.kdata.fetch_stock_hist",
        side_effect=lambda code, **kw: _fake_kdata_df(
            code, ["2024-01-02", "2024-01-03", "2024-01-04"]
        ),
    ):
        KDataService(root=str(tmp_path)).full_init(start="2024-01-01")

    svc = KDataService(root=str(tmp_path))
    df = svc.read_window("2024-01-02", "2024-01-03")
    assert len(df) == 2


def test_schema_version_guard(tmp_path: Path) -> None:
    """Data_State.json schema_version 不匹配 → read_window 拒绝."""
    _write_universe(tmp_path, ["600000.SH"])
    with patch(
        "newbee.datasource.service.kdata.fetch_stock_hist",
        side_effect=lambda code, **kw: _fake_kdata_df(code, ["2024-01-02"]),
    ):
        KDataService(root=str(tmp_path)).full_init(start="2024-01-01")

    # 手动改 state schema_version 为旧版
    from newbee.datasource.storage.state import StateTracker

    state_path = tmp_path / "data" / "_Manifest" / "Data_State.json"
    tracker = StateTracker(state_path)
    full = tracker.read_full()
    full["types"]["KData"]["schema_version"] = "0.9"
    state_path.write_text(__import__("json").dumps(full, indent=2, ensure_ascii=False))

    with pytest.raises(Exception):  # SchemaVersionError
        KDataService(root=str(tmp_path)).read_window("2024-01-02", "2024-01-02")