"""数据源适配器 (加厚版). 当前只实现 akshare, 未来切 Tushare 只改这一层."""
from newbee.data.sources.akshare import (
    fetch_index_constituents,
    fetch_stock_hist,
    fetch_stock_panel,
    fetch_with_fallback,
    FetchSummary,
    INDEX_CODE_MAP,
)

__all__ = [
    "fetch_index_constituents",
    "fetch_stock_hist",
    "fetch_stock_panel",
    "fetch_with_fallback",
    "FetchSummary",
    "INDEX_CODE_MAP",
]