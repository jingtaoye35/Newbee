from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["AdjFactor"]


class AdjFactor(BaseModel):
    """累积复权因子 (long format, float64 精度)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    trading_date: str  # YYYY-MM-DD — 交易日.
    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    adj_factor: float | None  # ratio — 累积复权因子 (float64 精度, 防长 horizon 漂移).

    @field_validator("stock_code")
    @classmethod
    def _check_stock_code(cls, v: str) -> str:
        """9 字符 .SH/.SZ 后缀校验."""
        if not isinstance(v, str) or len(v) != 9 or v[6] != "." or v[7:] not in ("SH", "SZ"):
            raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ 格式, 得到 {v!r}")
        return v
