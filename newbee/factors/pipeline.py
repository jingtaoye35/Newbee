"""因子计算 pipeline.

提供:
  - compute_factor_over_range(factor, prices, dates) -> ndarray(T, N)
  - compute_factor_at(factor, prices, asof) -> ndarray(N,)
  - batch_compute(factor, prices_dict) -> dict[date -> ndarray(N,)]

约定:
  - prices: ndarray(T, N) 整个时间窗口的矩阵
  - dates: list[date] 长度 T
  - 输出: ndarray(T, N), 每天的因子横截面 (前 window 天可能是 NaN, 表示数据不足)

注意: pipeline 只负责"批量调因子", 不负责数据加载 (那是 data.storage 的事).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from newbee.factors.base import FactorSpec, SimpleFactor, n_nonan
from newbee.factors.registry import get as get_factor

logger = logging.getLogger(__name__)


def compute_factor_at(
    factor: SimpleFactor,
    prices: np.ndarray,
    asof: date,
) -> np.ndarray:
    """计算单个 asof 的因子值.

    Args:
        factor: 已注册的 SimpleFactor
        prices: ndarray(T, N), T >= factor.spec.window + 1
        asof: 计算时点

    Returns:
        ndarray(N,), 不够窗口的位置 NaN
    """
    return factor.compute(asof=asof, prices=prices)


def compute_factor_over_range(
    factor: SimpleFactor,
    prices: np.ndarray,
    dates: list[date],
    *,
    min_history: int | None = None,
) -> tuple[np.ndarray, list[date]]:
    """计算整个时间窗口的因子横截面.

    Args:
        factor: 已注册的 SimpleFactor
        prices: ndarray(T, N), 与 dates 一一对应
        dates: list[date] 长度 T, 与 prices 行对齐
        min_history: 最少历史天数 (默认 factor.spec.window + 1)

    Returns:
        (ndarray(T_out, N), list[date_out])
        其中 T_out 是 dates 中满足 min_history 条件的子集
    """
    if prices.shape[0] != len(dates):
        raise ValueError(
            f"prices 长度 {prices.shape[0]} != dates 长度 {len(dates)}"
        )
    if min_history is None:
        min_history = (factor.spec.window or 20) + 1

    out_arrays: list[np.ndarray] = []
    out_dates: list[date] = []
    for i, d in enumerate(dates):
        if i + 1 < min_history:
            continue
        # 取截至 asof 的历史 (含 asof 当天)
        hist = prices[: i + 1]
        v = factor.compute(asof=d, prices=hist)
        out_arrays.append(v)
        out_dates.append(d)

    if not out_arrays:
        return np.empty((0, prices.shape[1])), []
    return np.stack(out_arrays, axis=0), out_dates


def compute_factor_panel(
    factor: SimpleFactor,
    prices: np.ndarray,
    dates: list[date],
    *,
    min_history: int | None = None,
) -> pd.DataFrame:
    """包装 compute_factor_over_range, 返回 DataFrame (index=date, columns=stock_ids).

    适合做时间序列分析 (IC, decile, etc.)
    """
    arr, ds = compute_factor_over_range(factor, prices, dates, min_history=min_history)
    if arr.size == 0:
        return pd.DataFrame()
    return pd.DataFrame(arr, index=pd.DatetimeIndex(ds))


def batch_compute(
    factor: SimpleFactor,
    prices_by_asof: dict[date, np.ndarray],
) -> dict[date, np.ndarray]:
    """批量算多个 asof 的因子值, prices_by_asof 由调用方提供 (避免反复切片)."""
    return {d: factor.compute(asof=d, prices=p) for d, p in prices_by_asof.items()}


# ---------- IC / RankIC / decile 计算 (回测用, 在 pipeline 工具) ----------


def information_coefficient(
    scores: np.ndarray,
    forward_returns: np.ndarray,
) -> float:
    """横截面 Pearson IC.

    Args:
        scores: ndarray(N,), 因子值 (NaN 会被 mask 掉)
        forward_returns: ndarray(N,), 同期 forward return (NaN 会被 mask 掉)

    Returns:
        IC 值, 范围 [-1, 1]. 如果有效样本 < 2, 返回 NaN.
    """
    valid = ~(np.isnan(scores) | np.isnan(forward_returns))
    if valid.sum() < 2:
        return np.nan
    s = scores[valid]
    r = forward_returns[valid]
    if np.std(s) == 0 or np.std(r) == 0:
        return np.nan
    return float(np.corrcoef(s, r)[0, 1])


def rank_ic(
    scores: np.ndarray,
    forward_returns: np.ndarray,
) -> float:
    """横截面 Spearman RankIC."""
    from scipy.stats import spearmanr

    valid = ~(np.isnan(scores) | np.isnan(forward_returns))
    if valid.sum() < 2:
        return np.nan
    rho, _ = spearmanr(scores[valid], forward_returns[valid])
    return float(rho) if not np.isnan(rho) else np.nan


def decile_returns(
    scores: np.ndarray,
    forward_returns: np.ndarray,
    n_groups: int = 10,
) -> tuple[np.ndarray, np.ndarray]:
    """十分组收益.

    Args:
        scores: ndarray(N,), 因子值
        forward_returns: ndarray(N,), 同期 forward return
        n_groups: 分组数, 默认 10

    Returns:
        (group_returns, group_sizes)
        - group_returns: ndarray(n_groups,), 每组平均 forward return
        - group_sizes: ndarray(n_groups,), 每组样本数
    """
    valid = ~(np.isnan(scores) | np.isnan(forward_returns))
    s = scores[valid]
    r = forward_returns[valid]
    if len(s) == 0:
        return np.full(n_groups, np.nan), np.zeros(n_groups, dtype=int)

    # 用 qcut 分组, ties 失败时回退到 rank 离散化
    try:
        groups = pd.qcut(s, q=n_groups, labels=False, duplicates="drop")
    except ValueError:
        # ties 太多, 用 rank 分组
        from newbee.factors.base import rank_

        ranks = rank_(s)
        groups = np.minimum((ranks / max(len(ranks), 1) * n_groups).astype(int), n_groups - 1)
    groups = np.asarray(groups, dtype=int)
    unique_groups = np.unique(groups)
    group_returns = np.full(n_groups, np.nan)
    group_sizes = np.zeros(n_groups, dtype=int)
    for g in unique_groups:
        mask_g = groups == g
        group_returns[g] = r[mask_g].mean()
        group_sizes[g] = int(mask_g.sum())
    return group_returns, group_sizes