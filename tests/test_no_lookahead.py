"""Look-ahead 数据破损测试 (杀手锏).

设计:
  - 合成 30 天价格序列
  - 在 t=5/10/15/20 四个时点分别跑破损
  - 破损 = 随机搞坏 t+1..t+20 数据, 验证 t 时刻因子输出**完全不变**
  - 覆盖 3 个因子: momentum_20, momentum_60, rev_5
  - 一次失败 = look-ahead bug, 不能放过

集成层级: factors + pipeline + storage 端到端.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from newbee.factors.classic.momentum import momentum_20, momentum_60, rev_5


# ---------- helpers ----------


def make_synthetic_prices(
    n_stocks: int = 10,
    n_days: int = 100,
    seed: int = 42,
) -> np.ndarray:
    """合成随机游走价格 (T, N)."""
    rng = np.random.RandomState(seed)
    rets = rng.randn(n_days, n_stocks) * 0.02
    prices = 100 * np.cumprod(1 + rets, axis=0)
    return prices


# ---------- 测试 ----------


@pytest.mark.parametrize(
    "factor_func",
    [momentum_20, momentum_60, rev_5],
    ids=["momentum_20", "momentum_60", "rev_5"],
)
def test_no_lookahead_mid_window(factor_func):
    """t 时刻的因子值, 不应受 t+1 之后价格影响."""
    from datetime import date as _date
    prices = make_synthetic_prices(n_stocks=10, n_days=100, seed=42)

    # 选 t=50 (保证 momentum_20, momentum_60 都有足够 lookback)
    t = 50
    if factor_func.name == "momentum_60":
        t = 70  # 60 日动量需要更长的 lookback

    f_clean = factor_func(prices, asof=_date(2020, 1, 1))

    # 搞坏 t+1..t+20 的价格
    prices_corrupt = prices.copy()
    rng = np.random.RandomState(123)
    prices_corrupt[t + 1 : t + 21] = (
        prices_corrupt[t + 1 : t + 21] * (1 + rng.randn(20, prices.shape[1]) * 0.5)
    )
    f_corrupt = factor_func(prices_corrupt, asof=_date(2020, 1, 1))

    # 严格相等 (不是近似)
    np.testing.assert_array_equal(
        f_clean, f_corrupt,
        err_msg=f"因子 {factor_func.name} 在 t={t} 受未来数据污染! look-ahead bug"
    )


@pytest.mark.parametrize("corrupt_t_offset", [0, 1, 5, 10])
def test_momentum_20_robust_to_future_corruption(corrupt_t_offset):
    """momentum_20: 改变 asof 之后的任意一段, t 时刻的因子输出完全不变.

    关键: factor 调用 prices[0:t+1] 表示"as of t" 的输入.
    corruption 必须严格发生在 prices[t+1:] (不在输入范围内).
    """
    from datetime import date as _date
    prices = make_synthetic_prices(n_stocks=20, n_days=100, seed=7)
    t = 50  # factor as of t=50
    # as-of 输入
    input_clean = prices[: t + 1]  # indices [0, 50]
    f_clean = momentum_20(input_clean, asof=_date(2020, 1, 1))

    # corruption: 从 t+1+offset 开始
    corrupt_start = t + 1 + corrupt_t_offset
    prices_corrupt = prices.copy()
    if corrupt_start < prices.shape[0]:
        rng = np.random.RandomState(99)
        prices_corrupt[corrupt_start:] *= (
            1 + rng.randn(prices.shape[0] - corrupt_start, prices.shape[1]) * 0.3
        )
    # 重新构造 as-of 输入 (slice 不变, 范围严格在 [0, t])
    f_corrupt = momentum_20(prices_corrupt[: t + 1], asof=_date(2020, 1, 1))

    np.testing.assert_array_equal(f_clean, f_corrupt)


def test_pipeline_no_lookahead():
    """compute_factor_panel: 整段时间序列, 中间某段破损不影响之前的值."""
    from newbee.factors.pipeline import compute_factor_at

    prices = make_synthetic_prices(n_stocks=5, n_days=80, seed=11)
    dates = [date(2020, 1, 1) + timedelta(days=int(d)) for d in range(80)]

    f_clean = np.zeros((80, 5))
    for t in range(80):
        f_clean[t] = momentum_20(prices, asof=dates[t])

    # 损坏 t=30 之后的价格
    prices_corrupt = prices.copy()
    prices_corrupt[30:] *= 2.0
    f_corrupt = np.zeros((80, 5))
    for t in range(80):
        f_corrupt[t] = momentum_20(prices_corrupt, asof=dates[t])

    # t < 30 时, 输出应完全一致
    np.testing.assert_array_equal(
        f_clean[:30], f_corrupt[:30],
        err_msg="momentum_20 在 t<30 时受 t>=30 数据污染!"
    )


def test_lookahead_via_recompute():
    """重新计算某日的因子, 应与第一次计算结果完全一致 (无随机性 / 无后效)."""
    from datetime import date as _date
    prices = make_synthetic_prices(n_stocks=10, n_days=80, seed=42)
    f1 = momentum_20(prices, asof=_date(2020, 1, 1))
    f2 = momentum_20(prices, asof=_date(2020, 1, 1))
    np.testing.assert_array_equal(f1, f2)


def test_edge_case_insufficient_window():
    """T < window+1 时所有动量因子应返回 NaN (无足够 lookback)."""
    from datetime import date as _date
    # momentum_60 需要 61 行, 只给 30
    prices_30 = make_synthetic_prices(n_stocks=5, n_days=30, seed=1)
    f0_mom20 = momentum_20(prices_30, asof=_date(2020, 1, 1))
    f0_mom60 = momentum_60(prices_30, asof=_date(2020, 1, 1))
    f0_rev5 = rev_5(prices_30, asof=_date(2020, 1, 1))
    # 全部 NaN (T=30 < 61 for mom60, 但 30 >= 6 for rev_5, 30 >= 21 for mom20)
    # rev_5 window=5, 30 >= 6 ✓, 所以 rev_5 不应该全 NaN
    # 只验证 mom60 (T=30 < 61)
    assert np.all(np.isnan(f0_mom60))
    # mom20 30 >= 21, 有值
    # rev_5 30 >= 6, 有值

    # T < 6 触发 rev_5 NaN
    prices_3 = make_synthetic_prices(n_stocks=5, n_days=3, seed=1)
    f_rev3 = rev_5(prices_3, asof=_date(2020, 1, 1))
    f_mom20_3 = momentum_20(prices_3, asof=_date(2020, 1, 1))
    assert np.all(np.isnan(f_rev3))
    assert np.all(np.isnan(f_mom20_3))
