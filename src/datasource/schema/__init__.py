"""datasource.schemas — 数据类型 Pydantic models + SCHEMAS 元信息表.

每个 schema 同时声明:
  - 字段类型 (Pydantic annotation); nullable 通过 ``Optional[X]`` 表达
  - 字段级 metadata (unit / description, 写在行末注释里)
  - 数据集级 metadata (ClassVar: ``type_name`` / ``schema_version`` / ``frequency`` /
    ``storage_path`` / ``primary_key`` / ``format`` / ``adapters``)

``SCHEMAS`` 是按 ``type_name`` (Pascal_Snake_Case) 索引的 BaseModel 表,
供 ``DataFile`` / CLI ``status`` / ``datasource.dataset`` facade 统一读取.
新增 schema 流程:
  1. 在本目录加一个 ``<snake_name>.py``, 继承 ``pydantic.BaseModel``,
     按需写 field_validator; ClassVar 元信息必须符合 Pascal_Snake_Case 等约束.
  2. 在本 ``__init__.py`` 的 ``SCHEMAS`` dict 登记 ``<Model>.type_name -> <Model>``.
"""

from __future__ import annotations

from pydantic import BaseModel

from common import DataAdapter
from datasource.schema.dividend_history import DividendHistory
from datasource.schema.financial_indicator import FinancialIndicator
from datasource.schema.financial_report_balance import FinancialReportBalance
from datasource.schema.financial_report_cashflow import FinancialReportCashflow
from datasource.schema.financial_report_income import FinancialReportIncome
from datasource.schema.stock_basic_data import StockBasicData
from datasource.schema.stock_kdata import StockKData as Stock_KData
from datasource.schema.trade_date import TradeDate
from datasource.schema.universe import Universe
from utils.tools import check_time_point, check_trade_date

__all__ = [
    # BaseModel 类 (供 type hints / validation 用)
    "DividendHistory",
    "FinancialIndicator",
    "FinancialReportBalance",
    "FinancialReportCashflow",
    "FinancialReportIncome",
    "StockKData",
    "StockBasicData",
    "TradeDate",
    "Universe",
    # 字段校验器 (供各 schema 复用)
    "check_trade_date",
    "check_time_point",
    # 元信息表 (供 DataFile / CLI / dataset facade 统一读取)
    "SCHEMAS",
]


# 按 ``type_name`` (Pascal_Snake_Case) 索引的 BaseModel 表.
# ``DataFile(model)`` 直接拿 Pydantic 类, 不再走 DataType forwarding 层.
SCHEMAS: dict[str, type[BaseModel]] = {
    cls.type_name: cls
    for cls in (
        DividendHistory,
        FinancialIndicator,
        FinancialReportBalance,
        FinancialReportCashflow,
        FinancialReportIncome,
        Stock_KData,
        StockBasicData,
        TradeDate,
        Universe,
    )
}
