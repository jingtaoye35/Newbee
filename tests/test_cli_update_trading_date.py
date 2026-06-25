"""test_cli_update_trading_date.py — `data update --type Trading_Date` 分支测试.

覆盖:
- cmd_data_update 在 --type Trading_Date 时路由到 Trading_DateService.daily_update,
  target = today + 1 天, 打印标准摘要, 退出码 0.
- `data update --help` 在 --type help 文本里列出 Trading_Date.
"""
from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path

import pytest

from newbee.datasource import cli as cli_mod
from newbee.datasource.service.trading_date import Trading_DateService
from newbee.datasource.service.trading_date import UpdateSummary as RealUpdateSummary


def _make_args(tmp_path: Path) -> Namespace:
    """构造 cmd_data_update 期望的 Namespace."""
    return Namespace(
        type="Trading_Date",
        source="sina",
        index="csi1000",
        backdate="2020-01-01",
        data_root=tmp_path,
    )


def test_cmd_data_update_trading_date_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Trading_Date 分支: 调用 Trading_DateService.daily_update(today=tomorrow), 打印摘要, 退出 0."""

    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, *, root: str) -> None:
            captured["root"] = root

        def daily_update(self, *, today: date) -> RealUpdateSummary:
            captured["today"] = today
            return RealUpdateSummary(
                type_name="Trading_Date",
                rows_added=2,
                elapsed_sec=0.123,
                first_date="2026-06-24",
                last_date="2026-06-26",
                row_count=3999,
            )

    monkeypatch.setattr(
        "newbee.datasource.service.trading_date.Trading_DateService",
        FakeService,
    )

    rc = cli_mod.cmd_data_update(_make_args(tmp_path))

    out = capsys.readouterr().out
    assert rc == 0, f"expected rc=0, got {rc}; stdout={out!r}"
    assert "Trading_Date update:" in out
    assert "rows_added=2" in out
    assert "last=2026-06-26" in out
    assert "rows=3999" in out
    assert "elapsed=0.1s" in out

    # 路由到了 tmp_path, 目标日期 = today + 1 天
    assert captured["root"] == str(tmp_path)
    assert captured["today"] == date.today() + timedelta(days=1)


def test_cmd_data_update_trading_date_no_op(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """已最新时 rows_added=0, 摘要仍然打印."""

    class FakeService:
        def __init__(self, *, root: str) -> None:
            pass

        def daily_update(self, *, today: date) -> RealUpdateSummary:
            return RealUpdateSummary(
                type_name="Trading_Date",
                rows_added=0,
                elapsed_sec=0.05,
                first_date="2010-01-04",
                last_date="2026-06-26",
                row_count=4000,
            )

    monkeypatch.setattr(
        "newbee.datasource.service.trading_date.Trading_DateService",
        FakeService,
    )

    rc = cli_mod.cmd_data_update(_make_args(tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert "rows_added=0" in out
    assert "last=2026-06-26" in out


def test_update_help_lists_trading_date() -> None:
    """`python -m newbee.datasource.cli update --help` 必须在 --type 段落里出现 Trading_Date."""
    result = subprocess.run(
        [sys.executable, "-m", "newbee.datasource.cli", "update", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Trading_Date" in result.stdout, (
        f"--type help 未列出 Trading_Date; got:\n{result.stdout}"
    )
    # 同时确认其他四类也仍在 (防止 help 改写意外删掉)
    for name in ("KData", "Trade_Status", "Stock_Basic_Data", "Universe"):
        assert name in result.stdout, f"--type help 应列出 {name}; got:\n{result.stdout}"
