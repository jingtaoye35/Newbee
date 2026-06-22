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

from pydantic import BaseModel

from utils.errors import (
    PrimaryKeyConflictError,
    SchemaValidationError,
    SchemaVersionError,
)

# ---------- 默认 root 解析 ----------

# io.py 文件位置 → src/datasource/storage/io.py
# parents[3] = repo root (即 src/ 的父目录). 当 config 未 load 时 fallback.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def default_datasource_dir() -> Path:
    """DataFile / Data_State.json 共享的数据根目录. 优先级:

    1. ``config.paths.datasource_dir`` — 与 backend 读取端一致 (生成与读同一个目录).
    2. ``_REPO_ROOT`` — fallback, 当 config 未初始化 (e.g. 离线脚本未走 load_config).

    公开, 让 state.py.default_state_path() 复用同一根, 保证 data files 与
    Data_State.json 始终在同一目录下. config 在此处 lazy import 避免循环.
    """
    try:
        from config import get_config

        configured = get_config().paths.datasource_dir
        if configured:
            return Path(configured)
    except Exception:
        pass
    return _REPO_ROOT / "datas/datasource"


# 向后兼容: 旧代码 ``PROJECT_ROOT`` 直接读取依然 OK (模块导入时即解析).
PROJECT_ROOT = default_datasource_dir()

# ---------- CoverageStats ----------


@dataclass
class CoverageStats:
    """单文件的覆盖统计."""

    type_name: str
    schema_version: str
    frequency: str
    first_date: Optional[str]
    last_date: Optional[str]
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

    def __init__(
        self,
        model: type[BaseModel],
        *,
        root: Optional[Path] = None,
        storage_path_override: Optional[Path] = None,
    ) -> None:
        self.dtype = model
        # 每次构造时重新解析 root (config 可能后续 load_config 改了).
        root = root if root is not None else default_datasource_dir()
        path_component = storage_path_override or Path(model.storage_path)
        self.path: Path = root / path_component
        self._is_csv: bool = model.format == "csv"

    # ---------- 存在性 ----------

    def exists(self) -> bool:
        return self.path.exists()

    # ---------- 读 ----------

    def read(
        self,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        stock_codes: Optional[List[str]] = None,
        columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """读 parquet 或 csv, 过滤后返回 pandas.DataFrame.

        parquet: pyarrow + filter pushdown (start/end/stock_codes).
        csv:     pd.read_csv + in-memory pandas filter (intended for small reference datas).

        Raises:
            FileNotFoundError: 文件不存在.
            SchemaVersionError: Data_State.json 中的 schema_version 与 dtype 不一致 (parquet only).
            SchemaValidationError: 校验失败.
        """
        if not self.path.exists():
            label = "csv" if self._is_csv else "parquet"
            raise FileNotFoundError(f"{self.dtype.type_name}: {label} 文件不存在: {self.path}")

        # 1. schema_version 校验 (parquet only; csv-backed types are reference datas,
        # schema_version 仍写入 Data_State.json 但 read 不强制)
        if not self._is_csv:
            self._assert_schema_fresh()

        if self._is_csv:
            # CSV 路径: 全量读 + pandas 内存过滤
            usecols = columns
            df = pd.read_csv(self.path, usecols=usecols) if usecols else pd.read_csv(self.path)
            if start is not None and "trade_date" in df.columns:
                df = df[df["trade_date"] >= start]
            if end is not None and "trade_date" in df.columns:
                df = df[df["trade_date"] <= end]
            if stock_codes and "stock_code" in df.columns:
                df = df[df["stock_code"].isin(stock_codes)]
        else:
            # parquet 路径: pyarrow filter pushdown
            table = pq.read_table(self.path, columns=columns)
            flt: list[Any] = []
            if start is not None:
                flt.append(pc.field("trade_date") >= start)
            if end is not None:
                flt.append(pc.field("trade_date") <= end)
            if stock_codes:
                flt.append(pc.field("stock_code").isin(stock_codes))
            if flt:
                combined = flt[0]
                for f in flt[1:]:
                    combined = combined & f
                table = table.filter(combined)
            df = table.to_pandas()

        # 默认按 (trade_date, stock_code) 排序 (若列存在)
        sort_cols = [c for c in ("trade_date", "stock_code") if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols).reset_index(drop=True)

        # 校验 Pydantic types (仅当读全字段时)
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
                raise PrimaryKeyConflictError(self.dtype.type_name, sample)

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
            raise PrimaryKeyConflictError(self.dtype.type_name, sample)

        # 向量化: 用 merge + indicator 一次性标记 existing / df 中主键命中行,
        # 取代 df.apply(..., axis=1) 的 Python 级逐行回调 (10⁶ 行级别慢几个数量级).
        pk = list(self.dtype.primary_key)
        if conflict == "ignore":
            # 仅保留 df 中不在 existing 主键集合的行
            existing_pk = existing[pk].drop_duplicates()
            merged = df.merge(existing_pk, on=pk, how="left", indicator=True)
            df = merged[merged["_merge"] == "left_only"].drop(columns="_merge")
            if len(df) == 0:
                return 0
            combined = pd.concat([existing, df], ignore_index=True)
        elif conflict == "replace":
            # 保留 existing 中主键不在 df 的行 (左连接 + left_only)
            new_pk = df[pk].drop_duplicates()
            merged = existing.merge(new_pk, on=pk, how="left", indicator=True)
            existing_kept = merged[merged["_merge"] == "left_only"].drop(columns="_merge")
            combined = pd.concat([existing_kept, df], ignore_index=True)
        else:
            combined = pd.concat([existing, df], ignore_index=True)

        return self._write_atomic(combined, append=False)

    def truncate(self) -> None:
        """删除物理文件 (parquet 或 csv)."""
        if self.path.exists():
            self.path.unlink()

    def write(self, df: pd.DataFrame) -> int:
        """无条件覆写整个文件 (含空 df: 仍创建带列头的骨架).

        与 upsert(conflict='replace') 的区别: 不读已有文件, 不做主键去重,
        直接整盘替换. 用于初始化空 schema skeleton 或全量重建场景.
        """
        if df is None:
            return 0
        self._validate_rows(df)
        return self._write_atomic(df, append=False)

    # ---------- 统计 ----------

    def stats(self) -> CoverageStats:
        """返回 CoverageStats. 文件不存在则返回 zeroed stats."""
        now = datetime.now(timezone.utc).isoformat()
        if not self.path.exists():
            return CoverageStats(
                type_name=self.dtype.type_name,
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
        if self._is_csv:
            # CSV: 全量读 + pandas 统计
            df = pd.read_csv(self.path)
            n_rows = int(len(df))
            first_date: Optional[str] = None
            last_date: Optional[str] = None
            stock_count = 0
            if "trade_date" in df.columns and n_rows > 0:
                tdf = df["trade_date"].dropna()
                if not tdf.empty:
                    first_date = str(tdf.min())
                    last_date = str(tdf.max())
            if "stock_code" in df.columns and n_rows > 0:
                stock_count = int(df["stock_code"].dropna().nunique())
        else:
            # parquet: metadata + 单列读
            pf = pq.ParquetFile(self.path)
            schema = pf.schema_arrow
            n_rows = pf.metadata.num_rows

            first_date = None
            last_date = None
            stock_count = 0
            if "trade_date" in schema.names and n_rows > 0:
                tdf = pq.read_table(self.path, columns=["trade_date"])["trade_date"].to_pandas()
                if not tdf.empty:
                    tdf = tdf.dropna()
                    if not tdf.empty:
                        first_date = str(tdf.min())
                        last_date = str(tdf.max())
            if "stock_code" in schema.names and n_rows > 0:
                sdf = pq.read_table(self.path, columns=["stock_code"])["stock_code"].to_pandas()
                stock_count = int(sdf.dropna().nunique())
        return CoverageStats(
            type_name=self.dtype.type_name,
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
        from datasource.storage.state import DEFAULT_STATE_PATH, StateTracker

        # DataFile.path = root / dtype.storage_path; state 路径 = (datas 目录)/_Manifest/Data_State.json
        # 例如: datas/Stock_KData.parquet → datas/_Manifest/Data_State.json
        state_path = self.path.parent / "_Manifest" / "Data_State.json"
        if not state_path.exists():
            state_path = DEFAULT_STATE_PATH
        tracker = StateTracker(state_path)
        state = tracker.read().get(self.dtype.type_name)
        if state is None:
            return  # 缺失 entry 视为 fresh (bootstrap 友好)
        if state.schema_version != self.dtype.schema_version:
            raise SchemaVersionError(
                self.dtype.type_name,
                disk=state.schema_version,
                code=self.dtype.schema_version,
            )

    def _validate_rows(self, df: pd.DataFrame) -> None:
        """校验 Pydantic. 只校验 df 中实际存在的字段 (允许只读部分列).

        不用 df.iterrows() — pandas 在 object 列里会把 None cell 升级成 float NaN
        (已知 iterrows 行为), 触发 Pydantic nullable=str 校验失败.
        改用 df.to_dict('records') 一次性序列化, 保留 None 语义.

        sanitize: 对所有 dtype 的 NaN 映回 None (PyArrow `null` 列读成 NaN,
        走 float64/object 时一样要处理). 这能防御从老 parquet 读出「列已 null
        类型」的脏数据再次写回时炸 Pydantic.
        """
        Model = self.dtype
        required_fields = set(Model.model_fields.keys())
        present_fields = list(set(df.columns) & required_fields)
        if not present_fields:
            return
        # 全 dtype sanitize: NaN → None, 覆盖 PyArrow null 列的 float64/object 表现.
        df_clean = df[present_fields].where(df.notna(), None)
        records = df_clean.to_dict(orient="records")
        errors: list[str] = []
        for i, payload in enumerate(records):
            try:
                Model.model_validate(payload)
            except Exception as e:
                errors.append(f"row {i}: {e}")
                if len(errors) >= 5:
                    errors.append("... (more)")
                    break
        if errors:
            raise SchemaValidationError(self.dtype.type_name, "; ".join(errors))

    def _row_key(self, row: pd.Series) -> tuple[Any, ...]:
        return tuple(row[k] for k in self.dtype.primary_key)

    def _make_key_tuples(self, df: pd.DataFrame) -> list[tuple[Any, ...]]:
        return list(df[list(self.dtype.primary_key)].itertuples(index=False, name=None))

    def _read_existing_for_conflict_check(self) -> pd.DataFrame | None:
        """读已有文件全量 rows. upsert 需要所有列以做最终 concat, 不能只读主键."""
        if not self.path.exists():
            return None
        try:
            if self._is_csv:
                # CSV: 读全量 (CSV-backed types are small by construction)
                return pd.read_csv(self.path)
            return pd.read_parquet(self.path)
        except Exception:
            return None

    def _write_atomic(self, df: pd.DataFrame, *, append: bool) -> int:
        """原子写 parquet 或 csv. append=False 时直接覆盖; append=True 时若文件存在则抛错 (业务应调 append)."""
        if append and self.path.exists():
            raise PrimaryKeyConflictError(self.dtype.type_name, "<append on existing file>")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        n = len(df)
        # 写到同目录临时文件, os.replace 原子替换
        prefix = ".csv_" if self._is_csv else ".parquet_"
        fd, tmp_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=str(self.path.parent))
        os.close(fd)
        try:
            if self._is_csv:
                df.to_csv(tmp_path, index=False)
            else:
                table = pa.Table.from_pandas(df, preserve_index=False)
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
