"""01_first_factor — 第一个因子探索 (momentum_20).

逻辑:
  1. 加载 CSI 1000 pool
  2. 加载 adj 数据 (5 年)
  3. 算 momentum_20 (T, N)
  4. 算 forward return (T, N)
  5. 跑 alpha 回测
  6. 输出 IC / decile / 画图

运行:
    /opt/anaconda3/envs/py312/bin/python docs/01_first_factor.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 无头 backend
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.data.universe import StockPool  # noqa: E402
from newbee.data.storage import load_bars_from_parquet  # noqa: E402
from newbee.factors.classic import momentum  # noqa: F401, E402  (触发注册)
from newbee.factors import get as get_factor  # noqa: E402
from newbee.alpha_store import AlphaStore  # noqa: E402
from newbee.engines.backtest_alpha import (  # noqa: E402
    forward_returns_from_prices,
    run_alpha_backtest,
)


def main(
    start: date = date(2020, 1, 1),
    end: date = date(2024, 12, 31),
    output_dir: Path = PROJECT_ROOT / "docs" / "output",
    factor_name: str = "momentum_20",
    factor_version: str = "1.0",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pool = StockPool.load()
    stock_ids = pool.export()["stock_id"].tolist()
    print(f"[1] pool.size() = {pool.size()}")

    print(f"[2] 加载 K 线 {start} ~ {end} ...")
    bars = load_bars_from_parquet(
        stock_ids, start=start, end=end, kind="adj", root=PROJECT_ROOT / "data"
    )
    print(f"    bars.T = {bars.T}, bars.N = {bars.N}")
    if bars.T == 0:
        print("⚠️  数据为空 (可能 fetch_data 还没跑), 跳过")
        return

    # 3. 算 momentum_20
    factor = get_factor(factor_name)
    T, N = bars.adj_close.shape
    scores = np.full((T, N), np.nan, dtype=np.float64)
    min_hist = (factor.spec.window or 20) + 1
    for t in range(min_hist - 1, T):
        scores[t] = factor.compute(asof=bars.dates[t], prices=bars.adj_close[: t + 1])
    n_valid = int(np.sum(~np.isnan(scores)))
    print(f"[3] {factor_name} 计算完成: n_valid={n_valid}")

    # 4. forward returns
    fwd_ret = forward_returns_from_prices(bars.adj_close, horizon=1)
    print(f"[4] forward returns: shape={fwd_ret.shape}, 末行 NaN (horizon=1)")

    # 5. alpha 回测
    result = run_alpha_backtest(scores, fwd_ret, bars.dates, n_groups=10, min_valid=30)
    print(f"\n{result.summary()}\n")

    # 6. 画图
    dates_arr = pd.DatetimeIndex(bars.dates)
    fig, axes = plt.subplots(3, 1, figsize=(12, 10))

    # IC 时序
    ax = axes[0]
    ax.plot(dates_arr, result.ic, "b-", alpha=0.7, label="IC")
    ax.axhline(result.ic_mean, color="r", linestyle="--", label=f"mean={result.ic_mean:.4f}")
    ax.axhline(0, color="k", linewidth=0.5)
    ax.set_title(f"IC time series ({factor_name})")
    ax.set_ylabel("IC")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # RankIC 时序
    ax = axes[1]
    ax.plot(dates_arr, result.rank_ic, "g-", alpha=0.7, label="RankIC")
    ax.axhline(result.rank_ic_mean, color="r", linestyle="--", label=f"mean={result.rank_ic_mean:.4f}")
    ax.axhline(0, color="k", linewidth=0.5)
    ax.set_title(f"RankIC time series ({factor_name})")
    ax.set_ylabel("RankIC")
    ax.legend()
    ax.grid(True, alpha=0.3)

    # decile 收益曲线 (cumulative)
    ax = axes[2]
    decile_cum = np.nancumsum(result.decile_returns, axis=0)
    for g in range(result.n_groups):
        ax.plot(dates_arr, decile_cum[:, g], label=f"G{g+1}", linewidth=1.5)
    ax.set_title("Decile cumulative returns (1=lowest score, 10=highest)")
    ax.set_ylabel("Cumulative return")
    ax.set_xlabel("Date")
    ax.legend(loc="upper left", ncol=5, fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = output_dir / f"{factor_name}_ic_decile.png"
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    print(f"[5] 图表已保存: {out_path}")

    # 7. 评价结论
    print("\n=== 评价 ===")
    if abs(result.ic_mean) > 0.02:
        print(f"✓ IC mean = {result.ic_mean:+.4f} > 0.02 (有信号)")
    else:
        print(f"✗ IC mean = {result.ic_mean:+.4f} < 0.02 (信号弱)")
    avg_decile = np.nanmean(result.decile_returns, axis=0)
    monotone = all(avg_decile[i] <= avg_decile[i + 1] for i in range(len(avg_decile) - 1))
    if monotone:
        print(f"✓ decile 单调: g1={avg_decile[0]:.4f} → g10={avg_decile[-1]:.4f}")
    else:
        print(f"△ decile 不严格单调: {np.round(avg_decile, 4)}")

    # 8. 写 alpha_store (供 run_alpha_backtest.py 消费)
    strategy_id = f"{factor_name}_{factor_version}"
    alpha_store = AlphaStore(
        PROJECT_ROOT / "data" / "alpha" / strategy_id, pool,
    )
    n_written = 0
    for t, d in enumerate(bars.dates):
        if not np.isnan(scores[t]).all():
            alpha_store.write(d, scores[t], strategy_id=strategy_id)
            n_written += 1
    print(f"\n[6] alpha_store 写入: {n_written} dates → data/alpha/{strategy_id}/")


if __name__ == "__main__":
    main()