"""交易成本模型 (双参数化: commission + slippage).

API:
  CostModel(commission_rate, slippage_rate, stamp_tax_rate=0.0)
    - compute(turnover) -> cost (NAV 比例)
    - compute_per_trade(weights_diff) -> ndarray 每只股票的成本

简化: M1 不区分买卖方向, 不区分冲击大小.
所有成本折成"换手率 * (commission + slippage)".
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class CostModel:
    """交易成本模型.

    Attributes:
        commission_rate: 佣金率 (默认 0.0005, 即万 5, 单边)
        slippage_rate: 滑点率 (默认 0.001, 即万 10, 单边)
        stamp_tax_rate: 印花税率 (默认 0, 预留字段, M2+ 接)
        min_commission: 最低佣金 (默认 5 元, 不区分币种)
    """

    commission_rate: float = 0.0005
    slippage_rate: float = 0.001
    stamp_tax_rate: float = 0.0
    min_commission: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def one_way_rate(self) -> float:
        """单边费率 (commission + slippage + stamp_tax)."""
        return self.commission_rate + self.slippage_rate + self.stamp_tax_rate

    def compute(self, turnover: float) -> float:
        """算一次调仓的总成本 (NAV 比例).

        Args:
            turnover: 换手率 (0-1 之间), 通常 0.5 * ||w_new - w_old||_1

        Returns:
            成本占 NAV 比例, 单期
        """
        if turnover < 0:
            turnover = abs(turnover)
        return float(turnover * self.one_way_rate)

    def compute_per_stock(
        self, weights_diff: np.ndarray
    ) -> np.ndarray:
        """算每只股票的成本 (按持仓变化绝对值 * 单边费率).

        Args:
            weights_diff: ndarray(N,), 持仓变化 (new - old)

        Returns:
            ndarray(N,), 每只股票占 NAV 比例
        """
        return np.abs(weights_diff) * self.one_way_rate

    def to_dict(self) -> dict:
        return {
            "commission_rate": self.commission_rate,
            "slippage_rate": self.slippage_rate,
            "stamp_tax_rate": self.stamp_tax_rate,
            "min_commission": self.min_commission,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CostModel":
        return cls(
            commission_rate=d.get("commission_rate", 0.0005),
            slippage_rate=d.get("slippage_rate", 0.001),
            stamp_tax_rate=d.get("stamp_tax_rate", 0.0),
            min_commission=d.get("min_commission", 0.0),
            extra=d.get("extra", {}),
        )