"""DividendHistoryService: 历史分红 full_init + daily_update.

特点:
- AkShare 没有"全市场单次返回"接口; 必须 per-symbol 拉 cninfo 巨潮.
- daily_update 用 30 天节流 (dividend 不像行情那样日更).
"""

from __future__ import annotations

from datasource.schema.dividend_history import DividendHistory

from datetime import date, datetime
from pathlib import Path

import pandas as pd

from datasource.service._financial_common import (
    parallel_chunked_fetch,
    upsert_and_update_state,
)
from datasource.service.universe import UniverseService
from datasource.adapter.finance import fetch_dividend_history
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker
from logger import logger
from utils.parallel_run import parallel

# dividend 更新节流窗口: 30 天
_DIVIDEND_THROTTLE_DAYS = 30


@parallel
def _fetch_one(code: str) -> pd.DataFrame:
    return fetch_dividend_history(code)


class DividendHistoryService:
    """历史分红服务 (per-symbol 拉取 + 30 天节流)."""

    def __init__(self, *, root: Optional[str] = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = DividendHistory
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        if root:
            self.state = StateTracker(self.file_.path.parent / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()  # 走 default_state_path() → datasource_dir/_Manifest/
        self.universe = UniverseService(root=str(self.root) if self.root else None)

    def full_init(self) -> dict[str, int]:
        """拉 universe 所有股票的分红事件, upsert(replace)."""
        codes = self.universe.stock_pool()
        if not codes:
            raise RuntimeError("universe 为空, 请先跑 UniverseService.full_init")

        logger.info(f"[DividendHistory] full_init: {len(codes)} stocks")
        summary, all_dfs = parallel_chunked_fetch(
            "Dividend_History",
            codes,
            _fetch_one,
            desc="[DividendHistory] full_init",
        )
        if not all_dfs:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}

        result = upsert_and_update_state(
            self.file_, self.state, "Dividend_History", all_dfs=all_dfs
        )
        result["success"] = summary.success_count
        result["failed"] = len(summary.failed)
        return result

    def daily_update(self, *, today: Optional[str] = None) -> dict[str, int]:
        """距上次更新 ≥ 30 天才重拉, 否则跳过."""
        from utils.tools import parse_iso_date
        today_d = parse_iso_date(today) if today else date.today()
        state = self.state.read().get("Dividend_History")
        if state is None or not state.updated_at:
            # 首次 / 无状态: 直接 full_init
            logger.info("[DividendHistory] 无 state, 首次跑 full_init")
            return self.full_init()

        try:
            last_update = datetime.fromisoformat(state.updated_at).date()
        except ValueError:
            logger.warning(
                f"[DividendHistory] state.updated_at 无法解析: {state.updated_at!r}, 触发 full_init"
            )
            return self.full_init()

        days_elapsed = (today_d - last_update).days
        if days_elapsed < _DIVIDEND_THROTTLE_DAYS:
            return {
                "rows": int(self.file_.stats().row_count),
                "skipped": True,
                "reason": f"within {_DIVIDEND_THROTTLE_DAYS}-day throttle window (last={last_update})",
            }
        logger.info(f"[DividendHistory] 距上次更新 {days_elapsed} 天, 触发 full_init")
        return self.full_init()


__all__ = ["DividendHistoryService"]
