"""`StockPool` adapter — 兼容 `newbee.data.universe.StockPool` 的 thin shim.

读取 `data/Universe.parquet` (3 列: stock_index, stock_code, ipo_date),
对外暴露 legacy StockPool 的核心 API:

  - `load(path)` — 工厂方法, 读 Universe.parquet
  - `stock_ids` — list[stock_code] (9 字符), 按 stock_index 升序
  - `size()` — 总股票数 (含历史已退市)
  - `export()` — DataFrame(stock_index, stock_code, ipo_date)
  - `active_mask(asof)` — ndarray(bool, N), 基于 ipo_date (asof 当天及之前上市)
  - `idx_of(stock_code)` — int | None, 用 stock_code 查 stock_index
  - `stock_of(idx)` — str | None, 用 stock_index 查 stock_code
  - `add(stock_code, source, ipo_date=None)` — 幂等追加, 返回分配的 stock_index

与 legacy StockPool 的差异 (迁移时已知):
  - stock_id (6 位) → stock_code (9 字符). export() 列名也从 stock_id
    改为 stock_code. 旧代码如访问 `pool.export()["stock_id"]` 需改为
    `["stock_code"]`.
  - 不再保留 added_at / source / status / retire_dt / note 等列 — Universe
    是 append-only, 股票"退休"通过 `UniverseService` 单独管理.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile

# ---------- 默认路径 ----------

DEFAULT_POOL_PATH = Path("data/Universe.parquet")

# ---------- 主类 ----------


@dataclass
class StockPool:
    """股票池适配 (在 `Universe.parquet` 上提供 legacy StockPool API)."""

    path: Path = DEFAULT_POOL_PATH
    _df: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    # ---------- 工厂方法 ----------

    @classmethod
    def load(cls, path: Path = DEFAULT_POOL_PATH) -> "StockPool":
        """加载 Universe.parquet. 不存在则返回空 pool (列已对齐 schema)."""
        pool = cls(path=Path(path))
        if pool.path.exists():
            pool._df = pd.read_parquet(pool.path)
            pool._validate_schema(pool._df)
        else:
            pool._df = pool._empty_df()
        return pool

    # ---------- 内部辅助 ----------

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        """返回空 pool 的 DataFrame (列与 Universe schema 对齐)."""
        return pd.DataFrame(
            {
                "stock_index": pd.Series(dtype="int32"),
                "stock_code": pd.Series(dtype="object"),
                "ipo_date": pd.Series(dtype="object"),
            }
        )

    @staticmethod
    def _validate_schema(df: pd.DataFrame) -> None:
        required = {"stock_index", "stock_code", "ipo_date"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Universe.parquet 缺少字段: {missing}")

    def _next_index(self) -> int:
        """下一个可分配的 stock_index (单调递增, 不回收)."""
        if self._df.empty:
            return 0
        return int(self._df["stock_index"].max()) + 1

    def _save(self) -> None:
        """落盘 Universe.parquet (走 DataFile schema 校验路径).

        StockPool.load() 接收的是完整文件路径 (e.g. ``data/Universe.parquet``),
        与 DataFile 的 root/storage_path 拆分约定不同. 用 ``dc_replace`` 构造一个
        storage_path 只含文件名的 DataType, 使 DataFile 写到 self.path.
        """
        from dataclasses import replace as dc_replace

        dtype = REGISTRY.get("Universe")
        custom_dtype = dc_replace(dtype, storage_path=Path(self.path.name))
        file_ = DataFile(custom_dtype, root=self.path.parent)
        file_.truncate()
        if not self._df.empty:
            file_.append(self._df)

    # ---------- 公开 API ----------

    def add(
        self,
        stock_code: str,
        source: str = "manual",
        ipo_date: str | None = None,
        note: str = "",
    ) -> int:
        """添加单只股票. 返回分配的 stock_index.

        幂等: stock_code 已存在则返回现有 stock_index, 不重复添加.

        Args:
            stock_code: 9 字符 stock_code (e.g. "600000.SH")
            source: 来源标记 (保留参数, 不写入 Universe, 仅兼容 legacy 接口)
            ipo_date: ISO string "YYYY-MM-DD", 默认 "1990-01-01" (老股回填)
            note: 保留参数, 同上
        """
        if len(stock_code) != 9 or stock_code[6] != "." or stock_code[7:] not in ("SH", "SZ"):
            raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ, 得到 {stock_code!r}")

        # 幂等
        existing = self._df[self._df["stock_code"] == stock_code]
        if not existing.empty:
            return int(existing.iloc[0]["stock_index"])

        new_idx = self._next_index()
        new_row = pd.DataFrame(
            [
                {
                    "stock_index": new_idx,
                    "stock_code": stock_code,
                    "ipo_date": ipo_date or "1990-01-01",
                }
            ]
        )
        self._df = pd.concat([self._df, new_row], ignore_index=True)
        self._save()
        return new_idx

    def idx_of(self, stock_code: str) -> int | None:
        """查 stock_code 对应的 stock_index, 不存在返回 None."""
        if self._df.empty:
            return None
        match = self._df[self._df["stock_code"] == stock_code]
        if match.empty:
            return None
        return int(match.iloc[0]["stock_index"])

    def stock_of(self, idx: int) -> str | None:
        """查 stock_index 对应的 stock_code, 不存在返回 None."""
        if self._df.empty:
            return None
        match = self._df[self._df["stock_index"] == idx]
        if match.empty:
            return None
        return str(match.iloc[0]["stock_code"])

    def size(self) -> int:
        """当前池子大小 (含历史, 因为 stock_index 不回收)."""
        return len(self._df)

    def active_count(self, asof: date | None = None) -> int:
        """当期活跃数量 (默认今天)."""
        mask = self.active_mask(asof)
        return int(mask.sum())

    def active_mask(self, asof: date | None = None) -> np.ndarray:
        """当期活跃 mask, ndarray(N,) bool. N = size().

        活跃定义: asof 当天及之前已上市 (ipo_date <= asof).
        """
        if self._df.empty:
            return np.array([], dtype=bool)
        if asof is None:
            asof_str = date.today().isoformat()
        else:
            asof_str = asof.isoformat()
        mask_series = self._df["ipo_date"].astype(str) <= asof_str
        # 按 stock_index 排序, 保证返回顺序稳定
        return (
            mask_series.sort_index()
            .to_numpy(dtype=bool)
        )

    def export(self) -> pd.DataFrame:
        """导出整张表 (只读副本)."""
        return self._df.copy()

    @property
    def stock_ids(self) -> list[str]:
        """按 stock_index 升序的 stock_code 列表."""
        if self._df.empty:
            return []
        return (
            self._df.sort_values("stock_index")["stock_code"].tolist()
        )

    # ---------- universe_sha (供 alpha_store / cache 校验) ----------

    def universe_sha(self) -> str:
        """基于 stock_code 排序的列表算 sha256 (前 16 位)."""
        codes = sorted(self.stock_ids)
        payload = "|".join(codes).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


__all__ = ["StockPool"]