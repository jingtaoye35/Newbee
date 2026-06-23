"""test_universe_service.py — UniverseService 单元测试 (mock akshare)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from newbee.datasource.service.universe import UniverseService


def _write_universe(root: Path, rows: list[tuple[int, str, str]]) -> None:
    """手工写 Universe.parquet."""
    df = pd.DataFrame(
        rows,
        columns=["stock_index", "stock_code", "ipo_date"],
    )
    df["stock_index"] = df["stock_index"].astype("int32")
    df["stock_code"] = df["stock_code"].astype(str)
    df["ipo_date"] = df["ipo_date"].astype(str)
    path = root / "data" / "Universe.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def test_active_mask_basic(tmp_path: Path) -> None:
    _write_universe(
        tmp_path,
        [
            (0, "600000.SH", "1990-01-01"),  # 早期
            (1, "000012.SZ", "2010-01-01"),
            (2, "300750.SZ", "2018-06-11"),
        ],
    )
    svc = UniverseService(root=str(tmp_path))
    mask = svc.active_mask("2015-01-01")
    # read() 默认按 (trading_date, stock_code) 排序; Universe 无 trading_date,
    # 仅按 stock_code 排序: 000012.SZ / 300750.SZ / 600000.SH
    # 对应 ipo: 2010 / 2018 / 1990 → asof=2015-01-01: True / False / True
    assert mask.tolist() == [True, False, True]


def test_active_mask_pre_ipo(tmp_path: Path) -> None:
    """上市前 → False."""
    _write_universe(
        tmp_path,
        [(0, "600000.SH", "2010-01-01")],
    )
    svc = UniverseService(root=str(tmp_path))
    mask = svc.active_mask("2009-12-31")
    assert mask.tolist() == [False]


def test_active_mask_disclosure_day(tmp_path: Path) -> None:
    """上市当日算 active."""
    _write_universe(
        tmp_path,
        [(0, "600000.SH", "2010-01-01")],
    )
    svc = UniverseService(root=str(tmp_path))
    mask = svc.active_mask("2010-01-01")
    assert mask.tolist() == [True]


def test_size(tmp_path: Path) -> None:
    _write_universe(
        tmp_path,
        [(i, f"{600000 + i:06d}.SH", "2000-01-01") for i in range(5)],
    )
    svc = UniverseService(root=str(tmp_path))
    assert svc.size() == 5


def test_all_codes(tmp_path: Path) -> None:
    rows = [
        (0, "600000.SH", "1990-01-01"),
        (1, "000012.SZ", "2010-01-01"),
    ]
    _write_universe(tmp_path, rows)
    svc = UniverseService(root=str(tmp_path))
    codes = svc.all_codes()
    # read() 默认按 (trading_date, stock_code) 排序
    assert sorted(codes) == sorted(["600000.SH", "000012.SZ"])


def test_full_init_writes_file(tmp_path: Path) -> None:
    """mock akshare: 1000 只成分股 + 2 只真实 IPO 日期."""
    fake_codes = [f"{600000 + i:06d}.SH" if i % 2 == 0 else f"{i:06d}.SZ" for i in range(1000)]
    # 1000 中只有前 2 只给真实 IPO
    ipo_calls = {"n": 0}

    def fake_fetch_ipo(code: str) -> str | None:
        ipo_calls["n"] += 1
        if ipo_calls["n"] <= 2:
            return "2010-01-01"
        return None

    with patch(
        "newbee.datasource.service.universe.fetch_index_constituents",
        return_value=fake_codes,
    ):
        with patch(
            "newbee.datasource.service.universe.fetch_ipo_date",
            side_effect=fake_fetch_ipo,
        ):
            svc = UniverseService(root=str(tmp_path))
            result = svc.full_init()

    assert result["total"] == 1000
    assert result["added"] == 1000
    assert result["with_ipo"] == 2

    # 验证 parquet 存在
    assert (tmp_path / "data" / "Universe.parquet").exists()
    df = pd.read_parquet(tmp_path / "data" / "Universe.parquet")
    assert len(df) == 1000
    assert (df["stock_index"].diff().dropna() == 1).all()  # 连续 idx


def test_full_init_idempotent(tmp_path: Path) -> None:
    """二次 full_init (同 index) 不重复添加."""
    fake_codes = ["600000.SH", "000012.SZ"]
    with patch(
        "newbee.datasource.service.universe.fetch_index_constituents",
        return_value=fake_codes,
    ):
        with patch(
            "newbee.datasource.service.universe.fetch_ipo_date",
            return_value="2010-01-01",
        ):
            svc = UniverseService(root=str(tmp_path))
            svc.full_init()
            result2 = svc.full_init()
    assert result2["added"] == 0
    df = pd.read_parquet(tmp_path / "data" / "Universe.parquet")
    assert len(df) == 2


def test_active_mask_no_file(tmp_path: Path) -> None:
    svc = UniverseService(root=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        svc.active_mask("2024-01-01")