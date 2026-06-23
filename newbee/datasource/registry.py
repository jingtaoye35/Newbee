"""DataType 元数据 + DataRegistry 全局单例.

`DataType` 是单个数据类型的不可变描述 (frozen dataclass).
`DataRegistry` 是进程内全局单例, 业务代码通过 `REGISTRY.get(<name>)` 获取.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# ---------- Pascal_Snake_Case 校验 ----------

_PASCAL_SNAKE_RE = re.compile(r"^([A-Z][A-Za-z0-9]*)(_[A-Z][A-Za-z0-9]*)*$")


def _assert_pascal_snake_case(value: str, where: str) -> None:
    """Pascal_Snake_Case: 每个 token 首字母大写, 由 _ 分隔. 例: KData, Trade_Status, KData_M1."""
    if not _PASCAL_SNAKE_RE.match(value):
        raise ValueError(
            f"{where} 必须是 Pascal_Snake_Case (每个 token 首字母大写, _ 分隔), 得到 {value!r}"
        )


# ---------- DataType ----------


@dataclass(frozen=True)
class DataType:
    """单个数据类型的不可变元数据.

    Attributes:
        name: Pascal_Snake_Case 类型名 (e.g. "KData", "Trade_Status").
        schema_version: semver 字符串, 与 Data_State.json 中的 schema_version 一致时才能读取.
        frequency: 频率 ("daily" / "1min" / "5min" / "static").
        storage_path: parquet 物理路径, 相对于项目根.
        primary_key: 字段名 tuple, 用于 append/upsert 冲突检测.
        pydantic_model: 校验 / 反序列化的 BaseModel 类.
    """

    name: str
    schema_version: str
    frequency: str
    storage_path: Path
    primary_key: tuple[str, ...]
    pydantic_model: type[BaseModel]

    def __post_init__(self) -> None:
        _assert_pascal_snake_case(self.name, f"DataType.name")
        # storage_path 也必须是 Pascal_Snake_Case 文件名 (不带 .parquet)
        stem = self.storage_path.stem
        _assert_pascal_snake_case(stem, f"DataType.storage_path.stem")
        if not isinstance(self.primary_key, tuple):
            object.__setattr__(self, "primary_key", tuple(self.primary_key))

    @property
    def type_name(self) -> str:
        """alias for name, for ergonomic API."""
        return self.name


# ---------- DataRegistry ----------


class DataRegistry:
    """进程内全局 DataType 注册表.

    使用:
        from newbee.datasource.registry import REGISTRY
        kdata = REGISTRY.get("KData")
    """

    def __init__(self) -> None:
        self._types: dict[str, DataType] = {}

    def register(self, dtype: DataType) -> None:
        """注册 DataType. 重复注册同名 dtype 抛 ValueError."""
        if dtype.name in self._types:
            raise ValueError(
                f"DataType {dtype.name!r} 已注册 (现有: {self._types[dtype.name]!r})"
            )
        self._types[dtype.name] = dtype

    def get(self, name: str) -> DataType:
        """按 name 查 DataType. 不存在抛 KeyError (附带已注册列表)."""
        if name not in self._types:
            raise KeyError(
                f"未注册的 DataType {name!r}; 已注册: {sorted(self._types.keys())}"
            )
        return self._types[name]

    def all(self) -> list[DataType]:
        """所有已注册 DataType, 按 name 排序."""
        return sorted(self._types.values(), key=lambda d: d.name)

    def by_frequency(self, frequency: str) -> list[DataType]:
        """按 frequency 过滤, 返回匹配的 DataType 列表 (按 name 排序)."""
        return sorted(
            (d for d in self._types.values() if d.frequency == frequency),
            key=lambda d: d.name,
        )

    def __contains__(self, name: str) -> bool:
        return name in self._types

    def __len__(self) -> int:
        return len(self._types)

    def __repr__(self) -> str:
        return f"DataRegistry(types={sorted(self._types.keys())})"


# 模块级全局单例
REGISTRY = DataRegistry()


# ---------- 注册入口 ----------


def _register_defaults() -> None:
    """注册所有内置 DataType. 模块导入时调用一次."""
    from newbee.datasource.schemas import (
        AdjFactor,
        KData,
        TradeStatus,
        Universe,
    )

    # KData: daily, primary key (trading_date, stock_code)
    REGISTRY.register(
        DataType(
            name="KData",
            schema_version="1.0",
            frequency="daily",
            storage_path=Path("data/KData.parquet"),
            primary_key=("trading_date", "stock_code"),
            pydantic_model=KData,
        )
    )
    # Trade_Status: daily
    REGISTRY.register(
        DataType(
            name="Trade_Status",
            schema_version="1.0",
            frequency="daily",
            storage_path=Path("data/Trade_Status.parquet"),
            primary_key=("trading_date", "stock_code"),
            pydantic_model=TradeStatus,
        )
    )
    # Adj_Factor: daily
    REGISTRY.register(
        DataType(
            name="Adj_Factor",
            schema_version="1.0",
            frequency="daily",
            storage_path=Path("data/Adj_Factor.parquet"),
            primary_key=("trading_date", "stock_code"),
            pydantic_model=AdjFactor,
        )
    )
    # Universe: static
    REGISTRY.register(
        DataType(
            name="Universe",
            schema_version="1.0",
            frequency="static",
            storage_path=Path("data/Universe.parquet"),
            primary_key=("stock_index",),
            pydantic_model=Universe,
        )
    )


# 导入即注册 (确保 REGISTRY 在任何使用前就绪)
_register_defaults()


__all__ = ["DataType", "DataRegistry", "REGISTRY"]