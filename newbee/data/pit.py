"""Point-in-Time (PIT) 工具: 财务数据的披露日语义.

核心问题:
  财务报告有 "报告期" (end_date) 和 "披露日" (ann_date) 两个时间维度.
  回测中只能在 ann_date <= asof 时才能看到该期财报, 否则是 look-ahead bias.

API:
  PITStore
    - add(stock_id, end_date, ann_date, field, value)
    - get_value(field, stock_id, asof, fallback='none'|'latest') -> value | None
    - get_series(field, stock_id, asof_start, asof_end) -> Series[asof -> value]
    - save() / load()        # parquet 持久化

字段:
  - end_date: 报告期 (如 2023-12-31)
  - ann_date: 披露日 (如 2024-03-25)
  - 检索: 给定 (field, stock_id, asof), 返回 ann_date <= asof 的最近一期值
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

import fcntl
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ---------- 常量 ----------

DEFAULT_PIT_PATH = Path("data/pit/financials.parquet")
DEFAULT_MANIFEST_PATH = Path("data/pit/manifest.json")

# 表 schema (locked)
_PIT_DTYPE = {
    "stock_id": pd.StringDtype(),
    "end_date": "datetime64[ns]",
    "ann_date": "datetime64[ns]",
    "field": pd.StringDtype(),
    "value": "float64",
}


# ---------- 锁 ----------


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()


# ---------- 异常 ----------


class PITError(Exception):
    """PIT 层基异常."""


class FieldNotFoundError(PITError):
    """field 不在 PIT 存储中."""


# ---------- 主类 ----------


@dataclass
class PITStore:
    """Point-in-Time 财务数据存储.

    Attributes:
        path: financials.parquet 路径
        manifest_path: manifest.json 路径
    """

    path: Path = DEFAULT_PIT_PATH
    manifest_path: Path = DEFAULT_MANIFEST_PATH

    _df: pd.DataFrame = field(default_factory=pd.DataFrame, init=False, repr=False)

    def __post_init__(self):
        # 确保 _df 有正确 schema (空 df 默认无列, 加上 schema)
        if len(self._df.columns) == 0:
            self._df = self._empty_df()

    # ---------- 工厂 ----------

    @classmethod
    def load(cls, path: Path = DEFAULT_PIT_PATH) -> "PITStore":
        store = cls(path=path, manifest_path=path.parent / "manifest.json")
        if path.exists():
            store._df = pd.read_parquet(path)
            cls._validate_schema(store._df)
        else:
            store._df = cls._empty_df()
        return store

    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame({col: pd.Series(dtype=dt) for col, dt in _PIT_DTYPE.items()})

    @staticmethod
    def _validate_schema(df: pd.DataFrame) -> None:
        missing = set(_PIT_DTYPE.keys()) - set(df.columns)
        if missing:
            raise ValueError(f"PIT parquet 缺少字段: {missing}")

    # ---------- 内部 ----------

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(self._df, preserve_index=False)
        pq.write_table(table, self.path, compression="snappy")

        manifest = {
            "count": len(self._df),
            "stocks": int(self._df["stock_id"].nunique()) if not self._df.empty else 0,
            "fields": sorted(self._df["field"].unique().tolist()) if not self._df.empty else [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sha": self._sha(),
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    def _sha(self) -> str:
        if not self.path.exists():
            return "empty"
        h = hashlib.sha256()
        with open(self.path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    # ---------- 写入 ----------

    def add(
        self,
        stock_id: str,
        end_date: date,
        ann_date: date,
        field: str,
        value: float,
    ) -> None:
        """加一条 (stock_id, end_date, ann_date, field, value).

        幂等: 同 (stock_id, end_date, ann_date, field) 才覆盖 value (避免重复写入).
        不同 ann_date 是不同披露 (支持重述 / 数据修订).
        """
        with _file_lock(self.path):
            if self.path.exists():
                self._df = pd.read_parquet(self.path)
                self._validate_schema(self._df)
            else:
                self._df = self._empty_df()

            mask = (
                (self._df["stock_id"] == stock_id)
                & (self._df["end_date"] == pd.Timestamp(end_date))
                & (self._df["ann_date"] == pd.Timestamp(ann_date))
                & (self._df["field"] == field)
            )
            if mask.any():
                self._df.loc[mask, "value"] = value
            else:
                row = pd.DataFrame(
                    [
                        {
                            "stock_id": stock_id,
                            "end_date": pd.Timestamp(end_date),
                            "ann_date": pd.Timestamp(ann_date),
                            "field": field,
                            "value": float(value),
                        }
                    ]
                )
                for col, dt in _PIT_DTYPE.items():
                    row[col] = row[col].astype(dt)
                self._df = pd.concat([self._df, row], ignore_index=True)
            self._save()

    def add_batch(self, df: pd.DataFrame) -> None:
        """批量写入, df 必须含 stock_id / end_date / ann_date / field / value."""
        required = {"stock_id", "end_date", "ann_date", "field", "value"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"批量写入缺字段: {missing}")
        with _file_lock(self.path):
            if self.path.exists():
                self._df = pd.read_parquet(self.path)
                self._validate_schema(self._df)
            else:
                self._df = self._empty_df()
            new = df.copy()
            new["stock_id"] = new["stock_id"].astype(str).str.zfill(6)
            new["end_date"] = pd.to_datetime(new["end_date"])
            new["ann_date"] = pd.to_datetime(new["ann_date"])
            new["field"] = new["field"].astype(str)
            new["value"] = pd.to_numeric(new["value"], errors="coerce").astype("float64")
            for col, dt in _PIT_DTYPE.items():
                new[col] = new[col].astype(dt)
            # 删同 key 的旧行
            keys = list(zip(new["stock_id"], new["end_date"], new["field"]))
            existing_keys = set(
                zip(self._df["stock_id"], self._df["end_date"], self._df["field"])
            )
            keep_mask = [k not in existing_keys for k in keys]
            new_only = new[pd.Series(keep_mask, index=new.index).values]
            self._df = pd.concat([self._df, new_only], ignore_index=True)
            self._save()

    # ---------- 读取 ----------

    def get_value(
        self,
        field: str,
        stock_id: str,
        asof: date,
        *,
        fallback: Literal["none", "latest"] = "none",
    ) -> float | None:
        """取 asof 时点能看到的最新一期 value.

        严格语义: 只取 ann_date <= asof 的最近一期 (end_date 最大的优先, 同一 end_date
        选 ann_date 最大的, 因为可能有重述).

        Args:
            field: 财务字段名 (e.g. 'pe' / 'roe' / 'revenue')
            stock_id: 6 位股票代码
            asof: 查询时点 (通常为回测当日)
            fallback:
                'none' (默认): 没有披露返回 None
                'latest': 没有披露返回所有已知数据中的最近一期 (用于 offline 分析)
        """
        sub = self._df[
            (self._df["stock_id"] == stock_id) & (self._df["field"] == field)
        ]
        if sub.empty:
            return None
        asof_ts = pd.Timestamp(asof)
        disclosed = sub[sub["ann_date"] <= asof_ts]
        if disclosed.empty:
            if fallback == "latest":
                # 退化: 取所有数据中 ann_date 最大的
                row = sub.sort_values("ann_date", ascending=False).iloc[0]
                return float(row["value"])
            return None
        # 先按 end_date desc, 再按 ann_date desc 取第一条 (重述优先)
        row = disclosed.sort_values(
            ["end_date", "ann_date"], ascending=[False, False]
        ).iloc[0]
        return float(row["value"])

    def get_series(
        self,
        field: str,
        stock_id: str,
        asof_start: date,
        asof_end: date,
    ) -> pd.Series:
        """返回 asof 区间内每个交易日 (asof_start..asof_end) 的当前可见 value.

        这是回测常用的 "step function" — value 在 ann_date 之前保持上一期, 在 ann_date
        当日跳跃到新值.

        注意: 输出索引是 asof 区间内每个日期 (含周末, 非交易日也填), 上层按交易日历裁剪.
        """
        sub = self._df[
            (self._df["stock_id"] == stock_id) & (self._df["field"] == field)
        ]
        if sub.empty:
            return pd.Series(dtype="float64")
        # 构造披露日 -> value 的 step
        sub = sub.sort_values("ann_date")
        idx = pd.date_range(pd.Timestamp(asof_start), pd.Timestamp(asof_end), freq="D")
        s = pd.Series(index=idx, dtype="float64")
        for d in idx:
            v = self.get_value(field, stock_id, d.date())
            if v is not None:
                s.loc[d] = v
        return s

    def history(
        self,
        field: str,
        stock_id: str,
    ) -> pd.DataFrame:
        """返回该字段该股票的所有历史披露 (end_date, ann_date, value), 按 ann_date 排序."""
        sub = self._df[
            (self._df["stock_id"] == stock_id) & (self._df["field"] == field)
        ].sort_values("ann_date")
        return sub[["end_date", "ann_date", "value"]].reset_index(drop=True)

    def export(self) -> pd.DataFrame:
        """导出整张表 (副本)."""
        return self._df.copy()


# ---------- 高层 helper: 与 akshare 衔接 ----------


def normalize_akshare_financial(
    df: pd.DataFrame, *, field_map: dict[str, str] | None = None
) -> pd.DataFrame:
    """把 akshare 财务数据归一化成 PIT 写入格式.

    akshare (财务摘要) 字段:
        SECUCODE / SECURITY_CODE / SECURITY_NAME_ABBR / REPORT_DATE /
        REPORT_TYPE / REPORT_DATE_NAME / NOTICE_DATE / ...

    标准化输出列: stock_id / end_date / ann_date / field / value (long format)
    """
    if field_map is None:
        field_map = {}

    rename = {
        "SECURITY_CODE": "stock_id",
        "REPORT_DATE": "end_date",
        "NOTICE_DATE": "ann_date",
        # 财务字段映射由 field_map 提供
    }
    df = df.rename(columns=rename)

    if "stock_id" not in df.columns or "end_date" not in df.columns or "ann_date" not in df.columns:
        raise ValueError(
            f"akshare 财务数据缺少必填字段. 当前列: {df.columns.tolist()}"
        )

    df["stock_id"] = df["stock_id"].astype(str).str.zfill(6)
    df["end_date"] = pd.to_datetime(df["end_date"])
    df["ann_date"] = pd.to_datetime(df["ann_date"])

    # 长格式展开
    out_rows = []
    for field_name, src_col in field_map.items():
        if src_col not in df.columns:
            continue
        for _, row in df.iterrows():
            v = row[src_col]
            if pd.isna(v):
                continue
            try:
                v_float = float(v)
            except (ValueError, TypeError):
                continue
            out_rows.append(
                {
                    "stock_id": row["stock_id"],
                    "end_date": row["end_date"],
                    "ann_date": row["ann_date"],
                    "field": field_name,
                    "value": v_float,
                }
            )
    return pd.DataFrame(out_rows)