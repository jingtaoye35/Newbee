"""Notebook 02: alpha → portfolio (组合回测)

流程:
  1. 加载股票池 + 行情
  2. 计算 alpha (momentum_20)
  3. 算 forward returns
  4. 写 alpha_store
  5. 调 mean_variance 优化器 (含 max_turnover)
  6. 累计 NAV, 调仓, 扣减成本
  7. 对比 baseline (equal-weight, inverse-vol)
  8. 画图: NAV, 持仓变化, 换手率

输出:
  - datas/alpha/{strategy_id}/{date}.npy
  - datas/portfolio/{strategy_id}/nav.parquet
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.datas.universe import StockPool
from alpha_backend.datas.storage import load_bars_from_parquet
from alpha_backend.datas.calendar import sessions_between
from alpha_backend.factors.classic.momentum import momentum_20
from alpha_backend.factors.pipeline import compute_factor_panel
from alpha_backend.alpha_store import AlphaStore
from alpha_backend.engines.backtest_alpha import forward_returns_from_prices
from alpha_backend.engines.backtest_portfolio import run_portfolio_backtest
from alpha_backend.portfolio import CostModel


# ---------- 1. 加载数据 ----------
print("=" * 60)
print("[1] 加载股票池 + 行情")
print("=" * 60)

pool = StockPool.load(PROJECT_ROOT / "datas" / "universe" / "pool.parquet")
print(f"Pool size: {pool.size}, active: {pool.active_count}")

# 用最近 2 年 (避免冷启动)
end = date(2024, 12, 31)
start = date(2023, 1, 1)
bars = load_bars_from_parquet(
    pool=pool,
    data_root=PROJECT_ROOT / "datas" / "adj",
    start=start,
    end=end,
    field="adj_close",  # 复权价
)
print(f"Bars: T={len(bars.dates)}, N={bars.N}, range={bars.dates[0]} ~ {bars.dates[-1]}")

# 校验
assert bars.matrix.shape == (len(bars.dates), pool.size, 6)
print(f"Matrix shape: {bars.matrix.shape} (T, N, F=6)")


# ---------- 2. 算 alpha ----------
print("\n" + "=" * 60)
print("[2] 算 alpha (momentum_20)")
print("=" * 60)

prices = bars.matrix[:, :, 5]  # adj_close
alpha_panel = compute_factor_panel(
    momentum_20, bars.dates, prices, pool,
    start=start, end=end,
)
print(f"Alpha panel shape: {alpha_panel.shape}")
print(f"Non-NaN ratio: {(~np.isnan(alpha_panel)).sum() / alpha_panel.size:.2%}")


# ---------- 3. Forward returns ----------
print("\n" + "=" * 60)
print("[3] 算 forward returns (20-day)")
print("=" * 60)

fwd_returns = forward_returns_from_prices(prices, horizon=20)
print(f"Forward returns shape: {fwd_returns.shape}")
print(f"Mean fwd return: {np.nanmean(fwd_returns):.4%}")


# ---------- 4. 写 alpha store ----------
print("\n" + "=" * 60)
print("[4] 写 alpha store")
print("=" * 60)

strategy_id = "momentum_20_v1"
alpha_store = AlphaStore(PROJECT_ROOT / "datas" / "alpha" / strategy_id, pool)

# 只写非全 NaN 的日期
for t, d in enumerate(bars.dates):
    if not np.isnan(alpha_panel[t]).all():
        alpha_store.write(d, alpha_panel[t], strategy_id=strategy_id)

print(f"Wrote {len(alpha_store.list_dates())} dates to alpha_store")


# ---------- 5. 组合回测 ----------
print("\n" + "=" * 60)
print("[5] 组合回测 (mean_variance vs equal_weight vs inverse_vol)")
print("=" * 60)

cost_model = CostModel(commission_rate=0.0005, slippage_rate=0.001)

results = {}
for opt in ["mean_variance", "equal_weight", "inverse_vol"]:
    print(f"  跑 {opt}...")
    results[opt] = run_portfolio_backtest(
        prices=prices, dates=bars.dates, pool=pool,
        alpha_scores=alpha_panel,
        rebalance_freq=20,  # 月频
        lookback_cov=60,
        risk_aversion=1.0,
        max_turnover=0.3,
        max_weight=0.05,
        cost_model=cost_model,
        optimizer=opt,
    )


# ---------- 6. 对比 ----------
print("\n" + "=" * 60)
print("[6] 关键指标对比")
print("=" * 60)

summary_df = pd.DataFrame({
    name: r.summary() for name, r in results.items()
}).T
print(summary_df.to_string())


# ---------- 7. 画图 ----------
print("\n" + "=" * 60)
print("[7] 画图")
print("=" * 60)

fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

# 7.1 NAV 曲线
ax = axes[0]
for name, r in results.items():
    ax.plot(r.nav.index, r.nav.values, label=name, linewidth=1.5)
ax.set_title("NAV curves (with cost)", fontsize=12)
ax.set_ylabel("NAV")
ax.legend(loc="best")
ax.grid(True, alpha=0.3)

# 7.2 换手率
ax = axes[1]
for name, r in results.items():
    to = r.turnover[r.turnover > 0]
    ax.plot(to.index, to.values, label=name, marker='o', markersize=3, linewidth=0)
ax.set_title("Turnover at rebalance dates", fontsize=12)
ax.set_ylabel("Turnover")
ax.legend(loc="best")
ax.grid(True, alpha=0.3)

# 7.3 累计成本
ax = axes[2]
cum_cost = pd.DataFrame({
    name: r.cost_paid.cumsum() for name, r in results.items()
})
for name in cum_cost.columns:
    ax.plot(cum_cost.index, cum_cost[name].values, label=name)
ax.set_title("Cumulative trading cost", fontsize=12)
ax.set_ylabel("Cumulative cost")
ax.set_xlabel("Date")
ax.legend(loc="best")
ax.grid(True, alpha=0.3)

plt.tight_layout()
plot_path = PROJECT_ROOT / "docs" / "02_alpha_to_portfolio.png"
plt.savefig(plot_path, dpi=100)
print(f"图已保存: {plot_path}")

# 7.4 持仓分布 (最后一次调仓的 top-10)
last_rebal_idx = np.where(results["mean_variance"].turnover > 0)[0][-1]
last_pos = results["mean_variance"].positions.iloc[last_rebal_idx]
top10 = last_pos.sort_values(ascending=False).head(10)
print(f"\n最后一次调仓 ({bars.dates[last_rebal_idx]}) 的 top-10 持仓:")
for sid, w in top10.items():
    if w > 1e-6:
        print(f"  {sid}: {w:.2%}")

# 7.5 持仓总数随时间变化
fig2, ax2 = plt.subplots(figsize=(14, 4))
n_holdings = (results["mean_variance"].positions > 1e-6).sum(axis=1)
ax2.plot(n_holdings.index, n_holdings.values, linewidth=1.0)
ax2.set_title("Number of holdings over time (mean_variance)")
ax2.set_ylabel("N holdings")
ax2.set_xlabel("Date")
ax2.grid(True, alpha=0.3)
plt.tight_layout()
plot_path2 = PROJECT_ROOT / "docs" / "02_n_holdings.png"
plt.savefig(plot_path2, dpi=100)
print(f"持仓数图: {plot_path2}")


# ---------- 8. 保存结果 ----------
print("\n" + "=" * 60)
print("[8] 保存结果")
print("=" * 60)

result_dir = PROJECT_ROOT / "datas" / "portfolio" / strategy_id
result_dir.mkdir(parents=True, exist_ok=True)
for name, r in results.items():
    r.nav.to_frame(name="nav").to_parquet(result_dir / f"nav_{name}.parquet")
print(f"Saved: {result_dir}")

print("\n--- notebook 02 完成 ---")
