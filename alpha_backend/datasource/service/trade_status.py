"""TradeStatusService: 交易状态 (停牌/ST/活跃) full_init + daily_update.

注: M1 阶段 TradeStatus 数据获取依赖 akshare 的 stock_zh_a_hist 的字段推断
(成交量为 0 / 涨跌幅异常) + 公开 ST 名单 (best-effort). 简化实现: 如果当天
KData 的 high == low == close (且 close > 0) → 视作停牌候选; ST 需要外部源
(暂留 None).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.datasource.service.universe import UniverseService
from alpha_backend.datasource.storage.io import DataFile
from alpha_backend.datasource.storage.state import StateTracker, DEFAULT_RESUME_START
from alpha_backend.utils import logger


class TradeStatusService:
    """交易状态服务."""

    def __init__(self, *, root: str | None = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = REGISTRY.get("Trade_Status")
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        if root:
            self.state = StateTracker(Path(root) / "datas" / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()
        self.universe = UniverseService(root=str(self.root) if self.root else None)

    def _infer_status_from_kdata(
        self, kdata: pd.DataFrame, stock_codes: list[str]
    ) -> pd.DataFrame:
        """从 KData 推断 is_suspended / is_st / is_activate.

        简化规则 (M1):
        - is_suspended: 当天 high == low == close == 0 (停牌无成交) 或 OHLCV 全空
        - is_st: 未知 → 默认 False (M2 接入 akshare stock_zh_a_st)
        - is_activate: 非停牌且非 ST
        """
        if kdata.empty:
            return pd.DataFrame(
                columns=[
                    "trading_date",
                    "stock_code",
                    "is_suspended",
                    "is_st",
                    "is_activate",
                ]
            )
        df = kdata.copy()
        # 停牌: OHLCV 全空 或 (close == 0 且 high == low == close)
        suspended_mask = (
            df["close"].isna()
            | ((df["close"].fillna(0) == 0) & (df["high"].fillna(0) == df["low"].fillna(0)))
        )
        df["is_suspended"] = suspended_mask
        df["is_st"] = False  # TODO M2: 接入真实 ST 名单
        df["is_activate"] = ~df["is_suspended"] & ~df["is_st"]
        return df[
            [
                "trading_date",
                "stock_code",
                "is_suspended",
                "is_st",
                "is_activate",
            ]
        ]

    def full_init(
        self,
        *,
        start: str = DEFAULT_RESUME_START,
        batch_size: int = 100,
    ) -> dict[str, int]:
        """从 datas/KData.parquet 推断 Trade_Status (M1: 无外部源)."""
        kdata_dtype = REGISTRY.get("KData")
        kdata_file = (
            DataFile(kdata_dtype, root=self.root) if self.root else DataFile(kdata_dtype)
        )
        if not kdata_file.exists():
            raise RuntimeError("datas/KData.parquet 不存在; 请先跑 KDataService.full_init")

        # 读 KData, 按 trading_date 分组后逐组推断
        df = kdata_file.read()
        df = df[df["trading_date"] >= start]
        if df.empty:
            logger.warning("[TradeStatus] KData 在 start 之后无数据, 跳过 full_init")
            return {"groups": 0, "rows": 0}

        groups = df.groupby("trading_date", sort=True)
        all_dfs: list[pd.DataFrame] = []
        for date_str, group in groups:
            inferred = self._infer_status_from_kdata(group, self.universe.all_codes())
            all_dfs.append(inferred)
        if all_dfs:
            merged = pd.concat(all_dfs, ignore_index=True)
            self.file_.upsert(merged, conflict="replace")

        stats = self.file_.stats()
        self.state.update("Trade_Status", stats)
        return {"groups": len(groups), "rows": int(stats.row_count)}

    def daily_update(self, *, today: date | None = None) -> dict[str, int]:
        today_str = today.isoformat() if today else date.today().isoformat()
        start, end = self.state.resume_range("Trade_Status", latest=today_str)
        if start > end:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}
        # M1: 直接复用 full_init(start) 即可 (推断从 KData)
        return self.full_init(start=start)


__all__ = ["TradeStatusService"]