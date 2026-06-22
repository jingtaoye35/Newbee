"""批量拉取全市场数据 (raw + adj) 并落盘.

用法:
    python scripts/fetch_data.py [--universe csi1000] [--start 2020-01-01] [--end 2025-12-31]

逻辑:
    1. 从 StockPool 拿股票列表
    2. 对每只股票:
       a. 拉原始 K 线 (adjust='', 不复权) → data/raw/{stock_id}.parquet
       b. 拉前复权 K 线 (adjust='qfq') → data/adj/{stock_id}.parquet
    3. 进度条 + 失败重试 (由 akshare 适配器提供)
    4. 输出汇总: 总数 / 成功 / 失败 / 耗时
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.universe import StockPool  # noqa: E402
from newbee.data.sources import fetch_stock_hist, FetchSummary  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_for_universe(
    pool: StockPool,
    *,
    start: date,
    end: date,
    raw_dir: Path,
    adj_dir: Path,
    progress: bool = True,
    skip_existing: bool = True,
) -> FetchSummary:
    """拉 raw + adj 两份 K 线."""
    # 拿当前 pool 中所有 stock_id
    df = pool.export()
    if df.empty:
        raise RuntimeError("池子为空, 请先跑 init_universe.py")
    stock_ids = df["stock_id"].tolist()

    # 准备 raw 目录
    raw_dir.mkdir(parents=True, exist_ok=True)
    adj_dir.mkdir(parents=True, exist_ok=True)

    # 进度条
    iter_ids = stock_ids
    if progress:
        try:
            from tqdm import tqdm

            iter_ids = tqdm(stock_ids, desc="[fetch]")
        except ImportError:
            logger.warning("[fetch] tqdm 未安装, 无进度条")

    failed: list[str] = []
    t0 = time.time()
    for sid in iter_ids:
        # raw (不复权) — 走新浪源 (稳定)
        raw_path = raw_dir / f"{sid}.parquet"
        if skip_existing and raw_path.exists():
            pass
        else:
            try:
                fetch_stock_hist(
                    sid, start=start, end=end, adjust="",
                    use_cache=True, raw_dir=raw_dir, source="sina",
                )
            except Exception as e:
                logger.error(f"[fetch] raw {sid} 失败: {e!r}")
                failed.append(f"raw:{sid}")

        # adj (前复权) — 走新浪源
        adj_path = adj_dir / f"{sid}.parquet"
        if skip_existing and adj_path.exists():
            pass
        else:
            try:
                fetch_stock_hist(
                    sid, start=start, end=end, adjust="qfq",
                    use_cache=True, raw_dir=adj_dir, source="sina",
                )
            except Exception as e:
                logger.error(f"[fetch] adj {sid} 失败: {e!r}")
                failed.append(f"adj:{sid}")

    elapsed = time.time() - t0
    summary = FetchSummary(
        total=len(stock_ids),
        success=len(stock_ids) - len(set(failed)),
        failed=failed,
        elapsed_sec=elapsed,
    )
    logger.info(f"[fetch] 完成: {summary}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="批量拉取中证 1000 全量 5 年数据")
    parser.add_argument("--universe", default="csi1000", help="universe 名 (默认 csi1000)")
    parser.add_argument("--start", default="2020-01-01", help="起始日 (默认 2020-01-01)")
    parser.add_argument("--end", default="2025-12-31", help="截止日 (默认 2025-12-31)")
    parser.add_argument("--raw-dir", default="data/raw", type=Path)
    parser.add_argument("--adj-dir", default="data/adj", type=Path)
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="强制重下 (默认 skip 已存在文件)",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    pool = StockPool.load()
    logger.info(
        f"[fetch] universe={args.universe} | {start}~{end} | "
        f"pool.size={pool.size()} | skip_existing={not args.no_skip}"
    )

    summary = fetch_for_universe(
        pool,
        start=start,
        end=end,
        raw_dir=args.raw_dir,
        adj_dir=args.adj_dir,
        skip_existing=not args.no_skip,
    )

    if summary.failed:
        logger.warning(
            f"[fetch] {len(summary.failed)} 个失败, 例如: {summary.failed[:5]}"
        )
    else:
        logger.info("[fetch] ✓ 全部成功")


if __name__ == "__main__":
    main()