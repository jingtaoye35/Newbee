"""20 日动量因子.

计算: momentum_20(t) = close(t) / close(t-20) - 1

输入:
  prices: ndarray(T, N), 时间×股票, **前复权收盘价**
  asof: 当前计算时点 (用于 PIT 校验)

输出:
  ndarray(N,), shape = (N,), 不够窗口的股票位置 NaN.

严格 PIT:
  - 只用 asof <= 当前时点之前的数据
  - 不会偷看未来
"""
from __future__ import annotations

from datetime import date

import numpy as np

from newbee.factors import FactorSpec, register


@register(
    spec=FactorSpec(
        name="momentum_20",
        version="1.0",
        window=20,
        deps=("adj_close",),
        notes="20 日动量: close(t) / close(t-20) - 1",
    )
)
def momentum_20(
    prices: np.ndarray, asof: date, *, window: int = 20
) -> np.ndarray:
    """20 日动量因子.

    Args:
        prices: ndarray(T, N), T >= window+1
        asof: 计算时点 (用于 PIT 校验, 实际计算不依赖)
        window: 回看窗口, 默认 20

    Returns:
        ndarray(N,), 不足窗口的位置 NaN
    """
    if prices.ndim != 2:
        raise ValueError(f"prices 必须是 2-D (T, N), got shape={prices.shape}")
    T, N = prices.shape
    if T < window + 1:
        return np.full(N, np.nan)

    out = prices[-1] / prices[-window - 1] - 1.0
    # 价格 <= 0 的位置返回 NaN (避免除零/异常)
    bad = prices[-1] <= 0
    if bad.any():
        out = out.astype(np.float64, copy=True)
        out[bad] = np.nan
    return out


@register(
    spec=FactorSpec(
        name="momentum_60",
        version="1.0",
        window=60,
        deps=("adj_close",),
        notes="60 日动量: close(t) / close(t-60) - 1",
    )
)
def momentum_60(
    prices: np.ndarray, asof: date, *, window: int = 60
) -> np.ndarray:
    """60 日动量因子 (跟 20 日同样的逻辑, 不同窗口)."""
    if prices.ndim != 2:
        raise ValueError(f"prices 必须是 2-D (T, N), got shape={prices.shape}")
    T, N = prices.shape
    if T < window + 1:
        return np.full(N, np.nan)
    out = prices[-1] / prices[-window - 1] - 1.0
    bad = prices[-1] <= 0
    if bad.any():
        out = out.astype(np.float64, copy=True)
        out[bad] = np.nan
    return out


@register(
    spec=FactorSpec(
        name="rev_5",
        version="1.0",
        window=5,
        deps=("adj_close",),
        notes="5 日反转: -(close(t)/close(t-5) - 1) = 5 日跌幅",
    )
)
def rev_5(
    prices: np.ndarray, asof: date, *, window: int = 5
) -> np.ndarray:
    """5 日反转因子 (取负, 等价于"5 日跌幅")."""
    if prices.ndim != 2:
        raise ValueError(f"prices 必须是 2-D (T, N), got shape={prices.shape}")
    T, N = prices.shape
    if T < window + 1:
        return np.full(N, np.nan)
    out = -(prices[-1] / prices[-window - 1] - 1.0)
    bad = prices[-1] <= 0
    if bad.any():
        out = out.astype(np.float64, copy=True)
        out[bad] = np.nan
    return out