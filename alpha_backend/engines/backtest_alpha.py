"""Alpha 回测引擎 (Phase A: 向量化速度优先).

职责:
  - 输入 alpha 矩阵 (T, N) + 收益矩阵 (T, N)
  - 计算 IC 时序、RankIC 时序、十分组 (decile) 收益曲线
  - 汇总: IC mean / std / ICIR / decile 收益表

不模拟: 调仓日、持仓期、现金管理、交易成本、涨跌停停牌 (这些是 Phase B 的事).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from alpha_backend.factors.pipeline import (
    decile_returns,
    information_coefficient,
    rank_ic,
)
from alpha_backend.utils import logger


# ---------- 结果容器 ----------


@dataclass
class AlphaBacktestResult:
    """Alpha 回测结果汇总."""

    dates: list[date]
    ic: np.ndarray  # (T,)
    rank_ic: np.ndarray  # (T,)
    decile_returns: np.ndarray  # (T, n_groups) 每天的十分组平均 forward return
    decile_sizes: np.ndarray  # (n_groups,) 每组样本数
    ic_mean: float
    ic_std: float
    icir: float
    rank_ic_mean: float
    rank_ic_std: float
    n_groups: int
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        # 用 nanmean 避免末尾 NaN 污染
        decile_avg = np.nanmean(self.decile_returns, axis=0)
        return (
            f"AlphaBacktestResult(\n"
            f"  n_periods={len(self.dates)},\n"
            f"  IC:     mean={self.ic_mean:+.4f}  std={self.ic_std:.4f}  ICIR={self.icir:+.3f}\n"
            f"  RankIC: mean={self.rank_ic_mean:+.4f}  std={self.rank_ic_std:.4f}\n"
            f"  decile_returns[avg over time, nanmean] = {np.round(decile_avg, 4)}\n"
            f"  n_groups={self.n_groups}\n"
            f")"
        )

    def summary_dict(self) -> dict[str, Any]:
        """结构化汇总 (供 JSON 序列化 / 字段访问)."""
        decile_avg = np.nanmean(self.decile_returns, axis=0)
        return {
            "n_periods": len(self.dates),
            "ic_mean": float(self.ic_mean),
            "ic_std": float(self.ic_std),
            "icir": float(self.icir) if np.isfinite(self.icir) else None,
            "rank_ic_mean": float(self.rank_ic_mean),
            "rank_ic_std": float(self.rank_ic_std),
            "decile_returns_avg": [float(x) for x in decile_avg],
            "decile_sizes": [int(x) for x in self.decile_sizes],
            "n_groups": int(self.n_groups),
        }


# ---------- 主函数 ----------


def run_alpha_backtest(
    scores: np.ndarray,
    forward_returns: np.ndarray,
    dates: Sequence[date],
    *,
    n_groups: int = 10,
    min_valid: int = 30,
) -> AlphaBacktestResult:
    """跑 alpha 回测 (向量化).

    Args:
        scores: ndarray(T, N), 因子 / alpha 横截面, NaN 表示无效
        forward_returns: ndarray(T, N), 同期 (或下期) 收益, NaN 表示无效
        dates: list[date] 长度 T
        n_groups: 十分组, 默认 10
        min_valid: 最少有效样本数, 少于则该期 IC 记为 NaN

    Returns:
        AlphaBacktestResult
    """
    if scores.shape != forward_returns.shape:
        raise ValueError(
            f"scores {scores.shape} != forward_returns {forward_returns.shape}"
        )
    if scores.shape[0] != len(dates):
        raise ValueError(
            f"scores 长度 {scores.shape[0]} != dates 长度 {len(dates)}"
        )
    T, N = scores.shape

    # 1. 算每天的 IC / RankIC
    ic_arr = np.full(T, np.nan)
    ric_arr = np.full(T, np.nan)
    decile_mat = np.full((T, n_groups), np.nan)

    for t in range(T):
        s = scores[t]
        r = forward_returns[t]
        valid = ~(np.isnan(s) | np.isnan(r))
        if valid.sum() < min_valid:
            continue
        ic_arr[t] = information_coefficient(s, r)
        ric_arr[t] = rank_ic(s, r)
        gr, _ = decile_returns(s, r, n_groups=n_groups)
        decile_mat[t] = gr

    # 2. 汇总
    ic_valid = ic_arr[~np.isnan(ic_arr)]
    ric_valid = ric_arr[~np.isnan(ric_arr)]

    if len(ic_valid) > 0:
        ic_mean = float(ic_valid.mean())
        ic_std = float(ic_valid.std(ddof=1)) if len(ic_valid) > 1 else 0.0
        icir = ic_mean / ic_std if ic_std > 0 else np.nan
    else:
        ic_mean = ic_std = icir = np.nan

    if len(ric_valid) > 0:
        ric_mean = float(ric_valid.mean())
        ric_std = float(ric_valid.std(ddof=1)) if len(ric_valid) > 1 else 0.0
    else:
        ric_mean = ric_std = np.nan

    # 3. decile sizes (用最近一期有效值)
    last_valid = -1
    for t in range(T - 1, -1, -1):
        if not np.isnan(ic_arr[t]):
            last_valid = t
            break
    if last_valid >= 0:
        _, sizes = decile_returns(
            scores[last_valid], forward_returns[last_valid], n_groups=n_groups
        )
    else:
        sizes = np.zeros(n_groups, dtype=int)

    return AlphaBacktestResult(
        dates=list(dates),
        ic=ic_arr,
        rank_ic=ric_arr,
        decile_returns=decile_mat,
        decile_sizes=sizes,
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=icir,
        rank_ic_mean=ric_mean,
        rank_ic_std=ric_std,
        n_groups=n_groups,
    )


# ---------- 便捷: 从 alpha_store + 数据直接跑 ----------


def run_alpha_backtest_from_store(
    alpha_store: "AlphaStore",
    forward_returns: np.ndarray,
    dates: list[date],
    *,
    n_groups: int = 10,
    min_valid: int = 30,
) -> AlphaBacktestResult:
    """从 alpha_store 读 alpha 矩阵 + 给定 forward_returns, 跑回测.

    Args:
        alpha_store: 绑定好的 AlphaStore 实例 (含 strategy_id / root)
        forward_returns: ndarray(T, N), 与 alpha 矩阵同日序
        dates: list[date] 长度 T

    Note:
        store 与 dates 不要求严格对齐: 缺日期的位置 scores 行记 NaN,
        由 `run_alpha_backtest` 内部按 min_valid 跳过, 不算错.
    """
    if not dates:
        raise ValueError("dates 不能为空")
    if forward_returns.shape[0] != len(dates):
        raise ValueError(
            f"forward_returns 长度 {forward_returns.shape[0]} != dates 长度 {len(dates)}"
        )
    store_mat, store_dates = alpha_store.read_range(dates[0], dates[-1])
    N = forward_returns.shape[1]
    if store_mat.size == 0:
        scores_mat = np.full((len(dates), N), np.nan)
    else:
        date_to_row = {d: i for i, d in enumerate(store_dates)}
        scores_mat = np.full((len(dates), N), np.nan)
        for i, d in enumerate(dates):
            if d in date_to_row:
                scores_mat[i] = store_mat[date_to_row[d]]
    n_missing = sum(
        1 for i, d in enumerate(dates)
        if d not in set(store_dates) if store_dates
    ) if store_dates else len(dates)
    if n_missing:
        logger.info(
            "alpha_store 缺 %d / %d 天 (视为 NaN, 由 min_valid 跳过)",
            n_missing, len(dates),
        )
    return run_alpha_backtest(
        scores_mat, forward_returns, dates, n_groups=n_groups, min_valid=min_valid
    )


# ---------- 工具: forward_return 计算 ----------


def forward_returns_from_prices(
    prices: np.ndarray, *, horizon: int = 1, log: bool = False
) -> np.ndarray:
    """算 horizon 步 forward return.

    Args:
        prices: ndarray(T, N)
        horizon: 前看步数 (1 = 次日收益)
        log: True 用 log return

    Returns:
        ndarray(T, N), 最后 horizon 行是 NaN (没有未来)
    """
    T, N = prices.shape
    out = np.full_like(prices, np.nan)
    if T <= horizon:
        return out
    p_now = prices[:-horizon]
    p_fwd = prices[horizon:]
    if log:
        out[:T - horizon] = np.log(p_fwd / p_now)
    else:
        out[:T - horizon] = p_fwd / p_now - 1.0
    return out