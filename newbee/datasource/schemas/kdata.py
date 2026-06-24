from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["KData"]


class KData(BaseModel):
    """日 K 线 (long format)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    trading_date: str  # YYYY-MM-DD — 交易日 (ISO string, 10 chars).
    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码, 形如 "600000.SH" / "000012.SZ".
    open: float | None  # CNY — 开盘价 (nullable, 停牌/未上市为 NaN).
    high: float | None  # CNY — 最高价 (nullable).
    low: float | None  # CNY — 最低价 (nullable).
    close: float | None  # CNY — 收盘价 (nullable).
    amount: float | None  # CNY — 成交额 (nullable, float64 — 高精度以支撑大额成交累积).
    volume: float | None  # shares — 成交量 (nullable, float64 — 避免长 horizon 累积溢出).
    close_adj: float | None  # CNY — 后复权收盘价 = close * adj_factor (post-adjusted).

    @field_validator("stock_code")
    @classmethod
    def _check_stock_code(cls, v: str) -> str:
        """9 字符 .SH/.SZ 后缀校验."""
        if not isinstance(v, str) or len(v) != 9 or v[6] != "." or v[7:] not in ("SH", "SZ"):
            raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ 格式, 得到 {v!r}")
        return v
