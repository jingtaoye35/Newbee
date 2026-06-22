"""StateTracker: datas/_Manifest/Data_State.json 读写.

设计:
  - 单一文件 datas/_Manifest/Data_State.json
  - 结构: { version, universe_sha, types: {<TypeName>: {...}}, updated_at }
  - 原子写: tempfile + os.replace
  - fcntl 文件锁防并发 (单进程内, 跨进程也安全)
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from utils.errors import SchemaVersionError, StateCorruptedError
from datasource.storage.io import CoverageStats

# ---------- 路径常量 ----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MANIFEST_DIR_NAME = "_Manifest"
STATE_FILE_NAME = "Data_State.json"
# 向后兼容: module 导入时即刻求值的 fallback (<repo>/datas/_Manifest/...).
# 实际运行时调用 ``default_state_path()`` 才会读 config.paths.datasource_dir.
DEFAULT_STATE_PATH = PROJECT_ROOT / "datas" / MANIFEST_DIR_NAME / STATE_FILE_NAME

STATE_VERSION = "1.0"
DEFAULT_RESUME_START = "2020-01-01"  # state 缺失时的 fallback 起点


def default_state_path() -> Path:
    """运行时默认 Data_State.json 路径: <data_root>/_Manifest/Data_State.json.

    data_root 与 DataFile 一致 (config.paths.datasource_dir → <repo> fallback),
    保证 state 与 data 始终在同一目录下. 与 io.py.default_datasource_dir() 共享根.
    """
    from datasource.storage.io import default_datasource_dir

    return default_datasource_dir() / MANIFEST_DIR_NAME / STATE_FILE_NAME


# ---------- dataclass ----------


@dataclass
class DataTypeState:
    """单类型的 state 条目."""

    schema_version: str
    frequency: str
    first_date: Optional[str]
    last_date: Optional[str]
    row_count: int
    stock_count: int
    updated_at: str

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "DataTypeState":
        return cls(
            schema_version=str(d.get("schema_version", "")),
            frequency=str(d.get("frequency", "")),
            first_date=d.get("first_date") if isinstance(d.get("first_date"), str) else None,
            last_date=d.get("last_date") if isinstance(d.get("last_date"), str) else None,
            row_count=int(d.get("row_count", 0)),
            stock_count=int(d.get("stock_count", 0)),
            updated_at=str(d.get("updated_at", "")),
        )


# ---------- 文件锁 ----------


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """fcntl 文件锁. 仅在 Unix 有效 (Windows 需 msvcrt)."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


# ---------- StateTracker ----------


class StateTracker:
    """Data_State.json 的读写器."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self.path: Path = state_path if state_path is not None else default_state_path()

    # ---------- read ----------

    def read(self) -> dict[str, DataTypeState]:
        """读 state. 文件不存在返回空 dict."""
        if not self.path.exists():
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                payload = json.load(f)
        except json.JSONDecodeError as e:
            raise StateCorruptedError(f"Data_State.json 解析失败: {e}") from e
        types_raw = payload.get("types", {})
        out: dict[str, DataTypeState] = {}
        if isinstance(types_raw, dict):
            for name, entry in types_raw.items():
                if isinstance(entry, dict):
                    out[str(name)] = DataTypeState.from_dict(entry)
        return out

    def read_full(self) -> dict[str, object]:
        """读完整 JSON (含 version / universe_sha / updated_at). 文件不存在返回默认."""
        if not self.path.exists():
            return {
                "version": STATE_VERSION,
                "universe_sha": None,
                "types": {},
                "updated_at": "",
            }
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    # ---------- update ----------

    def update(
        self,
        type_name: str,
        stats: CoverageStats,
        *,
        universe_sha: Optional[str] = None,
    ) -> DataTypeState:
        """原子更新单类型条目. 拒绝 schema_version 倒退.

        Raises:
            SchemaVersionError: 新 version < 旧 version.
        """
        with _file_lock(self.path):
            current = self.read_full()
            types = current.get("types", {})
            if not isinstance(types, dict):
                types = {}

            existing = types.get(type_name)
            if existing and isinstance(existing, dict):
                old_ver = str(existing.get("schema_version", ""))
                if old_ver and _compare_semver(stats.schema_version, old_ver) < 0:
                    raise SchemaVersionError(type_name, disk=old_ver, code=stats.schema_version)

            new_entry = {
                "schema_version": stats.schema_version,
                "frequency": stats.frequency,
                "first_date": stats.first_date,
                "last_date": stats.last_date,
                "row_count": stats.row_count,
                "stock_count": stats.stock_count,
                "updated_at": stats.updated_at,
            }
            types[type_name] = new_entry
            current["types"] = types
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            if universe_sha is not None:
                current["universe_sha"] = universe_sha
            self._atomic_write(current)
            return DataTypeState.from_dict(new_entry)

    def delete(self, type_name: str) -> bool:
        """原子删除单类型条目. 用于 truncate 配套: 文件被清空后 state 也应清空,
        避免后续 resume_range 读到「last_date 旧值」误判 up-to-date.

        Returns:
            True if entry existed and was removed; False if entry was absent.
        """
        with _file_lock(self.path):
            current = self.read_full()
            types = current.get("types", {})
            if not isinstance(types, dict) or type_name not in types:
                return False
            types.pop(type_name, None)
            current["types"] = types
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._atomic_write(current)
            return True

    # ---------- resume_range ----------

    def resume_range(self, type_name: str, *, latest: str) -> tuple[str, str]:
        """计算 [start, end] 区间. 已 up-to-date 时 start > end."""
        states = self.read()
        entry = states.get(type_name)
        if entry is None:
            return (DEFAULT_RESUME_START, latest)

        last = entry.last_date
        if last is None:
            return (DEFAULT_RESUME_START, latest)
        if last > latest:
            # up-to-date → start = latest+1, end = latest (start > end)
            # 注: 严格 `>` (非 `>=`) 让 last == latest 时仍重跑 today, 适用于
            # adj_factor 这类「日频但当日全量便宜」的数据 (与 financial quarterly
            # 类的保守跳过相反, 见 service/stock_basic_data.daily_update).
            from datetime import timedelta

            next_day = (date.fromisoformat(latest) + timedelta(days=1)).isoformat()
            return (next_day, latest)
        # 否则: start = last+1
        from datetime import timedelta

        next_day = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
        return (next_day, latest)

    # ---------- universe_sha ----------

    def is_universe_stale(self, current_sha: Optional[str]) -> bool:
        """state.universe_sha 与 current_sha 不一致 → True."""
        if current_sha is None:
            return False
        full = self.read_full()
        cached = full.get("universe_sha")
        if cached is None:
            return False
        return cached != current_sha

    def get_universe_sha(self) -> str | None:
        full = self.read_full()
        v = full.get("universe_sha")
        return str(v) if v is not None else None

    # ---------- write helper ----------

    def _atomic_write(self, payload: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".data_state_", suffix=".json.tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise


def _compare_semver(a: str, b: str) -> int:
    """简单 semver 比较: a < b 返回 -1, a == b 返回 0, a > b 返回 1."""

    def parse(v: str) -> tuple[int, ...]:
        parts: list[int] = []
        for chunk in v.split("."):
            try:
                parts.append(int(chunk))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    pa_, pb_ = parse(a), parse(b)
    if pa_ < pb_:
        return -1
    if pa_ > pb_:
        return 1
    return 0


__all__ = [
    "DataTypeState",
    "StateTracker",
    "DEFAULT_STATE_PATH",
    "DEFAULT_RESUME_START",
    "STATE_VERSION",
]
