"""数据层异常层级.

所有异常继承自 `DataSourceError`, 业务代码可统一捕获.
"""
from __future__ import annotations

__all__ = [
    "DataSourceError",
    "StorageError",
    "SchemaVersionError",
    "SchemaValidationError",
    "PrimaryKeyConflictError",
    "ManifestMismatchError",
    "StateCorruptedError",
]


class DataSourceError(Exception):
    """数据层异常的根."""


class StorageError(DataSourceError):
    """存储层错误的基类 (兼容历史 alias)."""


class SchemaVersionError(StorageError):
    """磁盘 schema_version 与代码不一致."""

    def __init__(self, type_name: str, disk: str, code: str) -> None:
        self.type_name = type_name
        self.disk = disk
        self.code = code
        super().__init__(
            f"{type_name}: schema_version 不一致 (disk={disk}, code={code}). "
            f"请运行 `python -m alpha_backend.datasource.codegen` 或显式 bump 磁盘版本."
        )


class SchemaValidationError(StorageError):
    """数据行未通过 Pydantic 校验."""

    def __init__(self, type_name: str, message: str, row: object = None) -> None:
        self.type_name = type_name
        self.row = row
        super().__init__(f"{type_name}: schema 校验失败 — {message}")


class PrimaryKeyConflictError(StorageError):
    """append() 检测到主键冲突, 提示用 upsert()."""

    def __init__(self, type_name: str, key: object) -> None:
        self.type_name = type_name
        self.key = key
        super().__init__(
            f"{type_name}: 主键 {key!r} 已存在. 改用 upsert(conflict=...) 或先 truncate()."
        )


class ManifestMismatchError(StorageError):
    """npy cache 的 manifest 与当前 universe_sha 不一致."""


class StateCorruptedError(StorageError):
    """Data_State.json 解析失败或格式损坏."""