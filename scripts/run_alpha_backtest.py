"""Alpha 回测入口: 从配置文件读入, 跑 alpha 回测.

用法:
  python scripts/run_alpha_backtest.py --config configs/factors/momentum_20.yaml
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.universe import StockPool
from newbee.data.storage import load_bars_from_parquet
from newbee.alpha_store import AlphaStore
from newbee.engines.backtest_alpha import (
    run_alpha_backtest_from_store,
    forward_returns_from_prices,
)
from newbee.utils import logger


def main():
    parser = argparse.ArgumentParser(description="Alpha backtest")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--universe", type=Path,
        default=PROJECT_ROOT / "data" / "universe" / "pool.parquet")
    parser.add_argument("--data-root", type=Path,
        default=PROJECT_ROOT / "data")
    parser.add_argument("--out-dir", type=Path,
        default=PROJECT_ROOT / "data" / "alpha" / "results")
    args = parser.parse_args()

    # 读 YAML
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    logging.info(f"配置: {args.config.name}")

    # 加载数据
    pool = StockPool.load(args.universe)
    logger.info(f"Pool: {pool.size()} stocks")

    start = date.fromisoformat(cfg["data"]["start"])
    end = date.fromisoformat(cfg["data"]["end"])
    bars = load_bars_from_parquet(
        stock_ids=pool.export()["stock_id"].tolist(),
        start=start, end=end,
        kind="adj", root=args.data_root,
    )
    logger.info(
        f"Bars: T={len(bars.dates)}, N={bars.N}, "
        f"range={bars.dates[0]} ~ {bars.dates[-1]}"
    )

    # 加载 alpha (从 store 读, 不重算)
    strategy_id = cfg["factor"]["name"] + "_" + cfg["factor"].get("version", "1.0")
    alpha_store = AlphaStore(PROJECT_ROOT / "data" / "alpha" / strategy_id, pool)
    if not alpha_store.list_dates():
        logger.warning(f"alpha_store 为空: data/alpha/{strategy_id}/")
        logger.info("   先跑: python docs/01_first_factor.py")
        return 1

    # 读 prices
    prices = bars.adj_close

    # 跑 alpha 回测
    horizon = cfg.get("evaluation", {}).get("horizon", 20)
    result = run_alpha_backtest_from_store(
        alpha_store=alpha_store,
        dates=bars.dates,
        forward_returns=forward_returns_from_prices(prices, horizon=horizon),
        n_groups=cfg.get("evaluation", {}).get("n_groups", 10),
    )
    summary = result.summary_dict()

    # 输出
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{strategy_id}_summary.json"

    import json
    with open(out_path, "w") as f:
        json.dump({
            "strategy_id": strategy_id,
            "config": str(args.config),
            "summary": summary,
        }, f, indent=2, ensure_ascii=False, default=str)

    # 打印
    logger.info(f"=== Alpha Backtest Result ===")
    logger.info(f"Strategy: {strategy_id}")
    for k, v in summary.items():
        logger.info(f"  {k}: {v}")
    logger.info(f"Saved: {out_path}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
