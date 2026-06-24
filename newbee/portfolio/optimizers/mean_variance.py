"""均值方差优化器 (scipy.optimize).

输入:
  - scores: ndarray(N,), alpha score (越大越被高估 → 越想买, 取负号变成"期望超额收益")
  - cov: ndarray(N, N), 协方差矩阵 (年化)
  - current_weights: ndarray(N,), 当前持仓 (调仓约束用)
  - constraints: 约束列表
  - risk_aversion: 风险厌恶系数 λ (越大越保守)

输出:
  - weights: ndarray(N,), 优化后的目标持仓

简化: M1 用 scipyl.optimize.minimize + 边界 + 约束
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
from scipy.optimize import minimize

from newbee.utils import logger


def mean_variance(
    scores: np.ndarray,
    cov: np.ndarray,
    current_weights: np.ndarray | None = None,
    *,
    risk_aversion: float = 1.0,
    long_only: bool = True,
    weight_sum: float = 1.0,
    max_weight: float | None = None,
    max_turnover: float | None = None,
    valid_mask: np.ndarray | None = None,
    maxiter: int = 200,
) -> np.ndarray:
    """均值方差优化 (单期).

    目标: max  scores^T w  -  (λ/2) w^T Σ w
    等价: min  -(scores^T w) + (λ/2) w^T Σ w

    约束:
      - sum(w) = weight_sum
      - 0 <= w_i (long_only)
      - |w_i| <= max_weight (可选)
      - 0.5 * ||w - w_prev||_1 <= max_turnover (可选)

    Args:
        scores: ndarray(N,), 期望超额收益 (alpha score, 已取负后正向往)
        cov: ndarray(N, N), 协方差矩阵
        current_weights: ndarray(N,), 当前持仓 (用于 max_turnover)
        risk_aversion: λ, 越大越保守
        long_only: True 时 w >= 0
        weight_sum: 权重和约束 (默认 1.0, 即满仓)
        max_weight: 单只最大权重
        max_turnover: 最大换手率 (与 current_weights 的 L1/2)
        valid_mask: ndarray(bool, N,), True 的位置参与优化, False 的位置权重强制 0

    Returns:
        weights: ndarray(N,), 优化结果
    """
    n = len(scores)
    if cov.shape != (n, n):
        raise ValueError(f"cov 形状 {cov.shape} != ({n}, {n})")
    if current_weights is not None and len(current_weights) != n:
        raise ValueError(f"current_weights 长度 {len(current_weights)} != {n}")

    # 初始: 等权 (但只在 valid_mask 范围内)
    if valid_mask is None:
        valid_mask = np.ones(n, dtype=bool)
    valid_idx = np.where(valid_mask)[0]

    if len(valid_idx) == 0:
        # 全 invalid, 返回全 0
        return np.zeros(n)

    w0 = np.zeros(n)
    if weight_sum > 0 and len(valid_idx) > 0:
        w0[valid_idx] = weight_sum / len(valid_idx)

    # 目标函数 (在 invalid 位置强制 0, 不参与计算)
    def _objective(w_full: np.ndarray) -> float:
        w = w_full.copy()
        w[~valid_mask] = 0.0
        expected = scores @ w
        risk = 0.5 * risk_aversion * w @ cov @ w
        return -(expected - risk)

    def _grad(w_full: np.ndarray) -> np.ndarray:
        w = w_full.copy()
        w[~valid_mask] = 0.0
        g = -(scores - risk_aversion * (cov @ w))
        g[~valid_mask] = 0.0
        return g

    # 约束
    constraints: list[dict] = []

    # sum(w) = weight_sum
    def _sum_eq(w_full: np.ndarray) -> float:
        w = w_full.copy()
        w[~valid_mask] = 0.0
        return w.sum() - weight_sum

    constraints.append({"type": "eq", "fun": _sum_eq})

    # max_turnover
    if max_turnover is not None and current_weights is not None:
        def _turnover_ub(w_full: np.ndarray) -> float:
            w = w_full.copy()
            w[~valid_mask] = 0.0
            return max_turnover - 0.5 * np.abs(w - current_weights).sum()

        constraints.append(
            {"type": "ineq", "fun": _turnover_ub}
        )

    # 边界
    bounds: list[tuple[float, float]] = []
    for i in range(n):
        if not valid_mask[i]:
            bounds.append((0.0, 0.0))
        else:
            lo = 0.0 if long_only else -1.0
            hi = float(max_weight) if max_weight is not None else 1.0
            bounds.append((lo, hi))

    # 优化
    result = minimize(
        _objective,
        w0,
        jac=_grad,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": int(maxiter), "ftol": 1e-9},
    )

    if not result.success:
        # 优化未收敛: 走 fallback 而不是直接返回可能违反约束的 SLSQP 解
        # 策略:
        #   1. 若有 current_weights 且不全为 0 → 保留当前持仓 (今天不调仓)
        #      避免在 solver 失控时贸然换仓, 对实盘最安全
        #   2. 否则 → 返回 valid mask 内的等权 (即 w0)
        #      等权是无信息先验, 比被 SLSQP 扭曲的 result.x 更稳
        if current_weights is not None and np.any(current_weights != 0):
            logger.warning(
                f"mean_variance 优化未收敛: {result.message}, "
                f"fallback to current_weights (今日不调仓)"
            )
            w_opt = current_weights.astype(float).copy()
            w_opt[~valid_mask] = 0.0
            w_opt = np.where(np.abs(w_opt) < 1e-9, 0.0, w_opt)
            return w_opt

        logger.warning(
            f"mean_variance 优化未收敛: {result.message}, "
            f"fallback to equal_weight (valid mask 内)"
        )
        w_opt = w0.copy()
        w_opt = np.where(np.abs(w_opt) < 1e-9, 0.0, w_opt)
        return w_opt

    w_opt = result.x.copy()
    w_opt[~valid_mask] = 0.0
    # 数值清理
    w_opt = np.where(np.abs(w_opt) < 1e-9, 0.0, w_opt)
    return w_opt


def equal_weight(
    n: int,
    *,
    valid_mask: np.ndarray | None = None,
    weight_sum: float = 1.0,
) -> np.ndarray:
    """等权 (简化版, 用于 baseline / fallback)."""
    w = np.zeros(n)
    if valid_mask is None:
        valid_mask = np.ones(n, dtype=bool)
    valid_idx = np.where(valid_mask)[0]
    if len(valid_idx) == 0:
        return w
    w[valid_idx] = weight_sum / len(valid_idx)
    return w


def inverse_vol(
    cov: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
    weight_sum: float = 1.0,
) -> np.ndarray:
    """Inverse volatility 权重 (对角协方差)."""
    diag = np.diag(cov)
    diag = np.where(diag <= 0, np.nan, diag)
    vol = np.sqrt(diag)
    if valid_mask is None:
        valid_mask = np.ones(len(vol), dtype=bool)
    vol[~valid_mask] = np.nan
    if np.all(np.isnan(vol)):
        return equal_weight(len(vol), valid_mask=valid_mask, weight_sum=weight_sum)
    inv = 1.0 / vol
    inv[~valid_mask] = 0.0
    s = inv.sum()
    if s == 0:
        return equal_weight(len(vol), valid_mask=valid_mask, weight_sum=weight_sum)
    return (inv / s) * weight_sum