"""组合回测引擎 (Phase B).

工作流 (按 rebalance_date 迭代):
  for asof in trading_days:
    1. 累计收益到 asof (基于 weight * daily_return)
    2. 如果 asof 是 rebalance_date:
        a. 读 alpha scores (从 alpha_store 或现算)
        b. 算协方差矩阵 (过去 N 天 returns)
        c. 调优化器 (mean_variance / equal_weight / inverse_vol)
        d. 计算 turnover + cost
        e. 调仓 (更新 state)
    3. 记录 NAV, positions, daily_return

关键设计:
  - 不调仓的日子: 持仓不变, NAV = NAV_prev * (1 + sum(weight * return_t))
  - 调仓的日子: 先收当天收益 (基于旧 weight), 再换仓
  - 交易成本: 调仓日扣减 (在 NAV 累计后)
  - 协方差: Ledoit-Wolf 收缩 (M1 简化用 sample cov)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd

from newbee.datasource.calendar import align_to_trading_day, sessions_between
from newbee.datasource.storage.bars_adapter import Bars, load_bars as load_bars_from_parquet
from newbee.datasource.storage.pool_adapter import StockPool
from newbee.portfolio import (
    CostModel,
    LongOnly,
    PortfolioState,
    WeightSum,
    MaxTurnover,
    MaxWeight,
    equal_weight,
    inverse_vol,
    mean_variance,
    project_all,
)
from newbee.utils import logger


# ----- 数据结构 -----


@dataclass
class PortfolioBacktestResult:
    """组合回测结果."""

    nav: pd.Series  # (T,) NAV 曲线
    daily_return: pd.Series  # (T,) 日收益
    positions: pd.DataFrame  # (T, N) 持仓 (按调仓日, 中间不变)
    turnover: pd.Series  # (T,) 换手率 (调仓日非零)
    cost_paid: pd.Series  # (T,) 成本 (调仓日非零)
    trades: list[dict]  # 调仓记录
    extra: dict = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """算关键指标."""
        ret = self.daily_return.dropna()
        if len(ret) == 0:
            return {"error": "no return data"}
        ann_ret = float(ret.mean() * 252)
        ann_vol = float(ret.std() * np.sqrt(252))
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
        cum = (1 + ret).cumprod()
        mdd = float(((cum / cum.cummax()) - 1).min())
        avg_to = float(self.turnover[self.turnover > 0].mean()) if (self.turnover > 0).any() else 0.0
        total_cost = float(self.cost_paid.sum())
        return {
            "n_days": int(len(ret)),
            "ann_return": ann_ret,
            "ann_vol": ann_vol,
            "sharpe": sharpe,
            "max_drawdown": mdd,
            "avg_turnover": avg_to,
            "total_cost": total_cost,
            "final_nav": float(self.nav.iloc[-1]) if len(self.nav) else 1.0,
        }


# ----- 核心引擎 -----


def run_portfolio_backtest(
    *,
    prices: np.ndarray,  # (T, N) close 矩阵
    dates: list[date],  # T 个交易日
    pool: StockPool,  # universe
    alpha_scores: np.ndarray,  # (T, N) 每天的 alpha score (越大越想买)
    rebalance_freq: int = 20,  # 调仓频率 (天), 默认月频
    lookback_cov: int = 60,  # 协方差估计回看期
    risk_aversion: float = 1.0,
    max_turnover: float = 0.3,
    max_weight: float | None = 0.05,
    cost_model: CostModel | None = None,
    optimizer: str = "mean_variance",  # "mean_variance" | "equal_weight" | "inverse_vol"
) -> PortfolioBacktestResult:
    """组合回测.

    Args:
        prices: (T, N) close 矩阵
        dates: T 个交易日
        pool: 股票池
        alpha_scores: (T, N) alpha scores
        rebalance_freq: 调仓频率 (天)
        lookback_cov: 协方差估计窗口
        risk_aversion: λ, 仅 mean_variance 用
        max_turnover: 单次调仓最大换手率
        max_weight: 单只最大权重
        cost_model: 成本模型
        optimizer: 优化器选择

    Returns:
        PortfolioBacktestResult
    """
    T, N = prices.shape
    if len(dates) != T:
        raise ValueError(f"dates 长度 {len(dates)} != prices 行数 {T}")
    if alpha_scores.shape != (T, N):
        raise ValueError(f"alpha_scores 形状 {alpha_scores.shape} != ({T}, {N})")

    if cost_model is None:
        cost_model = CostModel()

    # 状态初始化
    state = PortfolioState(positions=np.zeros(N), cash=1.0)

    # 结果容器
    nav_arr = np.full(T, np.nan)
    pos_arr = np.full((T, N), np.nan)
    ret_arr = np.full(T, np.nan)
    to_arr = np.zeros(T)
    cost_arr = np.zeros(T)
    trades: list[dict] = []

    nav_t = 1.0  # 当前 NAV

    # 日收益: (T, N) → 用于累计
    daily_returns = np.full((T, N), np.nan)
    daily_returns[1:] = (prices[1:] / prices[:-1]) - 1.0

    for t in range(T):
        d = dates[t]

        # 1. 累计当天收益 (基于当前持仓)
        if t > 0 and not np.isnan(daily_returns[t]).all():
            # mask 掉 NaN (停牌) 和 0 持仓
            valid = ~np.isnan(daily_returns[t]) & (state.positions > 1e-9)
            if valid.any():
                # 用市场收益近似 (停牌的用市场等权收益)
                avg = np.nanmean(daily_returns[t])
                dr = 0.0
                for j in range(N):
                    if state.positions[j] > 1e-9:
                        if not np.isnan(daily_returns[t, j]):
                            dr += state.positions[j] * daily_returns[t, j]
                        else:
                            dr += state.positions[j] * avg
                ret_arr[t] = dr
                nav_t = nav_t * (1 + dr)

        # 2. 调仓判断
        is_rebal = (t > 0) and (t % rebalance_freq == 0)

        if is_rebal:
            # 2a. 读 scores
            scores_t = alpha_scores[t].copy()

            # 2b. 算协方差
            start_cov = max(0, t - lookback_cov)
            ret_window = daily_returns[start_cov:t]  # (W, N)
            # 去掉全 NaN 列
            valid_cols = ~np.all(np.isnan(ret_window), axis=0)
            cov = np.eye(N) * 1e-4  # 默认
            if valid_cols.sum() > 1:
                # 用 nan-aware cov (填 0)
                rw = np.where(np.isnan(ret_window), 0.0, ret_window)
                cov_full = np.cov(rw, rowvar=False)
                if cov_full.shape == (N, N):
                    # 年化
                    cov = cov_full * 252
                    # 数值稳定: 对角 + 1e-6
                    cov = cov + np.eye(N) * 1e-6
                    cov[~valid_cols, :] = 0
                    cov[:, ~valid_cols] = 0

            # 2c. 优化
            valid_mask = valid_cols.copy()  # 只在有价格的位置调仓
            # NaN scores 也算 invalid
            valid_mask &= ~np.isnan(scores_t)
            # 取负号 (alpha score 越大代表"alpha 高"→"期望超额收益低", 实务取负)
            scores_for_opt = -np.where(np.isnan(scores_t), 0.0, scores_t)

            if optimizer == "equal_weight":
                w_opt = equal_weight(N, valid_mask=valid_mask, weight_sum=1.0)
            elif optimizer == "inverse_vol":
                w_opt = inverse_vol(cov, valid_mask=valid_mask, weight_sum=1.0)
            else:  # mean_variance
                # first trade: 没有 prev, 不施加 max_turnover
                cur = state.positions
                apply_to = max_turnover if (cur.sum() > 1e-6 and np.any(cur > 1e-9)) else None
                w_opt = mean_variance(
                    scores_for_opt, cov,
                    current_weights=cur,
                    risk_aversion=risk_aversion,
                    long_only=True,
                    weight_sum=1.0,
                    max_weight=max_weight,
                    max_turnover=apply_to,
                    valid_mask=valid_mask,
                )

            # 2d. 调仓
            turnover = state.turnover_to(w_opt)
            cost = cost_model.compute(turnover)
            to_arr[t] = turnover
            cost_arr[t] = cost
            state.rebalance(w_opt, asof=d, turnover=turnover, cost=cost)
            trades.append({
                "asof": d.isoformat(),
                "turnover": float(turnover),
                "cost": float(cost),
                "n_holdings": int((w_opt > 1e-9).sum()),
                "max_weight": float(w_opt.max()) if w_opt.size else 0.0,
            })

        # 3. 记录
        nav_arr[t] = nav_t
        pos_arr[t] = state.positions

    # 扣减调仓成本
    for t in range(T):
        if cost_arr[t] > 0:
            nav_arr[t] = nav_arr[t] * (1 - cost_arr[t])

    # 包装
    nav = pd.Series(nav_arr, index=pd.DatetimeIndex([d for d in dates]), name="nav")
    daily_ret = pd.Series(ret_arr, index=nav.index, name="return")
    positions = pd.DataFrame(
        pos_arr, index=nav.index, columns=pool.stock_ids
    )
    turnover = pd.Series(to_arr, index=nav.index, name="turnover")
    cost_paid = pd.Series(cost_arr, index=nav.index, name="cost")

    return PortfolioBacktestResult(
        nav=nav,
        daily_return=daily_ret,
        positions=positions,
        turnover=turnover,
        cost_paid=cost_paid,
        trades=trades,
    )


def run_portfolio_backtest_from_store(
    *,
    pool: StockPool,
    bars: Bars,
    alpha_panel: np.ndarray,  # (T, N) 与 bars.dates / bars.indices 对齐
    start_idx: int = 0,
    end_idx: int | None = None,
    rebalance_freq: int = 20,
    lookback_cov: int = 60,
    risk_aversion: float = 1.0,
    max_turnover: float = 0.3,
    max_weight: float | None = 0.05,
    cost_model: CostModel | None = None,
    optimizer: str = "mean_variance",
) -> PortfolioBacktestResult:
    """从 Bars + alpha panel 回测 (便利接口)."""
    if end_idx is None:
        end_idx = len(bars.dates)

    dates = list(bars.dates[start_idx:end_idx])
    # prices 矩阵 (T, N)
    # bars.matrix: (T, N, 6), close 在 [:, :, 3] (open, high, low, close, vol, adj_close)
    # 实际字段顺序由 Bars dataclass 决定, 这里按 close=3
    prices_full = bars.matrix[start_idx:end_idx, :, :]  # (T, N, F)
    if prices_full.shape[2] >= 5:
        # 优先用 adj_close (复权), 更适合回测
        prices = prices_full[:, :, 5]
    else:
        prices = prices_full[:, :, 3]

    alpha = alpha_panel[start_idx:end_idx, :]

    return run_portfolio_backtest(
        prices=prices,
        dates=dates,
        pool=pool,
        alpha_scores=alpha,
        rebalance_freq=rebalance_freq,
        lookback_cov=lookback_cov,
        risk_aversion=risk_aversion,
        max_turnover=max_turnover,
        max_weight=max_weight,
        cost_model=cost_model,
        optimizer=optimizer,
    )