from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

from common import DataAdapter
from utils.tools import check_stock_code

__all__ = ["Universe"]


class Universe(BaseModel):
    """自建股票池 (append-only, stock_index 单调递增)."""

    # ---- 元信息 (供 DataFile / CLI / dataset 读取; ClassVar 不参与实例化 / validation) ----
    type_name: ClassVar[str] = "Universe"
    schema_version: ClassVar[str] = "1.0"
    frequency: ClassVar[str] = "static"
    storage_path: ClassVar[str] = "Universe.csv"
    primary_key: ClassVar[tuple[str, ...]] = ("stock_index",)
    format: ClassVar[str] = "csv"

    # ---- 数据源适配器 ----
    # 指数成分股: akshare (IPO 日期字段已废弃, 不再走 baostock fallback).
    adapters: ClassVar[list[DataAdapter]] = [DataAdapter.Akshare]

    # ---- 模型配置 + 字段 ----
    model_config = ConfigDict(extra="forbid", frozen=False)

    stock_index: int  # int — 单调递增 idx, 一旦分配永不回收 (即使股票退市).
    stock_code: str  # 9-char .SH/.SZ — 9 字符股票代码.
    stock_name: str

    _check_stock_code = field_validator("stock_code")(check_stock_code)
