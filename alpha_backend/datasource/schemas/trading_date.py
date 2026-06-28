from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["TradingDate"]


class TradingDate(BaseModel):
    """A 股交易日历 (single-column reference datas, CSV-backed)."""

    model_config = ConfigDict(extra="forbid", frozen=False)

    trading_date: str  # YYYY-MM-DD — 实际交易日 (ISO YYYY-MM-DD), 例如 2024-01-02.
