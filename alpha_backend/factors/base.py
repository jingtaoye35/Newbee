"""因子基类 + Protocol.

核心契约:
  - compute(asof, universe) -> ndarray(N,), shape 必须 == universe.size()
  - 不活跃股票位置返回 NaN
  - 严格只用 asof 之前的数据 (无 look-ahead)
  - 注册到 factors.registry 统一管理

设计: 用 dataclass + Protocol, 而不是抽象类, 因为:
  - Protocol 给静态类型提示, 不强制继承
  - dataclass 方便做 metadata (version / window / deps)
  - 因子可以是函数, 用 @register 装饰器包装
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, Protocol, runtime_checkable

import numpy as np

# ---------- 抽象因子协议 ----------


@runtime_checkable
class Factor(Protocol):
    """因子协议 (静态鸭子类型).

    实现 compute(asof, ...) -> ndarray(N,) 即可. 业务代码可注册任意 callable.
    """

    name: str
    version: str

    def compute(self, asof: date, *args, **kwargs) -> np.ndarray: ...


# ---------- 因子元信息 ----------


@dataclass(frozen=True)
class FactorSpec:
    """因子元信息 (写入 manifest / cache key / 日志)."""

    name: str
    version: str = "1.0"
    window: int | None = None
    deps: tuple[str, ...] = ()
    created_at: str = ""
    notes: str = ""

    def cache_key(self) -> str:
        """cache 路径用的 key: {name}@{version}"""
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class FactorResult:
    """compute() 的标准返回: ndarray(N,) + universe 索引信息."""

    values: np.ndarray  # shape (N,), 严格 PIT, 不活跃位置 NaN
    asof: date
    spec: FactorSpec
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.values.ndim != 1:
            raise ValueError(f"FactorResult.values 必须是 1-D, got shape={self.values.shape}")


# ---------- 简单函数式因子 ----------


@dataclass(frozen=True)
class SimpleFactor:
    """把一个 (prices, asof) -> ndarray(N,) 的函数包装成 Factor.

    用法:
        @register(spec=FactorSpec(name="momentum_20", window=20, deps=("adj_close",)))
        def momentum_20(prices: np.ndarray, asof: date) -> np.ndarray:
            ...
    """

    spec: FactorSpec
    func: Callable[..., np.ndarray]
    universe_size: int  # 上次 compute 时记录的 (用于 shape 校验)

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def version(self) -> str:
        return self.spec.version

    def compute(self, asof: date, *args, **kwargs) -> np.ndarray:
        out = self.func(*args, asof=asof, **kwargs)
        if out.ndim != 1:
            raise ValueError(
                f"因子 {self.name} compute() 返回 shape={out.shape}, 期望 1-D"
            )
        return out

    def __call__(self, *args, asof: date | None = None, **kwargs) -> np.ndarray:
        """调用接口: factor(prices, asof=...) -> ndarray(N,).

        为方便, 把 asof 提到 kwargs (func 也接受 asof).
        """
        if asof is None:
            raise ValueError(f"因子 {self.name} 调用必须传 asof=date")
        return self.func(*args, asof=asof, **kwargs)


# ---------- 工具: NaN mask ----------


def nan_positions(values: np.ndarray) -> np.ndarray:
    """返回 NaN 位置的 boolean mask (N,)."""
    return np.isnan(values)


def n_nonan(values: np.ndarray) -> int:
    """返回非 NaN 数量."""
    return int(np.sum(~np.isnan(values)))


def standardize(values: np.ndarray, *, ddof: int = 0) -> np.ndarray:
    """横截面 z-score 标准化 (忽略 NaN)."""
    valid = ~np.isnan(values)
    if valid.sum() == 0:
        return values
    mu = np.nanmean(values)
    sigma = np.nanstd(values, ddof=ddof)
    if sigma == 0 or np.isnan(sigma):
        return values
    out = np.full_like(values, np.nan)
    out[valid] = (values[valid] - mu) / sigma
    return out


def rank_(values: np.ndarray) -> np.ndarray:
    """横截面 rank (1..N), NaN 位置保持 NaN.

    平均 rank 处理 ties.
    """
    from scipy.stats import rankdata

    valid = ~np.isnan(values)
    out = np.full_like(values, np.nan)
    if valid.sum() == 0:
        return out
    out[valid] = rankdata(values[valid], method="average")
    return out