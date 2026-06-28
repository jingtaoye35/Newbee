"""优化器测试 (单元 + 集成).

覆盖:
  单元:
    1. 2 只股票闭式解
    2. 换手率约束生效
    3. 无约束退化
    4. valid_mask 强制
  集成:
    5. 100 只股票 + 多约束并存
    6. inverse_vol
    7. equal_weight
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path("/Users/yejingtao/JohnsonProject/Newbee")
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.portfolio import (
    mean_variance,
    equal_weight,
    inverse_vol,
    LongOnly,
    WeightSum,
    MaxTurnover,
    MaxWeight,
    project_all,
    check_all,
    PortfolioState,
    CostModel,
)
from alpha_backend.engines.backtest_portfolio import run_portfolio_backtest


# ============ 单元测试 ============


def test_two_stock_closed_form():
    """2 只股票: 无协方差, 闭式解 = scores 全部分给 score 最高的."""
    scores = np.array([1.0, 0.0])
    cov = np.eye(2)
    w = mean_variance(scores, cov, risk_aversion=1.0,
                      long_only=True, weight_sum=1.0)
    assert abs(w.sum() - 1.0) < 1e-6
    assert w[0] > 0.9
    assert w[1] < 0.1


def test_max_turnover_constraint():
    """max_turnover 生效: 实际换手 ≤ cap."""
    current = np.array([0.5, 0.5])
    scores = np.array([10.0, 0.0])
    cov = np.eye(2)
    cap = 0.1
    w = mean_variance(scores, cov, current_weights=current,
                      risk_aversion=1.0, long_only=True, weight_sum=1.0,
                      max_turnover=cap)
    actual_to = 0.5 * np.abs(w - current).sum()
    assert actual_to <= cap + 1e-3, f"换手 {actual_to} 超 cap {cap}"


def test_unconstrained_degenerate():
    """无 sum 约束时, 权重和可以不为 1 (但被 weight_sum=1.0 锁定)."""
    scores = np.array([1.0, 1.0])
    cov = np.eye(2)
    w = mean_variance(scores, cov, risk_aversion=1.0,
                      long_only=True, weight_sum=1.0)
    # 对称 scores + 对称 cov + 长仓 → 等权
    assert abs(w[0] - 0.5) < 0.01
    assert abs(w[1] - 0.5) < 0.01


def test_valid_mask_forces_zero():
    """valid_mask=False 的位置 w=0."""
    scores = np.array([0.5, 1.0, 0.5])
    cov = np.eye(3)
    valid = np.array([True, False, True])
    w = mean_variance(scores, cov, risk_aversion=1.0, valid_mask=valid,
                      weight_sum=1.0)
    assert w[1] == 0
    assert abs(w.sum() - 1.0) < 1e-6


def test_all_invalid_returns_zero():
    """valid_mask 全 False → 全 0."""
    scores = np.array([0.5, 1.0, 0.5])
    cov = np.eye(3)
    valid = np.array([False, False, False])
    w = mean_variance(scores, cov, risk_aversion=1.0, valid_mask=valid)
    assert np.all(w == 0)


def test_long_only_constraint():
    """long_only=True 强制 w >= 0."""
    scores = np.array([1.0, -0.5, 0.5])
    cov = np.eye(3)
    w = mean_variance(scores, cov, risk_aversion=1.0, long_only=True)
    assert np.all(w >= 0)


# ============ inverse_vol / equal_weight ============


def test_inverse_vol():
    """inverse_vol 权重 ∝ 1/vol."""
    cov = np.diag([0.04, 0.16, 0.01])
    w = inverse_vol(cov, weight_sum=1.0)
    # vols = [0.2, 0.4, 0.1], inv = [5, 2.5, 10]
    expected = np.array([5, 2.5, 10]) / 17.5
    np.testing.assert_allclose(w, expected)


def test_equal_weight():
    w = equal_weight(5, weight_sum=1.0)
    np.testing.assert_allclose(w, np.ones(5) / 5)


# ============ 约束工具 ============


def test_long_only_project():
    lo = LongOnly()
    w = lo.project(np.array([-0.1, 0.3, 0.5]))
    assert np.all(w >= 0)


def test_weight_sum_project():
    ws = WeightSum(target=1.0)
    w = ws.project(np.array([0.3, 0.3, 0.3]))
    assert abs(w.sum() - 1.0) < 1e-6


def test_max_turnover_project():
    mt = MaxTurnover(max_turnover=0.2)
    prev = np.array([0.5, 0.3, 0.2])
    new = np.array([0.9, 0.05, 0.05])
    w = mt.project(new, prev)
    l1 = 0.5 * np.abs(w - prev).sum()
    assert l1 <= 0.2 + 1e-6


def test_max_weight_project():
    mw = MaxWeight(cap=0.4)
    w = mw.project(np.array([0.5, 0.3, 0.6]))
    assert np.all(w <= 0.4)


def test_project_all():
    """所有约束同时施加 (LongOnly → MaxWeight → WeightSum)."""
    w = project_all(
        np.array([0.5, 0.3, 0.6]),
        [LongOnly(), MaxWeight(cap=0.4), WeightSum(target=1.0)]
    )
    assert np.all(w >= 0)
    assert abs(w.sum() - 1.0) < 1e-6
    assert np.all(w <= 0.4)


def test_check_all_ok():
    ok, violated = check_all(
        np.array([0.3, 0.3, 0.4]),
        [LongOnly(), WeightSum(target=1.0), MaxTurnover(max_turnover=0.5)]
    )
    assert ok
    assert violated == []


def test_check_all_violated():
    ok, violated = check_all(
        np.array([-0.1, 0.5, 0.6]),
        [LongOnly(), WeightSum(target=1.0), MaxWeight(cap=0.4)]
    )
    assert not ok
    assert "LongOnly" in violated
    assert "MaxWeight" in violated


# ============ 集成: 100 只股票 + 多约束 ============


def test_100_stocks_with_all_constraints():
    """100 只股票 + LongOnly + WeightSum + MaxTurnover + MaxWeight."""
    np.random.seed(42)
    n = 100
    scores = np.random.randn(n)
    # 协方差: 1.0 对角 + 0.3 相关
    A = np.random.randn(n, n)
    cov = A @ A.T / n + np.eye(n) * 0.5  # PD

    valid = np.random.rand(n) > 0.1  # 10% invalid
    w = mean_variance(
        scores, cov,
        current_weights=np.zeros(n),
        risk_aversion=1.0,
        long_only=True,
        weight_sum=1.0,
        max_weight=0.05,
        max_turnover=0.3,
        valid_mask=valid,
    )
    assert abs(w.sum() - 1.0) < 1e-4
    assert np.all(w[~valid] == 0)
    assert np.all(w >= 0)
    assert np.all(w <= 0.05 + 1e-6)


# ============ 集成: portfolio 回测 + 成本 ============


def test_backtest_with_cost():
    """组合回测: max_turnover 约束 + 成本生效."""
    from datetime import date as _date, timedelta

    n, t = 50, 200
    np.random.seed(42)
    prices = 100 * np.cumprod(1 + np.random.randn(t, n) * 0.02, axis=0)
    alpha = np.random.randn(t, n)
    dates = [_date(2020, 1, 1) + timedelta(days=int(d)) for d in range(t)]

    class MockPool:
        stock_ids = [f"S{i}" for i in range(n)]

    cm = CostModel(commission_rate=0.0005, slippage_rate=0.001)
    result = run_portfolio_backtest(
        prices=prices, dates=dates, pool=MockPool(),
        alpha_scores=alpha, rebalance_freq=20,
        lookback_cov=60, max_turnover=0.3, max_weight=0.05,
        cost_model=cm, optimizer="mean_variance",
    )
    # cost = total_turnover * 0.0015
    total_to = sum(x["turnover"] for x in result.trades)
    expected = total_to * 0.0015
    assert abs(result.cost_paid.sum() - expected) < 1e-6
    # max_turnover: 后续调仓都 ≤ 0.3
    for i, tr in enumerate(result.trades):
        if i > 0:
            assert tr["turnover"] <= 0.3 + 1e-3


def test_portfolio_state_rebalance():
    """PortfolioState rebalance + cash 守恒."""
    state = PortfolioState(positions=np.zeros(5), cash=1.0)
    target = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
    state.rebalance(target, asof=None)
    assert abs(state.positions.sum() + state.cash - 1.0) < 1e-9
    assert state.asof is None
    assert len(state.history) == 1


# ============ fallback (SLSQP 未收敛) ============


def test_fallback_to_equal_weight_when_no_current_weights():
    """SLSQP 未收敛 + 无 current_weights → fallback 到 valid mask 内等权.

    通过 maxiter=1 强制 SLSQP 失败.
    """
    n = 4
    scores = np.array([1.0, 2.0, 3.0, 0.5])
    cov = np.eye(n)
    valid = np.array([True, True, False, True])
    w = mean_variance(
        scores, cov,
        current_weights=None,
        risk_aversion=1.0,
        long_only=True,
        weight_sum=1.0,
        valid_mask=valid,
        # maxiter=1 → SLSQP 必然来不及收敛
        maxiter=1,
    )
    # invalid 位置必须为 0
    assert w[2] == 0
    # valid 内等权: 1/3 各
    np.testing.assert_allclose(w[valid], np.ones(3) / 3)
    assert abs(w.sum() - 1.0) < 1e-6


def test_fallback_to_current_weights_when_provided():
    """SLSQP 未收敛 + 有非零 current_weights → 保留 current_weights.

    实盘语义: 今天不调仓, 避免在 solver 失控时贸然换仓.
    """
    n = 4
    scores = np.array([1.0, 2.0, 3.0, 0.5])
    cov = np.eye(n)
    valid = np.array([True, True, False, True])
    current = np.array([0.1, 0.4, 0.0, 0.5])
    w = mean_variance(
        scores, cov,
        current_weights=current,
        risk_aversion=1.0,
        long_only=True,
        weight_sum=1.0,
        valid_mask=valid,
        maxiter=1,
    )
    # 必须严格等于 current_weights (invalid 位置已由原值 0 覆盖)
    np.testing.assert_allclose(w, current)
    # 不应被 SLSQP 扭曲
    assert w[1] == pytest.approx(0.4)
    assert w[3] == pytest.approx(0.5)
    assert w[2] == 0


def test_fallback_ignores_zero_current_weights():
    """current_weights 全为 0 → 视为无信息, 仍然 fallback 到等权.

    避免 all-zero current_weights 触发"今日不调仓"分支
    (那样会一直停在全 0 仓位).
    """
    n = 3
    scores = np.array([0.1, 0.5, 0.2])
    cov = np.eye(n)
    valid = np.array([True, True, True])
    current = np.zeros(n)
    w = mean_variance(
        scores, cov,
        current_weights=current,
        risk_aversion=1.0,
        long_only=True,
        weight_sum=1.0,
        valid_mask=valid,
        maxiter=1,
    )
    # 全 0 current → fallback 等权, 不停在全 0
    np.testing.assert_allclose(w, np.ones(3) / 3)
    assert abs(w.sum() - 1.0) < 1e-6
