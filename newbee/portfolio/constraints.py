"""组合约束: LongOnly / WeightSum / MaxTurnover.

API:
  Constraint (基类)
  - apply(weights, prev_weights=None) -> ndarray
  - project(weights, prev_weights=None) -> ndarray
  - is_satisfied(weights, prev_weights=None) -> bool

约束都是"软约束" + projection: 不满足时通过投影满足, 而不是在优化器中加硬约束.
但 mean_variance 优化器本身也支持硬约束 (max_turnover, long_only, weight_sum).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from newbee.utils import logger


@runtime_checkable
class Constraint(Protocol):
    """约束协议."""

    def project(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> np.ndarray: ...


# ---------- 实现 ----------


@dataclass(frozen=True)
class LongOnly:
    """权重非负 (不允许卖空)."""

    eps: float = 1e-9

    def project(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> np.ndarray:
        return np.maximum(weights, 0.0)

    def is_satisfied(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> bool:
        return bool(np.all(weights >= -self.eps))


@dataclass(frozen=True)
class WeightSum:
    """权重和约束 sum(w) = target."""

    target: float = 1.0
    eps: float = 1e-6

    def project(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> np.ndarray:
        s = weights.sum()
        if abs(s) < self.eps:
            return weights
        # 等比缩放
        return weights * (self.target / s)

    def is_satisfied(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> bool:
        return abs(weights.sum() - self.target) < self.eps


@dataclass(frozen=True)
class MaxTurnover:
    """换手率约束: 0.5 * ||w - w_prev||_1 <= max_turnover."""

    max_turnover: float
    eps: float = 1e-9

    def project(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> np.ndarray:
        if prev_weights is None:
            return weights
        delta = weights - prev_weights
        l1 = 0.5 * np.abs(delta).sum()
        if l1 <= self.max_turnover + self.eps:
            return weights
        # 缩放 delta
        scale = self.max_turnover / l1
        return prev_weights + delta * scale

    def is_satisfied(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> bool:
        if prev_weights is None:
            return True
        l1 = 0.5 * np.abs(weights - prev_weights).sum()
        return l1 <= self.max_turnover + self.eps


@dataclass(frozen=True)
class MaxWeight:
    """单只最大权重: |w_i| <= cap."""

    cap: float
    eps: float = 1e-9

    def project(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> np.ndarray:
        return np.clip(weights, 0.0 if True else -self.cap, self.cap)
        # 注: 假设与 LongOnly 组合使用, 这里不处理负数 (卖空由 LongOnly 限制)

    def is_satisfied(
        self, weights: np.ndarray, prev_weights: np.ndarray | None = None
    ) -> bool:
        return bool(np.all(weights <= self.cap + self.eps))


def project_all(
    weights: np.ndarray,
    constraints: list[Constraint],
    prev_weights: np.ndarray | None = None,
) -> np.ndarray:
    """依次施加所有约束 (顺序可能影响结果, 通常 LongOnly → WeightSum → MaxTurnover → MaxWeight)."""
    w = weights.copy()
    for c in constraints:
        w = c.project(w, prev_weights)
    return w


def check_all(
    weights: np.ndarray,
    constraints: list[Constraint],
    prev_weights: np.ndarray | None = None,
) -> tuple[bool, list[str]]:
    """检查所有约束是否满足. Returns (all_ok, violated_names)."""
    violated = []
    for c in constraints:
        name = type(c).__name__
        if not c.is_satisfied(weights, prev_weights):
            violated.append(name)
    return len(violated) == 0, violated