"""组合回测入口: 从策略配置读入, 跑组合回测.

用法:
  python scripts/run_portfolio_backtest.py --config configs/strategies/momentum_baseline.yaml
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
from newbee.engines.backtest_portfolio import run_portfolio_backtest
from newbee.portfolio import CostModel
from newbee.factors.pipeline import compute_factor_panel
from newbee.factors.classic.momentum import momentum_20, momentum_60, rev_5


# 因子注册表 (简化)
FACTOR_REGISTRY = {
    "momentum_20": momentum_20,
    "momentum_60": momentum_60,
    "rev_5": rev_5,
}


def main():
    parser = argparse.ArgumentParser(description="Portfolio backtest")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--universe", type=Path,
        default=PROJECT_ROOT / "data" / "universe" / "pool.parquet")
    parser.add_argument("--data-root", type=Path,
        default=PROJECT_ROOT / "data" / "adj")
    parser.add_argument("--out-dir", type=Path,
        default=PROJECT_ROOT / "data" / "portfolio" / "results")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    logging.info(f"配置: {args.config.name}")

    # 加载数据
    pool = StockPool.load(args.universe)
    start = date.fromisoformat(cfg["data"]["start"])
    end = date.fromisoformat(cfg["data"]["end"])
    bars = load_bars_from_parquet(
        pool=pool, data_root=args.data_root,
        start=start, end=end, field="adj_close",
    )
    print(f"Bars: T={len(bars.dates)}, N={bars.N}")

    # 算 alpha
    factor_name = cfg["factor"]["name"]
    factor_func = FACTOR_REGISTRY[factor_name]
    prices = bars.matrix[:, :, 5]
    print(f"计算因子: {factor_name} ...")
    alpha_panel = compute_factor_panel(
        factor_func, bars.dates, prices, pool,
        start=start, end=end,
    )
    print(f"Alpha shape: {alpha_panel.shape}, non-NaN: {(~np.isnan(alpha_panel)).sum()}")

    # 写 alpha store (cache 接入)
    strategy_id = cfg["factor"]["name"] + "_" + cfg["factor"].get("version", "1.0")
    alpha_store = AlphaStore(PROJECT_ROOT / "data" / "alpha" / strategy_id, pool)
    for t, d in enumerate(bars.dates):
        if not np.isnan(alpha_panel[t]).all():
            alpha_store.write(d, alpha_panel[t], strategy_id=strategy_id)
    print(f"Alpha store: {len(alpha_store.list_dates())} dates")

    # 组合回测
    cost_cfg = cfg.get("cost", {})
    cost_model = CostModel(
        commission_rate=cost_cfg.get("commission_rate", 0.0005),
        slippage_rate=cost_cfg.get("slippage_rate", 0.001),
    )

    portfolio_cfg = cfg.get("portfolio", {})
    result = run_portfolio_backtest(
        prices=prices, dates=bars.dates, pool=pool,
        alpha_scores=alpha_panel,
        rebalance_freq=portfolio_cfg.get("rebalance_freq", 20),
        lookback_cov=portfolio_cfg.get("lookback_cov", 60),
        risk_aversion=portfolio_cfg.get("risk_aversion", 1.0),
        max_turnover=portfolio_cfg.get("max_turnover", 0.3),
        max_weight=portfolio_cfg.get("max_weight", 0.05),
        cost_model=cost_model,
        optimizer=portfolio_cfg.get("optimizer", "mean_variance"),
    )

    # 输出
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{strategy_id}_nav.parquet"
    result.nav.to_frame("nav").to_parquet(out_path)

    # summary
    summary = result.summary()
    summary_path = args.out_dir / f"{strategy_id}_summary.json"
    import json
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n=== Portfolio Backtest Result ===")
    print(f"Strategy: {strategy_id}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
