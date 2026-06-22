"""FinancialIndicatorService: 主要财务指标 full_init + daily_update.

特点:
- 单一源 (stock_financial_analysis_indicator_em); 失败 symbol 仅 warn-and-skip,
  不阻塞其他 symbol, 不做 vendor fallback.
"""

from __future__ import annotations

from datasource.schema.financial_indicator import FinancialIndicator

from datetime import date
from pathlib import Path

import pandas as pd

from datasource.service._financial_common import (
    parallel_chunked_fetch,
    upsert_and_update_state,
)
from datasource.service.universe import UniverseService
from datasource.adapter.finance import fetch_financial_indicator
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker, DEFAULT_RESUME_START
from logger import logger
from utils.parallel_run import parallel


@parallel
def _fetch_one(code: str) -> pd.DataFrame:
    return fetch_financial_indicator(code)


class FinancialIndicatorService:
    """主要财务指标服务 (单一源, warn-and-skip)."""

    def __init__(self, *, root: Optional[str] = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = FinancialIndicator
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        if root:
            self.state = StateTracker(self.file_.path.parent / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()  # 走 default_state_path() → datasource_dir/_Manifest/
        self.universe = UniverseService(root=str(self.root) if self.root else None)

    def full_init(
        self,
        *,
        start: str = DEFAULT_RESUME_START,
        source: str = "em",
    ) -> dict[str, int]:
        codes = self.universe.stock_pool()
        if not codes:
            raise RuntimeError("universe 为空, 请先跑 UniverseService.full_init")

        logger.info(f"[FinancialIndicator] full_init: {len(codes)} stocks")
        summary, all_dfs = parallel_chunked_fetch(
            "Financial_Indicator",
            codes,
            _fetch_one,
            desc="[FinancialIndicator] full_init",
        )
        if not all_dfs:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}

        result = upsert_and_update_state(
            self.file_, self.state, "Financial_Indicator", all_dfs=all_dfs
        )
        result["success"] = summary.success_count
        result["failed"] = len(summary.failed)
        return result

    def daily_update(self, *, today: Optional[str] = None, source: str = "em") -> dict[str, int]:
        today_str = today or date.today().isoformat()
        start, end = self.state.resume_range("Financial_Indicator", latest=today_str)
        if start > end:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}
        return self.full_init(start=start, source=source)


__all__ = ["FinancialIndicatorService"]
