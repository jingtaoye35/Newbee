"""FinancialReportIncomeService: 利润表 full_init + daily_update.

Mirror KDataService 结构:
- 全量: fetch universe → 并发 chunk 拉 → upsert(replace) → state.update
- 增量: resume_range → 已 up-to-date 则跳过, 否则重拉 universe (idempotent upsert)
"""

from __future__ import annotations

from datasource.schema.financial_report_income import FinancialReportIncome

from datetime import date
from pathlib import Path

import pandas as pd

from datasource.service._financial_common import (
    parallel_chunked_fetch,
    upsert_and_update_state,
)
from datasource.service.universe import UniverseService
from datasource.adapter.finance import fetch_financial_report_income
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker, DEFAULT_RESUME_START
from logger import logger
from utils.parallel_run import parallel


# spawn-fork 安全: 模块级 @parallel worker
@parallel
def _fetch_one(code: str) -> pd.DataFrame:
    return fetch_financial_report_income(code)


class FinancialReportIncomeService:
    """利润表服务."""

    def __init__(self, *, root: Optional[str] = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = FinancialReportIncome
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
        """拉 universe 所有股票的利润表, upsert(replace)."""
        codes = self.universe.stock_pool()
        if not codes:
            raise RuntimeError("universe 为空, 请先跑 UniverseService.full_init")

        logger.info(f"[FinancialReportIncome] full_init: {len(codes)} stocks")
        summary, all_dfs = parallel_chunked_fetch(
            "Financial_Report_Income",
            codes,
            _fetch_one,
            desc="[FinancialReportIncome] full_init",
        )
        if not all_dfs:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}

        result = upsert_and_update_state(
            self.file_, self.state, "Financial_Report_Income", all_dfs=all_dfs
        )
        result["success"] = summary.success_count
        result["failed"] = len(summary.failed)
        return result

    def daily_update(self, *, today: Optional[str] = None, source: str = "em") -> dict[str, int]:
        today_str = today or date.today().isoformat()
        start, end = self.state.resume_range("Financial_Report_Income", latest=today_str)
        if start > end:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}
        return self.full_init(start=start, source=source)


__all__ = ["FinancialReportIncomeService"]
