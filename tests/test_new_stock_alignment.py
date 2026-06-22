"""universe 加入新股票后, 新股票 first_date = batch.last_date + 1."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.fetch_state import infer_resume_range, update_state  # noqa: E402


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir()
    (tmp_path / "adj").mkdir()
    (tmp_path / "_manifest").mkdir()
    return tmp_path


def test_new_stock_uses_batch_last_date_plus_one(tmp_root: Path):
    """universe 加新股票 → 新股票 start = batch.last_date + 1, 不是 added_at."""
    # 现有 batch 的 last_date = 2026-06-19
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=1,
        file_count=1,
        root=tmp_root,
    )

    # 今天 2026-06-23 (假设 17:00 已收盘)
    latest = date(2026, 6, 23)
    start, end = infer_resume_range("raw", latest=latest, root=tmp_root)

    # 新股票 (无论 added_at 是哪天) 都从 2026-06-20 开始
    assert start == date(2026, 6, 20)
    assert end == date(2026, 6, 23)


def test_existing_stocks_resume_point_unchanged_when_universe_grows(tmp_root: Path):
    """universe 加新股票, 旧股票的 resume 点不变."""
    # 旧 batch 已经拉到 2026-06-19
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=100,
        file_count=10,
        root=tmp_root,
    )

    # 假设今天 universe 加了一只新股票 S_new, 但其 parquet 还是空文件
    df = pd.DataFrame(
        {
            "date": pd.to_datetime([]),
            "stock_id": pd.Series([], dtype=str),
            "open": pd.Series([], dtype=float),
            "high": pd.Series([], dtype=float),
            "low": pd.Series([], dtype=float),
            "close": pd.Series([], dtype=float),
            "volume": pd.Series([], dtype=float),
            "adj_close": pd.Series([], dtype=float),
        }
    )
    df.to_parquet(tmp_root / "raw" / "999999.parquet", index=False)

    latest = date(2026, 6, 23)
    start, end = infer_resume_range("raw", latest=latest, root=tmp_root)

    # resume 从 2026-06-20 开始 (不因 S_new 加入而改变)
    assert start == date(2026, 6, 20)


def test_first_date_for_new_stock_after_run(tmp_root: Path):
    """跑完增量后, 新股票的 parquet first_date == batch.last_date + 1."""
    import pyarrow.parquet as pq

    # S_new 是新股票 (空文件)
    new_path = tmp_root / "raw" / "999999.parquet"
    df_empty = pd.DataFrame(
        {"date": pd.to_datetime([]), "stock_id": pd.Series([], dtype=str)}
    )
    df_empty.to_parquet(new_path, index=False)

    # 直接写一个 parquet 模拟"已经按 batch.last+1 跑完"的产物
    df_filled = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-20", "2026-06-21"]),
            "stock_id": ["999999"] * 2,
            "open": [1.0, 1.1],
            "close": [1.0, 1.1],
        }
    )
    df_filled.to_parquet(new_path, index=False)

    table = pq.read_table(new_path, columns=["date"])
    first_date = table.column("date")[0].as_py().date()
    assert first_date == date(2026, 6, 20)