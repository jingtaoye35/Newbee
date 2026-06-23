"""DataFile: 单类型 parquet IO 门面.

封装:
  - read() with predicate pushdown (start/end/stock_codes/columns)
  - append() / upsert() with Pydantic validation
  - stats() returns CoverageStats
  - truncate() 重置文件
  - schema_version 一致性校验 (与 Data_State.json 比对)
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from newbee.datasource.registry import DataType
from newbee.datasource.storage.errors import (
    PrimaryKeyConflictError,
    SchemaValidationError,
    SchemaVersionError,
)

# ---------- 项目根 (data/ 在其下) ----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# ---------- CoverageStats ----------


@dataclass
class CoverageStats:
    """单文件的覆盖统计."""

    type_name: str
    schema_version: str
    frequency: str
    first_date: str | None
    last_date: str | None
    row_count: int
    stock_count: int
    file_size_bytes: int
    file_sha256: str  # 前 16 字符
    updated_at: str  # ISO timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------- 文件 SHA / size helpers ----------


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ---------- DataFile ----------


class DataFile:
    """单数据类型的物理文件门面."""

    def __init__(self, dtype: DataType, *, root: Path | None = None) -> None:
        self.dtype = dtype
        root = root if root is not None else PROJECT_ROOT
        self.path: Path = root / dtype.storage_path

    # ---------- 存在性 ----------

    def exists(self) -> bool:
        return self.path.exists()

    # ---------- 读 ----------

    def read(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        stock_codes: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """读 parquet, 过滤后返回 pandas.DataFrame.

        Raises:
            FileNotFoundError: 文件不存在.
            SchemaVersionError: Data_State.json 中的 schema_version 与 dtype 不一致.
            SchemaValidationError: 校验失败.
        """
        if not self.path.exists():
            raise FileNotFoundError(f"{self.dtype.name}: parquet 文件不存在: {self.path}")

        # 1. schema_version 校验
        self._assert_schema_fresh()

        # 2. 读 parquet
        table = pq.read_table(self.path, columns=columns)

        # 3. pyarrow filter pushdown (start/end/stock_codes)
        flt: list[Any] = []
        if start is not None:
            flt.append(pc.field("trading_date") >= start)
        if end is not None:
            flt.append(pc.field("trading_date") <= end)
        if stock_codes:
            flt.append(pc.field("stock_code").isin(stock_codes))
        if flt:
            combined = flt[0]
            for f in flt[1:]:
                combined = combined & f
            table = table.filter(combined)

        df = table.to_pandas()
        # 默认按 (trading_date, stock_code) 排序 (若列存在)
        sort_cols = [c for c in ("trading_date", "stock_code") if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols).reset_index(drop=True)

        # 4. 校验 Pydantic types (仅当读全字段时)
        # 读时只校验主键列 (trading_date / stock_code 等必须存在且合规)
        if columns is None:
            self._validate_rows(df)
        return df

    # ---------- 写 ----------

    def append(self, df: pd.DataFrame) -> int:
        """追加 rows. 拒绝主键冲突. 返回写入行数.

        Raises:
            SchemaValidationError: Pydantic 校验失败.
            PrimaryKeyConflictError: 与磁盘主键冲突.
        """
        if df is None or len(df) == 0:
            return 0
        # 校验
        self._validate_rows(df)

        existing = self._read_existing_for_conflict_check()
        if existing is not None and len(existing) > 0:
            existing_keys = set(self._make_key_tuples(existing))
            new_keys = set(self._make_key_tuples(df))
            overlap = existing_keys & new_keys
            if overlap:
                sample = sorted(overlap)[0]
                raise PrimaryKeyConflictError(self.dtype.name, sample)

        return self._write_atomic(df, append=True)

    def upsert(
        self,
        df: pd.DataFrame,
        conflict: str = "replace",
    ) -> int:
        """按主键 upsert. conflict: 'replace' / 'ignore' / 'error'."""
        if conflict not in ("replace", "ignore", "error"):
            raise ValueError(f"conflict 必须是 replace/ignore/error, 得到 {conflict!r}")
        if df is None or len(df) == 0:
            return 0
        self._validate_rows(df)

        existing = self._read_existing_for_conflict_check()
        if existing is None or len(existing) == 0:
            return self._write_atomic(df, append=False)

        existing_keys = set(self._make_key_tuples(existing))
        new_keys = set(self._make_key_tuples(df))
        overlap = existing_keys & new_keys

        if conflict == "error" and overlap:
            sample = sorted(overlap)[0]
            raise PrimaryKeyConflictError(self.dtype.name, sample)

        if conflict == "ignore":
            df = df[~df.apply(lambda r: self._row_key(r) in existing_keys, axis=1)]
            if len(df) == 0:
                return 0
            combined = pd.concat([existing, df], ignore_index=True)
        elif conflict == "replace":
            # 保留 existing 中不在 overlap 的行, 然后追加新 df (新 df 自然覆盖)
            existing_kept = existing[~existing.apply(
                lambda r: self._row_key(r) in overlap, axis=1
            )]
            combined = pd.concat([existing_kept, df], ignore_index=True)
        else:
            combined = pd.concat([existing, df], ignore_index=True)

        return self._write_atomic(combined, append=False)

    def truncate(self) -> None:
        """删除 parquet 文件."""
        if self.path.exists():
            self.path.unlink()

    # ---------- 统计 ----------

    def stats(self) -> CoverageStats:
        """返回 CoverageStats. 文件不存在则返回 zeroed stats."""
        now = datetime.now(timezone.utc).isoformat()
        if not self.path.exists():
            return CoverageStats(
                type_name=self.dtype.name,
                schema_version=self.dtype.schema_version,
                frequency=self.dtype.frequency,
                first_date=None,
                last_date=None,
                row_count=0,
                stock_count=0,
                file_size_bytes=0,
                file_sha256="missing",
                updated_at=now,
            )
        # 读 parquet metadata (zero-IO 模式)
        pf = pq.ParquetFile(self.path)
        schema = pf.schema_arrow
        n_rows = pf.metadata.num_rows

        first_date: str | None = None
        last_date: str | None = None
        stock_count = 0
        if "trading_date" in schema.names and n_rows > 0:
            # 一次性读 trading_date 列 (大文件也只一列, 快)
            tdf = pq.read_table(self.path, columns=["trading_date"])["trading_date"].to_pandas()
            if not tdf.empty:
                tdf = tdf.dropna()
                if not tdf.empty:
                    first_date = str(tdf.min())
                    last_date = str(tdf.max())
        if "stock_code" in schema.names and n_rows > 0:
            sdf = pq.read_table(self.path, columns=["stock_code"])["stock_code"].to_pandas()
            stock_count = int(sdf.dropna().nunique())
        return CoverageStats(
            type_name=self.dtype.name,
            schema_version=self.dtype.schema_version,
            frequency=self.dtype.frequency,
            first_date=first_date,
            last_date=last_date,
            row_count=int(n_rows),
            stock_count=stock_count,
            file_size_bytes=self.path.stat().st_size,
            file_sha256=_file_sha256(self.path),
            updated_at=now,
        )

    # ---------- helpers ----------

    def _assert_schema_fresh(self) -> None:
        """检查 Data_State.json 中本类型的 schema_version 是否与 dtype 一致."""
        from newbee.datasource.storage.state import DEFAULT_STATE_PATH, StateTracker

        # DataFile.path = root / dtype.storage_path; state 路径 = (data 目录)/_Manifest/Data_State.json
        # 例如: data/KData.parquet → data/_Manifest/Data_State.json
        state_path = self.path.parent / "_Manifest" / "Data_State.json"
        if not state_path.exists():
            state_path = DEFAULT_STATE_PATH
        tracker = StateTracker(state_path)
        state = tracker.read().get(self.dtype.name)
        if state is None:
            return  # 缺失 entry 视为 fresh (bootstrap 友好)
        if state.schema_version != self.dtype.schema_version:
            raise SchemaVersionError(
                self.dtype.name,
                disk=state.schema_version,
                code=self.dtype.schema_version,
            )

    def _validate_rows(self, df: pd.DataFrame) -> None:
        """校验 Pydantic. 只校验 df 中实际存在的字段 (允许只读部分列)."""
        Model = self.dtype.pydantic_model
        required_fields = set(Model.model_fields.keys())
        present_fields = set(df.columns) & required_fields
        errors: list[str] = []
        for i, row in df.iterrows():
            payload = {k: row[k] for k in present_fields}
            try:
                Model.model_validate(payload)
            except Exception as e:
                errors.append(f"row {i}: {e}")
                if len(errors) >= 5:
                    errors.append("... (more)")
                    break
        if errors:
            raise SchemaValidationError(self.dtype.name, "; ".join(errors))

    def _row_key(self, row: pd.Series) -> tuple[Any, ...]:
        return tuple(row[k] for k in self.dtype.primary_key)

    def _make_key_tuples(self, df: pd.DataFrame) -> list[tuple[Any, ...]]:
        return list(df[list(self.dtype.primary_key)].itertuples(index=False, name=None))

    def _read_existing_for_conflict_check(self) -> pd.DataFrame | None:
        if not self.path.exists():
            return None
        try:
            return pd.read_parquet(self.path, columns=list(self.dtype.primary_key))
        except Exception:
            return None

    def _write_atomic(self, df: pd.DataFrame, *, append: bool) -> int:
        """原子写 parquet. append=False 时直接覆盖; append=True 时若文件存在则抛错 (业务应调 append)."""
        if append and self.path.exists():
            raise PrimaryKeyConflictError(self.dtype.name, "<append on existing file>")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        n = len(df)
        table = pa.Table.from_pandas(df, preserve_index=False)
        # 写到同目录临时文件, os.replace 原子替换
        fd, tmp_path = tempfile.mkstemp(
            prefix=".parquet_", suffix=".tmp", dir=str(self.path.parent)
        )
        os.close(fd)
        try:
            pq.write_table(table, tmp_path, compression="snappy")
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise
        return n


__all__ = ["CoverageStats", "DataFile"]