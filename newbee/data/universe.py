"""自建股票池 (StockPool).

核心理念:
  - 真理是"我池子里的股票", 不是"指数成分股"
  - 单一表 (parquet) append-only
  - idx 单调递增, 永不回收 (即使股票 retired)

API:
  - add(stock_id, source, added_at=None, note="")        # 单只添加
  - add_index(name, stock_ids, backdate_to=None)         # 批量添加
  - remove(stock_id, retire_at, note="")                 # 标记退休
  - active_mask(asof) -> ndarray[bool] (N,)              # 当期活跃 mask
  - idx_of(stock_id) -> int | None                       # 查 idx
  - stock_of(idx) -> str | None                          # 查 stock_id
  - size() -> int                                        # 当前池子大小
  - export() -> pd.DataFrame                             # 整张表
  - save() / load()                                      # 持久化
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ---------- 常量 ----------

DEFAULT_POOL_PATH = Path("data/universe/pool.parquet")
DEFAULT_MANIFEST_PATH = Path("data/universe/manifest.json")

# 字段类型 (parquet schema 锁定)
_DTYPE = {
    "idx": pd.Int64Dtype(),
    "stock_id": pd.StringDtype(),
    "added_at": "datetime64[ns]",
    "source": pd.StringDtype(),
    "status": pd.StringDtype(),
    "retire_dt": "datetime64[ns]",   # nullable
    "note": pd.StringDtype(),
}
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"


# ---------- 文件锁 (单机进程安全, 防并发读写损坏 parquet) ----------


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """简单的文件锁 (fcntl). 仅在 Unix 生效, Windows 需 msvcrt."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()


# ---------- 主类 ----------


@dataclass
class StockPool:
    """自建股票池 (append-only, idx 永增).

    Attributes:
        path: pool.parquet 的路径
        manifest_path: manifest.json 的路径
    """

    path: Path = DEFAULT_POOL_PATH
    manifest_path: Path = DEFAULT_MANIFEST_PATH

    # ---------- 工厂方法 ----------

    @classmethod
    def load(cls, path: Path = DEFAULT_POOL_PATH) -> "StockPool":
        """加载现有池子 (不存在则创建空池)."""
        pool = cls(path=path, manifest_path=path.parent / "manifest.json")
        if not path.exists():
            pool._df = pool._empty_df()
        else:
            pool._df = pd.read_parquet(path)
            pool._validate_schema(pool._df)
        return pool

    # ---------- 内部辅助 ----------

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            {col: pd.Series(dtype=dt) for col, dt in _DTYPE.items()}
        )

    @staticmethod
    def _validate_schema(df: pd.DataFrame) -> None:
        missing = set(_DTYPE.keys()) - set(df.columns)
        if missing:
            raise ValueError(f"pool.parquet 缺少字段: {missing}")

    def _next_idx(self) -> int:
        """下一个可分配的 idx (单调递增, 不回收)."""
        if self._df.empty:
            return 0
        return int(self._df["idx"].max()) + 1

    def _save(self) -> None:
        """落盘 parquet + 更新 manifest (在锁内)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 显式指定 schema, 防止 nullable 字段类型漂移
        table = pa.Table.from_pandas(self._df, preserve_index=False)
        pq.write_table(table, self.path, compression="snappy")

        # 写 manifest
        sha = self._compute_sha()
        manifest = {
            "current_sha": sha,
            "count": len(self._df),
            "active_count": int((self._df["status"] == STATUS_ACTIVE).sum()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def _compute_sha(self) -> str:
        """基于 parquet 内容计算 sha256 (manifest 校验用)."""
        if not self.path.exists():
            return "empty"
        h = hashlib.sha256()
        with open(self.path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    # ---------- 公开 API ----------

    def add(
        self,
        stock_id: str,
        source: str,
        added_at: date | None = None,
        note: str = "",
    ) -> int:
        """添加单只股票到池子. 返回分配的 idx.

        幂等: 若 stock_id 已存在, 返回现有 idx, 不重复添加.

        Args:
            stock_id: 6 位股票代码 (如 "600000")
            source: 来源标记 (如 "csi1000" / "manual" / "csi2000")
            added_at: 加入日期, 默认今天
            note: 备注
        """
        with _file_lock(self.path):
            if self.path.exists():
                self._df = pd.read_parquet(self.path)
                self._validate_schema(self._df)

            # 幂等: 已存在则直接返回
            existing = self._df[self._df["stock_id"] == stock_id]
            if not existing.empty:
                return int(existing.iloc[0]["idx"])

            if added_at is None:
                added_at = date.today()
            if not isinstance(added_at, pd.Timestamp):
                added_at_ts = pd.Timestamp(added_at)
            else:
                added_at_ts = added_at

            new_idx = self._next_idx()
            new_row = pd.DataFrame(
                [
                    {
                        "idx": new_idx,
                        "stock_id": stock_id,
                        "added_at": added_at_ts,
                        "source": source,
                        "status": STATUS_ACTIVE,
                        "retire_dt": pd.NaT,
                        "note": note,
                    }
                ]
            )
            # 统一 dtype, 避免 concat 报警
            for col, dt in _DTYPE.items():
                new_row[col] = new_row[col].astype(dt)

            self._df = pd.concat([self._df, new_row], ignore_index=True)
            self._save()
            return new_idx

    def add_index(
        self,
        name: str,
        stock_ids: list[str],
        backdate_to: date | None = None,
    ) -> list[int]:
        """批量从指数添加股票.

        Args:
            name: 指数名 (如 "csi1000")
            stock_ids: 成分股 stock_id 列表
            backdate_to: 若指定, 所有股票的 added_at 都设为该日期

        Returns:
            分配的 idx 列表 (按输入顺序)
        """
        with _file_lock(self.path):
            if self.path.exists():
                self._df = pd.read_parquet(self.path)
                self._validate_schema(self._df)

            existing_ids = set(self._df["stock_id"].tolist())
            new_ids = [sid for sid in stock_ids if sid not in existing_ids]

            if not new_ids:
                return [
                    int(self._df[self._df["stock_id"] == sid].iloc[0]["idx"])
                    for sid in stock_ids
                ]

            if backdate_to is None:
                backdate_to = date.today()
            added_at_ts = pd.Timestamp(backdate_to)

            start_idx = self._next_idx()
            new_rows = pd.DataFrame(
                {
                    "idx": range(start_idx, start_idx + len(new_ids)),
                    "stock_id": new_ids,
                    "added_at": added_at_ts,
                    "source": name,
                    "status": STATUS_ACTIVE,
                    "retire_dt": pd.NaT,
                    "note": "",
                }
            )
            for col, dt in _DTYPE.items():
                new_rows[col] = new_rows[col].astype(dt)

            self._df = pd.concat([self._df, new_rows], ignore_index=True)
            self._save()

            # 返回所有 (含已存在) 的 idx, 按输入顺序
            id_to_idx = dict(
                zip(self._df["stock_id"].tolist(), self._df["idx"].tolist())
            )
            return [int(id_to_idx[sid]) for sid in stock_ids]

    def remove(
        self,
        stock_id: str,
        retire_at: date,
        note: str = "",
    ) -> None:
        """标记股票为 retired (idx 不回收).

        Raises:
            KeyError: stock_id 不在池子中
        """
        with _file_lock(self.path):
            if self.path.exists():
                self._df = pd.read_parquet(self.path)
                self._validate_schema(self._df)

            mask = self._df["stock_id"] == stock_id
            if not mask.any():
                raise KeyError(f"stock_id {stock_id} 不在池子中")

            retire_ts = pd.Timestamp(retire_at)
            self._df.loc[mask, "status"] = STATUS_RETIRED
            self._df.loc[mask, "retire_dt"] = retire_ts
            if note:
                self._df.loc[mask, "note"] = note
            self._save()

    def idx_of(self, stock_id: str) -> int | None:
        """查 stock_id 对应的 idx, 不存在返回 None."""
        if self._df.empty:
            return None
        match = self._df[self._df["stock_id"] == stock_id]
        if match.empty:
            return None
        return int(match.iloc[0]["idx"])

    def stock_of(self, idx: int) -> str | None:
        """查 idx 对应的 stock_id, 不存在返回 None."""
        if self._df.empty:
            return None
        match = self._df[self._df["idx"] == idx]
        if match.empty:
            return None
        return str(match.iloc[0]["stock_id"])

    def size(self) -> int:
        """当前池子大小 (含 retired, 因为 idx 不回收)."""
        return len(self._df)

    def active_count(self, asof: date | None = None) -> int:
        """当期活跃数量 (默认今天)."""
        mask = self.active_mask(asof)
        return int(mask.sum())

    def active_mask(self, asof: date | None = None) -> np.ndarray:
        """当期活跃 mask, ndarray(N,) bool. N = size().

        活跃定义: added_at <= asof AND (status == active OR (retired AND retire_dt > asof))
        """
        if self._df.empty:
            return np.array([], dtype=bool)

        if asof is None:
            asof_ts = pd.Timestamp(date.today())
        else:
            asof_ts = pd.Timestamp(asof)

        # 满足 added_at <= asof
        cond_added = self._df["added_at"] <= asof_ts
        # active: 永远计入; retired: 只在 retire_dt > asof 之前计入
        cond_active = self._df["status"] == STATUS_ACTIVE
        cond_retired = (self._df["status"] == STATUS_RETIRED) & (
            self._df["retire_dt"].fillna(pd.Timestamp.max) > asof_ts
        )
        cond_status = cond_active | cond_retired

        mask_series = (cond_added & cond_status).fillna(False)
        # 按 idx 排序确保 mask 顺序稳定
        return mask_series.sort_index().to_numpy(dtype=bool)

    def export(self) -> pd.DataFrame:
        """导出整张表 (只读副本)."""
        return self._df.copy()
