"""UniverseService: 自建股票池 (append-only, 9 字符 stock_code)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from newbee.datasource.registry import REGISTRY
from newbee.datasource.sources.akshare import fetch_index_constituents, fetch_ipo_date
from newbee.datasource.storage.io import DataFile
from newbee.datasource.storage.state import StateTracker
from newbee.utils import logger


class UniverseService:
    """自建股票池服务."""

    def __init__(self, *, root: str | None = None) -> None:
        from pathlib import Path

        self.root = Path(root) if root else None
        self.dtype = REGISTRY.get("Universe")
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        self.state = StateTracker()

    # ---------- init ----------

    def full_init(
        self,
        *,
        index_name: str = "csi1000",
        backdate_to: str = "2020-01-01",
    ) -> dict[str, int]:
        """从指数拉成分股 + 拉每只 IPO 日期 → 写 data/Universe.parquet.

        Returns:
            {"total": N, "added": M, "with_ipo": K}
        """
        logger.info(f"[Universe] full_init: index={index_name} backdate_to={backdate_to}")
        codes = fetch_index_constituents(index_name)
        logger.info(f"[Universe] {len(codes)} constituents from {index_name}")

        # 已有 rows (避免重复)
        existing_codes: set[str] = set()
        if self.file_.exists():
            df_old = self.file_.read(columns=["stock_code"])
            existing_codes = set(df_old["stock_code"].tolist())
        new_codes = [c for c in codes if c not in existing_codes]

        rows: list[dict[str, object]] = []
        next_idx = self._next_index()
        for code in new_codes:
            ipo = fetch_ipo_date(code) or "1990-01-01"
            rows.append(
                {
                    "stock_index": int(next_idx),
                    "stock_code": code,
                    "ipo_date": ipo,
                }
            )
            next_idx += 1

        if rows:
            df_new = pd.DataFrame(rows)
            self.file_.upsert(df_new, conflict="ignore")
            logger.info(f"[Universe] appended {len(rows)} new stocks (next_idx={next_idx})")

        # 写 state
        stats = self.file_.stats()
        sha = self._compute_sha()
        self.state.update("Universe", stats, universe_sha=sha)
        return {
            "total": len(codes),
            "added": len(new_codes),
            "with_ipo": sum(1 for r in rows if r["ipo_date"] != "1990-01-01"),
        }

    # ---------- query ----------

    def active_mask(self, asof: str) -> np.ndarray:
        """基于 trading_date >= ipo_date 的活跃 mask (long format 返回 ndarray(N,)).

        Returns:
            ndarray(bool,) of length stock_count, True 表示 asof 时已上市.
        """
        if not self.file_.exists():
            raise FileNotFoundError("Universe.parquet 不存在; 请先跑 full_init")
        df = self.file_.read(columns=["stock_code", "ipo_date"])
        mask = df["ipo_date"].astype(str) <= asof
        return mask.to_numpy(dtype=bool)

    def size(self) -> int:
        if not self.file_.exists():
            return 0
        return int(self.file_.stats().stock_count)

    def all_codes(self) -> list[str]:
        if not self.file_.exists():
            return []
        df = self.file_.read(columns=["stock_code"])
        return df["stock_code"].tolist()

    # ---------- helpers ----------

    def _next_index(self) -> int:
        if not self.file_.exists():
            return 0
        stats = self.file_.stats()
        return stats.row_count  # 行数 == 已分配的 stock_index 数 (append-only)

    def _compute_sha(self) -> str:
        """基于 parquet 内容计算 sha256 (前 16 位)."""
        from pathlib import Path

        path = self.file_.path
        if not path.exists():
            return "empty"
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]


__all__ = ["UniverseService"]