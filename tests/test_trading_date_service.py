"""test_trading_date_service.py — Trading_DateService full_init / daily_update 测试."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from alpha_backend.datasource.service.trading_date import Trading_DateService


def _state_path(root: Path) -> Path:
    return root / "datas" / "_Manifest" / "Data_State.json"


def _read_state(root: Path) -> dict:
    p = _state_path(root)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------- full_init ----------


def test_full_init_writes_csv(tmp_path: Path) -> None:
    svc = Trading_DateService(root=str(tmp_path))
    summary = svc.full_init(start="2024-01-01")

    csv_path = tmp_path / "datas" / "Trading_Date.csv"
    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    assert list(df.columns) == ["trading_date"]
    assert len(df) >= 200  # 2024 整年 + 截至 today 的 2025 (截至 2026/06/25 必然够)
    # ISO 排序
    assert list(df["trading_date"]) == sorted(df["trading_date"].tolist())
    # 全部 ISO 格式
    for s in df["trading_date"].tolist()[:5]:
        date.fromisoformat(s)
    # summary
    assert summary.type_name == "Trading_Date"
    assert summary.row_count == len(df)
    assert summary.first_date is not None
    assert summary.last_date is not None


def test_full_init_idempotent(tmp_path: Path) -> None:
    svc = Trading_DateService(root=str(tmp_path))
    s1 = svc.full_init(start="2024-01-01")
    df1 = pd.read_csv(tmp_path / "datas" / "Trading_Date.csv")
    s2 = svc.full_init(start="2024-01-01")
    df2 = pd.read_csv(tmp_path / "datas" / "Trading_Date.csv")
    # 两次结果一致
    assert df1["trading_date"].tolist() == df2["trading_date"].tolist()
    # 第二次的 rows_added 应是 0 (已有数据完全覆盖)
    assert s2.rows_added == 0
    assert s2.row_count == s1.row_count


def test_full_init_state_tracker(tmp_path: Path) -> None:
    svc = Trading_DateService(root=str(tmp_path))
    svc.full_init(start="2024-01-01")
    state = _read_state(tmp_path)
    types = state.get("types", {})
    assert "Trading_Date" in types
    entry = types["Trading_Date"]
    assert entry["schema_version"] == "1.0"
    assert entry["frequency"] == "static"
    assert entry["row_count"] > 200
    assert entry["first_date"] is not None
    assert entry["last_date"] is not None


# ---------- daily_update ----------


def test_daily_update_no_existing_falls_back_to_full_init(tmp_path: Path) -> None:
    svc = Trading_DateService(root=str(tmp_path))
    summary = svc.daily_update()
    csv_path = tmp_path / "datas" / "Trading_Date.csv"
    assert csv_path.exists()
    assert summary.row_count > 0


def test_daily_update_noop_when_current(tmp_path: Path) -> None:
    svc = Trading_DateService(root=str(tmp_path))
    svc.full_init(start="2024-01-01")
    s1 = svc.daily_update(today=date(2024, 6, 17))
    # 此时 last=2024-06-17. 再 daily_update 同一个 today → no-op
    s2 = svc.daily_update(today=date(2024, 6, 17))
    assert s2.rows_added == 0
    assert s2.row_count == s1.row_count


def test_daily_update_appends_only_new_sessions(tmp_path: Path) -> None:
    svc = Trading_DateService(root=str(tmp_path))
    # 先建到 2024-06-30
    svc.full_init(start="2024-01-01", today=date(2024, 6, 30))
    df_before = pd.read_csv(tmp_path / "datas" / "Trading_Date.csv")
    n_before = len(df_before)
    last_before = df_before["trading_date"].max()

    # 增量到 2024-07-10
    s = svc.daily_update(today=date(2024, 7, 10))
    df_after = pd.read_csv(tmp_path / "datas" / "Trading_Date.csv")
    n_after = len(df_after)

    # 只追加了 6/30 之后的 sessions
    new_rows = df_after[df_after["trading_date"] > last_before]
    assert all(d > last_before for d in new_rows["trading_date"].tolist())
    # 数量合理 (7 月 1-10 之间的 XSHG 交易日)
    assert n_after - n_before == len(new_rows) == s.rows_added
    # 没有 6/30 之前的行被改
    assert df_after[df_after["trading_date"] <= last_before]["trading_date"].tolist() == \
        df_before["trading_date"].tolist()
    # last_date 推到 7-10
    assert s.last_date == "2024-07-10"
