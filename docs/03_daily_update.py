"""03_daily_update — 每日增量拉取演示.

逻辑:
    1. 打印当前 fetch_state (覆盖范围)
    2. 打印 dry-run 计划 (零网络请求)
    3. 实际增量拉取 (从 last_date+1 到最新已收盘交易日)
    4. 重新打印 fetch_state 验证已更新

运行:
    /opt/anaconda3/envs/py312/bin/python docs/03_daily_update.py [--dry-run]

注意: 首次运行前需要先跑 `scripts/init_universe.py` 与 `scripts/fetch_data.py`
至少一次, 让 `data/raw/` 与 `data/adj/` 有内容; 否则会从 universe pool 的
created_at 开始全量回填.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.fetch_state import (  # noqa: E402
    is_universe_stale,
    progress_summary,
    read_state,
)
from newbee.data.universe import StockPool  # noqa: E402
from newbee.data.incremental import build_plan, run_update  # noqa: E402
from newbee.data.calendar import latest_trading_day  # noqa: E402
from newbee.utils import logger  # noqa: E402


def print_status(root: Path) -> None:
    """打印 fetch_state 覆盖范围 + universe stale 警告."""
    state = read_state(root)
    pool = StockPool.load(root / "universe" / "pool.parquet")
    current_sha = pool._compute_sha() if pool.path.exists() else None  # type: ignore[attr-defined]

    print(f"=== fetch_state ===")
    print(f"data_root:      {root}")
    print(f"fetch_state:    {'present' if not state.is_fresh else 'missing (fresh)'}")
    print(f"universe_sha:   state={state.universe_sha} current={current_sha}")
    if is_universe_stale(state, current_sha):
        print(f"  WARN: universe_sha 不一致 (universe 已变化)")

    summary = progress_summary(state)
    if not summary:
        print("(no categories recorded yet)")
    else:
        print(f"\n{'category':<12} {'coverage':<50}")
        print("-" * 70)
        for cat, cov in summary.items():
            print(f"{cat:<12} {cov}")
    print(f"\nlatest_trading_day = {latest_trading_day()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="每日增量拉取演示")
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划, 不实际下载, 不写 fetch_state",
    )
    parser.add_argument("--no-progress", action="store_true")
    args = parser.parse_args()

    print(f"[1] 当前 fetch_state 状态:")
    print_status(args.data_root)

    print(f"\n[2] dry-run 计划预览:")
    plan = build_plan(categories=["raw", "adj"], root=args.data_root)
    print(plan.render_table())

    if args.dry_run:
        logger.info("[dry-run] exit 0, 未触发任何网络请求")
        return 0

    print(f"\n[3] 执行增量拉取 (raw + adj)...")
    result = run_update(
        categories=["raw", "adj"],
        root=args.data_root,
        progress=not args.no_progress,
    )
    print(f"\n=== Update Result ===")
    for cat, summary in result.summaries.items():
        print(
            f"  {cat}: success={summary.success} failed={len(summary.failed)} "
            f"elapsed={summary.elapsed_sec:.1f}s"
        )
    if result.skipped:
        print(f"  skipped: {result.skipped}")

    print(f"\n[4] 跑完后 fetch_state 状态:")
    print_status(args.data_root)

    if result.has_failures():
        print(f"\nWARN: {result.total_failed} 个 stock 失败")
        return 1
    print(f"\n✓ done")
    return 0


if __name__ == "__main__":
    sys.exit(main())