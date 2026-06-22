"""`newbee data update --dry-run` 测试 (零网络, 零写)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data import incremental  # noqa: E402
from newbee.data import fetch_state  # noqa: E402


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    (tmp_path / "raw").mkdir()
    (tmp_path / "adj").mkdir()
    (tmp_path / "_manifest").mkdir()
    return tmp_path


def test_build_plan_does_not_call_network(tmp_root: Path):
    """build_plan 应当完全本地化, 不 import akshare, 不发请求."""
    # 若 build_plan 内部 import akshare, 这条断言会失败
    with patch.dict(sys.modules, {"akshare": None}):
        plan = incremental.build_plan(
            categories=["raw", "adj"], root=tmp_root
        )
    # plan 应有 raw + adj 两条
    cats = {p.category for p in plan.plans}
    assert cats == {"raw", "adj"}


def test_build_plan_empty_root(tmp_root: Path):
    """无任何数据时, raw / adj 都标记为非 up-to-date."""
    plan = incremental.build_plan(
        categories=["raw", "adj"], root=tmp_root
    )
    for p in plan.plans:
        assert not p.up_to_date
        assert p.missing_days >= 1


def test_dry_run_does_not_write_fetch_state(tmp_root: Path):
    """run_update 内部不调 update_state 的情况下, fetch_state.json 不应被创建."""
    # 直接验证: build_plan() 不写 fetch_state
    incremental.build_plan(categories=["raw", "adj"], root=tmp_root)
    assert not (tmp_root / "_manifest" / "fetch_state.json").exists()


def test_dry_run_exit_code_is_zero(tmp_root: Path, capsys):
    """scripts/fetch_incremental.py --dry-run 应当 exit 0."""
    import subprocess

    script = PROJECT_ROOT / "scripts" / "fetch_incremental.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--data-root",
            str(tmp_root),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout
    assert "update plan" in out
    assert "raw" in out
    assert "adj" in out