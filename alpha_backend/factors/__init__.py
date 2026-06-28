"""因子层: 基类、注册表、经典因子、计算 pipeline."""
from alpha_backend.factors.base import (
    Factor,
    FactorSpec,
    FactorResult,
    SimpleFactor,
    standardize,
    rank_,
    n_nonan,
    nan_positions,
)
from alpha_backend.factors.registry import (
    register,
    get,
    get_spec,
    exists,
    unregister,
    list_all,
    clear as clear_registry,
)

__all__ = [
    "Factor",
    "FactorSpec",
    "FactorResult",
    "SimpleFactor",
    "standardize",
    "rank_",
    "n_nonan",
    "nan_positions",
    "register",
    "get",
    "get_spec",
    "exists",
    "unregister",
    "list_all",
    "clear_registry",
]