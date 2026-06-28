from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["TradeStatus"]


class TradeStatus(BaseModel):
    """交易状态 (停牌/ST/活跃, long format)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    trading_date: str  # YYYY-MM-DD — 交易日.
    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    is_suspended: bool  # bool — True 当日停牌.
    is_st: bool  # bool — True 当日被 ST 标记.
    is_activate: bool  # bool — True 当日正常交易 (非停牌非 ST 非退市).

    @field_validator("stock_code")
    @classmethod
    def _check_stock_code(cls, v: str) -> str:
        """9 字符 .SH/.SZ 后缀校验."""
        if not isinstance(v, str) or len(v) != 9 or v[6] != "." or v[7:] not in ("SH", "SZ"):
            raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ 格式, 得到 {v!r}")
        return v
