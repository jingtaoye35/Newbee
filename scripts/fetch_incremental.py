"""增量拉取 wrapper 脚本 (复用 newbee.data.incremental).

用法:
    python scripts/fetch_incremental.py [--categories raw adj] [--dry-run]

逻辑:
    1. build_plan() 算每类数据的 resume 范围
    2. (非 dry-run) 调 run_update() 逐只股票 append
    3. 失败聚合输出
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.incremental import build_plan, run_update  # noqa: E402
from newbee.data.storage import DEFAULT_DATA_ROOT  # noqa: E402
from newbee.utils import logger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="增量拉取 raw / adj 数据")
    parser.add_argument(
        "--categories",
        nargs="+",
        default=["raw", "adj"],
        choices=["raw", "adj", "universe", "pit", "alpha", "features"],
        help="要更新的 category (默认 raw adj)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印计划, 不实际下载, 不写 fetch_state",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help=f"data 根目录 (默认 {DEFAULT_DATA_ROOT})",
    )
    parser.add_argument(
        "--source",
        default="sina",
        choices=["sina", "em", "tx"],
        help="数据源 (默认 sina)",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="不打 tqdm 进度条",
    )
    args = parser.parse_args()

    # 1. 计划
    plan = build_plan(categories=args.categories, root=args.data_root)
    print(plan.render_table())

    if args.dry_run:
        logger.info("[dry-run] exit 0, 未触发任何网络请求, 未写入 fetch_state")
        return 0

    # 2. 执行
    result = run_update(
        categories=args.categories,
        root=args.data_root,
        progress=not args.no_progress,
        source=args.source,
    )

    # 3. 汇总
    print("\n=== Update Result ===")
    for cat, summary in result.summaries.items():
        print(f"  {cat}: success={summary.success} failed={len(summary.failed)} "
              f"elapsed={summary.elapsed_sec:.1f}s")
    if result.skipped:
        print(f"  skipped: {result.skipped}")
    if result.has_failures():
        print(f"\nWARN: {result.total_failed} 个 stock 失败")
        return 1
    print("\n✓ All up-to-date")
    return 0


if __name__ == "__main__":
    sys.exit(main())