"""StockBasicDataService: 股票基础数据 (复权因子 + 涨跌停价 + 申万行业) full_init + daily_update.

M1 简化: 由于 sina 源不直接给 adj_factor, 而东财源给 hfq 价, 我们用
"close / close_adj" 的比值作为 adj_factor 写入. 这是一个 best-effort
实现: 假设数据源给的 close_adj 已经是后复权, 且 close 是真实收盘价.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from newbee.datasource.registry import REGISTRY
from newbee.datasource.service.universe import UniverseService
from newbee.datasource.storage.io import DataFile
from newbee.datasource.storage.state import StateTracker
from newbee.utils import logger


class StockBasicDataService:
    """股票基础数据服务 (复权因子 + 涨跌停价 + 申万行业)."""

    def __init__(self, *, root: str | None = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = REGISTRY.get("Stock_Basic_Data")
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        if root:
            self.state = StateTracker(Path(root) / "data" / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()
        self.universe = UniverseService(root=str(self.root) if self.root else None)

    def _infer_adj_from_kdata(self, kdata: pd.DataFrame) -> pd.DataFrame:
        """adj_factor = close_adj / close (假设两者均非 0 / NaN).

        返回 trading_date / stock_code / adj_factor / limit_upper_price / limit_lower_price /
        sw_industry / total_share / turnover. 后五个字段 M1 阶段从 KData 无法推得,
        填 None, 后续接入涨跌停/行业/股本数据源再回填.
        """
        cols = [
            "trading_date",
            "stock_code",
            "adj_factor",
            "limit_upper_price",
            "limit_lower_price",
            "sw_industry",
            "total_share",
            "turnover",
        ]
        if kdata.empty:
            return pd.DataFrame(columns=cols)
        df = kdata[["trading_date", "stock_code", "close", "close_adj"]].copy()
        # 仅保留两者都有效
        valid = df["close"].notna() & df["close_adj"].notna() & (df["close"] != 0)
        df = df[valid].copy()
        df["adj_factor"] = (df["close_adj"] / df["close"]).astype("float64")
        df["limit_upper_price"] = None
        df["limit_lower_price"] = None
        df["sw_industry"] = None
        df["total_share"] = None
        df["turnover"] = None
        return df[cols]

    def full_init(self, *, start: str = "2020-01-01") -> dict[str, int]:
        """从 KData 推算 adj_factor."""
        kdata_dtype = REGISTRY.get("KData")
        kdata_file = (
            DataFile(kdata_dtype, root=self.root) if self.root else DataFile(kdata_dtype)
        )
        if not kdata_file.exists():
            raise RuntimeError("data/KData.parquet 不存在; 请先跑 KDataService.full_init")

        df = kdata_file.read()
        df = df[df["trading_date"] >= start]
        if df.empty:
            logger.warning("[StockBasicData] KData 在 start 之后无数据, 跳过 full_init")
            return {"rows": 0}

        inferred = self._infer_adj_from_kdata(df)
        if not inferred.empty:
            self.file_.upsert(inferred, conflict="replace")

        stats = self.file_.stats()
        self.state.update("Stock_Basic_Data", stats)
        return {"rows": int(stats.row_count)}

    def daily_update(self, *, today: date | None = None) -> dict[str, int]:
        today_str = today.isoformat() if today else date.today().isoformat()
        start, end = self.state.resume_range("Stock_Basic_Data", latest=today_str)
        if start > end:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}
        return self.full_init(start=start)


__all__ = ["StockBasicDataService"]
