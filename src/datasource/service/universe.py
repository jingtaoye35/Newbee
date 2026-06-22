"""UniverseService: 自建股票池 (append-only, 9 字符 stock_code)."""

from __future__ import annotations

from datasource.schema.universe import Universe

import hashlib
import numpy as np
import pandas as pd
from typing import Optional

from datasource.adapter.akshare import fetch_index_constituents, fetch_ipo_date
from datasource.adapter.baostock import fetch_ipo_date_baostock
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker, DEFAULT_RESUME_START
from utils.tools import check_stock_code
from logger import logger


class UniverseService:
    """自建股票池服务."""

    def __init__(self, *, root: Optional[str] = None) -> None:
        from pathlib import Path

        self.root = Path(root) if root else None
        self.dtype = Universe
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        self.state = StateTracker()  # 走 default_state_path() → datasource_dir/_Manifest/

    # ---------- init ----------

    def full_init(
        self,
        *,
        index_name: str = "csi1000",
        backdate_to: str = DEFAULT_RESUME_START,
    ) -> dict[str, int]:
        """从指数拉成分股 + 拉每只 IPO 日期 → 写 datas/Universe.parquet.

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
        # new_codes = [c for c in codes["stock_code"] if c not in existing_codes]

        rows: list[dict[str, object]] = []
        next_idx = self._next_index()
        new_codes = []
        for _, row in codes.iterrows():
            sc = row["stock_code"]
            if sc in existing_codes:
                continue
            check_stock_code(sc)
            rows.append({ "stock_index": int(next_idx), "stock_code": sc, "stock_name": row["stock_name"]})
            next_idx += 1
            new_codes.append(sc)

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
            "added": len(new_codes)
        }

    # ---------- query ----------

    # def active_mask(self, asof: str) -> np.ndarray:
    #     if not self.file_.exists():
    #         raise FileNotFoundError("Universe.parquet 不存在; 请先跑 full_init")
    #     df = self.file_.read(columns=["stock_code", "ipo_date"])
    #     mask = df["ipo_date"].astype(str) <= asof
    #     return mask.to_numpy(dtype=bool)

    def size(self) -> int:
        if not self.file_.exists():
            return 0
        return int(self.file_.stats().stock_count)

    def stock_pool(self) -> list[str]:
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


# def _fetch_ipo_date(stock_code: str) -> str:
#     """Fetch IPO date with adapter-driven fallback (akshare → baostock).

#     严格失败语义:
#     - akshare 成功 → 直接返回
#     - akshare 抛异常 / 返回 None → 立刻换 baostock
#     - baostock 成功 → 返回
#     - baostock 也失败 → 抛 RuntimeError, 不再吞

#     Returns:
#         IPO 日期 (YYYY-MM-DD).

#     Raises:
#         RuntimeError: akshare 和 baostock 都拿不到数据. caller 应立即停止,
#         不应占位 ("1990-01-01") 静默继续 — 占位会污染 universe 的 ipo_date 字段,
#         影响 active_mask(asof) 的下游计算.
#     """
#     ak_err: BaseException | None = None
#     try:
#         result = fetch_ipo_date(stock_code)
#         if result:
#             return result
#     except Exception as e:
#         ak_err = e

#     ak_msg = repr(ak_err) if ak_err is not None else "returned None"
#     logger.error(
#         f"[akshare→baostock] akshare fetch_ipo_date failed for {stock_code}: "
#         f"{ak_msg}, switching to baostock"
#     )

#     bs_result = fetch_ipo_date_baostock(stock_code)
#     if bs_result:
#         return bs_result

#     raise RuntimeError(
#         f"fetch_ipo_date({stock_code}) failed on both adapters: akshare={ak_msg}, baostock=None"
#     )


__all__ = ["UniverseService"]
