"""因子注册表 (单例, 内存版).

提供:
  - register(spec=...) 装饰器
  - get(name) -> SimpleFactor
  - list_all() -> list[FactorSpec]
  - exists(name) -> bool
  - unregister(name) -> bool

线程安全: 用 threading.Lock 保护内部 dict.
"""
from __future__ import annotations

import threading
from typing import Callable

from alpha_backend.factors.base import FactorSpec, SimpleFactor

_LOCK = threading.Lock()
_REGISTRY: dict[str, SimpleFactor] = {}
_REGISTRY_META: dict[str, FactorSpec] = {}


def register(
    spec: FactorSpec | None = None,
    *,
    name: str | None = None,
    version: str = "1.0",
    window: int | None = None,
    deps: tuple[str, ...] = (),
) -> Callable:
    """装饰器: 把一个 (prices, asof, **kwargs) -> ndarray 的函数注册为因子.

    用法:
        @register(spec=FactorSpec(name="momentum_20", window=20, deps=("adj_close",)))
        def momentum_20(prices, asof):
            ...

    或简写:
        @register(name="momentum_20", window=20)
        def momentum_20(prices, asof):
            ...
    """
    def decorator(func: Callable) -> SimpleFactor:
        # 解析 spec
        if spec is not None:
            s = spec
        else:
            if name is None:
                raise ValueError("register() 必须传 spec= 或 name=")
            s = FactorSpec(name=name, version=version, window=window, deps=deps)

        factor = SimpleFactor(spec=s, func=func, universe_size=0)
        with _LOCK:
            if s.name in _REGISTRY:
                raise ValueError(f"因子 {s.name} 已注册, 不可重复")
            _REGISTRY[s.name] = factor
            _REGISTRY_META[s.name] = s
        return factor

    return decorator


def get(name: str) -> SimpleFactor:
    """按名取因子, 不存在抛 KeyError."""
    with _LOCK:
        if name not in _REGISTRY:
            raise KeyError(f"因子 {name} 未注册, 已注册: {list(_REGISTRY.keys())}")
        return _REGISTRY[name]


def get_spec(name: str) -> FactorSpec:
    """按名取因子元信息."""
    with _LOCK:
        if name not in _REGISTRY_META:
            raise KeyError(f"因子 {name} 未注册")
        return _REGISTRY_META[name]


def exists(name: str) -> bool:
    with _LOCK:
        return name in _REGISTRY


def unregister(name: str) -> bool:
    with _LOCK:
        if name in _REGISTRY:
            del _REGISTRY[name]
            del _REGISTRY_META[name]
            return True
        return False


def list_all() -> list[FactorSpec]:
    """返回所有已注册因子的 FactorSpec (按 name 排序)."""
    with _LOCK:
        return sorted(_REGISTRY_META.values(), key=lambda s: s.name)


def clear() -> None:
    """清空注册表 (测试用)."""
    with _LOCK:
        _REGISTRY.clear()
        _REGISTRY_META.clear()