from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["Universe"]


class Universe(BaseModel):
    """自建股票池 (append-only, stock_index 单调递增)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_index: int  # int — 单调递增 idx, 一旦分配永不回收 (即使股票退市).
    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    ipo_date: str  # YYYY-MM-DD — IPO 日期, 用于 active_mask(asof) 计算.

    @field_validator("stock_code")
    @classmethod
    def _check_stock_code(cls, v: str) -> str:
        """9 字符 .SH/.SZ 后缀校验."""
        if not isinstance(v, str) or len(v) != 9 or v[6] != "." or v[7:] not in ("SH", "SZ"):
            raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ 格式, 得到 {v!r}")
        return v
