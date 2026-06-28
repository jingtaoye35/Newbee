"""组合状态 (持仓 + 现金 + 调仓历史).

设计:
  - positions: ndarray(N,), 持仓权重 (按 pool idx 对齐)
  - cash: float, 现金 (NAV = sum(positions) + cash, 通常 sum(positions) + cash = 1.0)
  - history: 调仓历史 (Trade 列表)
  - target_weights 持久化 (实盘恢复用)

注意:
  - 持仓用 weight (0-1) 表示, 不是股数 (M1 阶段不建模股数, M2+ 可扩展)
  - NaN 位置 = 该股票当前不持仓 (也可能是 universe 中已退市)
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Trade:
    """一次调仓记录."""

    asof: date
    target_weights: np.ndarray  # ndarray(N,)
    prev_weights: np.ndarray  # ndarray(N,)
    turnover: float  # 实际换手率
    cost: float  # 交易成本 (NAV 比例)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asof": self.asof.isoformat(),
            "target_weights": self.target_weights.tolist(),
            "prev_weights": self.prev_weights.tolist(),
            "turnover": float(self.turnover),
            "cost": float(self.cost),
            "notes": self.notes,
        }


@dataclass
class PortfolioState:
    """组合状态机 (按日/调仓日更新).

    Attributes:
        positions: ndarray(N,), 当前持仓权重, 默认全 0
        cash: float, 现金比例, 默认 1.0
        history: list[Trade], 调仓历史
        asof: date, 当前状态对应的日期
        extra: 其它元数据
    """

    positions: np.ndarray = field(default_factory=lambda: np.zeros(0))
    cash: float = 1.0
    history: list[Trade] = field(default_factory=list)
    asof: date | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # positions 必须 1-D
        if self.positions.ndim != 1:
            raise ValueError(f"positions 必须是 1-D, got shape={self.positions.shape}")

    @property
    def N(self) -> int:
        return len(self.positions)

    @property
    def nav(self) -> float:
        """NAV (恒为 1.0, 因为 weights + cash = 1.0)."""
        return 1.0

    @property
    def total_position(self) -> float:
        """总持仓比例 (1 - cash)."""
        return 1.0 - self.cash

    @property
    def invested_mask(self) -> np.ndarray:
        """有持仓的 mask (weight > 0)."""
        return self.positions > 1e-9

    def long_only_check(self) -> bool:
        """检查是否满足 LongOnly (weight >= 0)."""
        return bool(np.all(self.positions >= -1e-9))

    def weight_sum_check(self, target: float = 1.0, tol: float = 1e-6) -> bool:
        """检查 weight + cash = target."""
        return abs(self.positions.sum() + self.cash - target) < tol

    def turnover_to(self, new_weights: np.ndarray) -> float:
        """算到 new_weights 的换手率 (按 weight 之差的一半, L1 norm)."""
        if new_weights.shape != self.positions.shape:
            raise ValueError(
                f"new_weights {new_weights.shape} != positions {self.positions.shape}"
            )
        return float(0.5 * np.abs(new_weights - self.positions).sum())

    def rebalance(
        self,
        target_weights: np.ndarray,
        asof: date,
        *,
        turnover: float | None = None,
        cost: float = 0.0,
        notes: str = "",
    ) -> None:
        """执行调仓.

        Args:
            target_weights: 目标 weight ndarray(N,)
            asof: 调仓日期
            turnover: 实际换手率 (None 自动算)
            cost: 本次交易成本 (NAV 比例)
            notes: 备注
        """
        if target_weights.shape != self.positions.shape:
            raise ValueError(
                f"target_weights {target_weights.shape} != positions {self.positions.shape}"
            )
        if turnover is None:
            turnover = self.turnover_to(target_weights)

        trade = Trade(
            asof=asof,
            target_weights=target_weights.copy(),
            prev_weights=self.positions.copy(),
            turnover=float(turnover),
            cost=float(cost),
            notes=notes,
        )
        self.history.append(trade)
        self.positions = target_weights.astype(np.float64, copy=True)
        self.cash = 1.0 - float(self.positions.sum())
        self.asof = asof

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions": self.positions.tolist(),
            "cash": float(self.cash),
            "history": [t.to_dict() for t in self.history],
            "asof": self.asof.isoformat() if self.asof else None,
            "extra": self.extra,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path) -> "PortfolioState":
        with open(path) as f:
            d = json.load(f)
        history = []
        for t in d.get("history", []):
            history.append(
                Trade(
                    asof=date.fromisoformat(t["asof"]),
                    target_weights=np.array(t["target_weights"], dtype=np.float64),
                    prev_weights=np.array(t["prev_weights"], dtype=np.float64),
                    turnover=t["turnover"],
                    cost=t["cost"],
                    notes=t.get("notes", ""),
                )
            )
        return cls(
            positions=np.array(d["positions"], dtype=np.float64),
            cash=d["cash"],
            history=history,
            asof=date.fromisoformat(d["asof"]) if d.get("asof") else None,
            extra=d.get("extra", {}),
        )

    def copy(self) -> "PortfolioState":
        return PortfolioState(
            positions=self.positions.copy(),
            cash=self.cash,
            history=[copy.deepcopy(t) for t in self.history],
            asof=self.asof,
            extra=dict(self.extra),
        )