"""fetch_state 模块测试 (read_state / update_state 原子写 / 并发安全)."""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data import fetch_state  # noqa: E402
from newbee.data.fetch_state import (  # noqa: E402
    STATE_VERSION,
    FetchState,
    read_state,
    update_state,
)


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """每个 case 一个干净的 tmp_root (data/ 子目录)."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "adj").mkdir()
    (tmp_path / "_manifest").mkdir()
    return tmp_path


# ---------- read_state ----------


def test_read_state_on_missing_file_returns_fresh(tmp_root: Path):
    """fetch_state.json 不存在 → fresh FetchState."""
    state = read_state(tmp_root)
    assert state.is_fresh
    assert state.universe_sha is None
    assert state.categories == {}


def test_read_state_round_trips_dates(tmp_root: Path):
    """写入 ISO 日期字符串, 读回 date 对象."""
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=1523456,
        file_count=1000,
        root=tmp_root,
    )
    state = read_state(tmp_root)
    cov = state.categories["raw"]
    assert cov.first_date == date(2020, 1, 2)
    assert cov.last_date == date(2026, 6, 19)
    assert cov.row_count == 1523456
    assert cov.file_count == 1000


def test_read_state_on_corrupted_file_raises(tmp_root: Path):
    """JSON 损坏应当抛 JSONDecodeError (而不是 silently 返回 fresh)."""
    path = tmp_root / "_manifest" / "fetch_state.json"
    path.write_text("{not valid json")
    with pytest.raises(json.JSONDecodeError):
        read_state(tmp_root)


# ---------- update_state 原子写 ----------


def test_update_state_creates_state_file(tmp_root: Path):
    """首次 update_state 应当创建 fetch_state.json."""
    assert not (tmp_root / "_manifest" / "fetch_state.json").exists()
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=100,
        file_count=1,
        root=tmp_root,
    )
    assert (tmp_root / "_manifest" / "fetch_state.json").exists()


def test_update_state_only_mutates_target_category(tmp_root: Path):
    """调 update_state('raw') 不动 'adj' 等其他 category."""
    update_state("raw", first_date=date(2020, 1, 2), last_date=date(2026, 6, 19),
                 row_count=1, file_count=1, root=tmp_root)
    update_state("adj", first_date=date(2020, 1, 2), last_date=date(2026, 6, 19),
                 row_count=2, file_count=2, root=tmp_root)
    update_state("raw", first_date=date(2020, 1, 2), last_date=date(2026, 6, 20),
                 row_count=11, file_count=11, root=tmp_root)  # 只动 raw
    state = read_state(tmp_root)
    assert state.categories["raw"].row_count == 11
    assert state.categories["adj"].row_count == 2  # 未被覆盖


def test_update_state_bootstrap_first_date(tmp_root: Path):
    """first_date=None 且旧值为 None 时, 用 last_date 兜底."""
    update_state(
        "raw",
        first_date=None,
        last_date=date(2026, 6, 19),
        row_count=1,
        file_count=1,
        root=tmp_root,
    )
    state = read_state(tmp_root)
    assert state.categories["raw"].first_date == date(2026, 6, 19)
    assert state.categories["raw"].last_date == date(2026, 6, 19)


def test_update_state_keeps_existing_first_date(tmp_root: Path):
    """first_date=None 且旧值非 None 时, 保留旧值."""
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2020, 12, 31),
        row_count=1,
        file_count=1,
        root=tmp_root,
    )
    update_state(
        "raw",
        first_date=None,  # 显式 None
        last_date=date(2026, 6, 19),
        row_count=11,
        file_count=11,
        root=tmp_root,
    )
    state = read_state(tmp_root)
    assert state.categories["raw"].first_date == date(2020, 1, 2)
    assert state.categories["raw"].last_date == date(2026, 6, 19)


def test_concurrent_updates_do_not_corrupt_state(tmp_root: Path):
    """多线程并发 update_state 应当保持 JSON 完整 (不破坏文件).

    spec/fetch-state-tracking: 'the final fetch_state.json is either the old
    version or the new version — never a half-written file'.
    M1 不保证 lost-update 防护 (那是更高层 file lock 的事).
    """
    import json as _json

    n_threads = 8
    barrier = threading.Barrier(n_threads)
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            barrier.wait()
            update_state(
                f"cat_{i}",
                first_date=date(2020, 1, 1),
                last_date=date(2026, 6, 19),
                row_count=i,
                file_count=i,
                root=tmp_root,
            )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 update 出错: {errors}"
    # 文件必须能 parse (没有半写状态)
    raw = (tmp_root / "_manifest" / "fetch_state.json").read_text()
    payload = _json.loads(raw)  # 不抛异常 = 通过
    assert "categories" in payload
    # 至少有一个 category 写成功 (last-write-wins)
    assert len(payload["categories"]) >= 1


# ---------- infer_resume_range ----------


def test_infer_resume_range_from_state(tmp_root: Path):
    """有 last_date 时, 从 last+1 开始."""
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=1,
        file_count=1,
        root=tmp_root,
    )
    start, end = fetch_state.infer_resume_range(
        "raw", latest=date(2026, 6, 22), root=tmp_root
    )
    assert start == date(2026, 6, 20)
    assert end == date(2026, 6, 22)


def test_infer_resume_range_up_to_date_returns_empty(tmp_root: Path):
    """last_date >= latest → 空区间 (start > end)."""
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 22),
        row_count=1,
        file_count=1,
        root=tmp_root,
    )
    start, end = fetch_state.infer_resume_range(
        "raw", latest=date(2026, 6, 22), root=tmp_root
    )
    assert start > end


def test_infer_resume_range_bootstrap_from_scan(tmp_root: Path, monkeypatch):
    """无 fetch_state 但有 parquet 文件 → bootstrap 扫盘."""
    import pandas as pd

    # 写一个 raw parquet
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-15", "2026-06-16", "2026-06-17"]),
            "stock_id": ["600000"] * 3,
            "open": [1.0, 1.1, 1.2],
            "high": [1.0, 1.1, 1.2],
            "low": [1.0, 1.1, 1.2],
            "close": [1.0, 1.1, 1.2],
            "volume": [100, 200, 300],
            "adj_close": [1.0, 1.1, 1.2],
        }
    )
    df.to_parquet(tmp_root / "raw" / "600000.parquet", index=False)

    start, end = fetch_state.infer_resume_range(
        "raw", latest=date(2026, 6, 22), root=tmp_root
    )
    assert start == date(2026, 6, 18)
    assert end == date(2026, 6, 22)


def test_infer_resume_range_empty_returns_universe_default(tmp_root: Path, monkeypatch):
    """无任何数据 → 用 universe created_at (本测试用 fallback 2020-01-01)."""
    # monkeypatch universe default to a known date
    monkeypatch.setattr(
        fetch_state, "_universe_default_start", lambda root: date(2021, 6, 1)
    )
    start, end = fetch_state.infer_resume_range(
        "raw", latest=date(2026, 6, 22), root=tmp_root
    )
    assert start == date(2021, 6, 1)
    assert end == date(2026, 6, 22)


# ---------- progress_summary ----------


def test_progress_summary_format(tmp_root: Path):
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=100,
        file_count=5,
        root=tmp_root,
    )
    state = read_state(tmp_root)
    summary = fetch_state.progress_summary(state)
    assert "raw" in summary
    # days = (2026-06-19 - 2020-01-02).days + 1 = 2360 + 1 = 2361
    assert summary["raw"] == "first=2020-01-02 last=2026-06-19 days=2361"


def test_progress_summary_skips_empty_categories(tmp_root: Path):
    update_state(
        "raw",
        first_date=date(2020, 1, 2),
        last_date=date(2026, 6, 19),
        row_count=100,
        file_count=5,
        root=tmp_root,
    )
    # 写一个空 category (first=None, last=None)
    update_state("pit", first_date=None, last_date=None, row_count=0, file_count=0, root=tmp_root)
    state = read_state(tmp_root)
    summary = fetch_state.progress_summary(state)
    assert "raw" in summary
    assert "pit" not in summary


# ---------- is_universe_stale ----------


def test_is_universe_stale_true_on_mismatch():
    state = FetchState(universe_sha="aaa", categories={}, updated_at="now")
    assert fetch_state.is_universe_stale(state, "bbb") is True


def test_is_universe_stale_false_on_match():
    state = FetchState(universe_sha="aaa", categories={}, updated_at="now")
    assert fetch_state.is_universe_stale(state, "aaa") is False


def test_is_universe_stale_false_when_state_has_no_sha():
    state = FetchState(universe_sha=None, categories={}, updated_at="now")
    # state 没记录 sha → 不算 stale (初次)
    assert fetch_state.is_universe_stale(state, "aaa") is False