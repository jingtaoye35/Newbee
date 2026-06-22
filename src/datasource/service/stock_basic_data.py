"""StockBasicDataService: 股票基础数据 (复权因子 + 涨跌停价 + 申万行业) full_init + daily_update.

M3 改造: adj_factor 不再从 Stock_KData 推算, 改由 baostock query_adjust_factor 提供
(authoritative). 单只股票 bs 失败 → 该股票 adj_factor 留 NaN, 不退回 Stock_KData 推算.
其他字段 (limit_upper_price / sw_industry / total_share / turnover) 仍为 None
stub — bs 没有这些字段或语义不一致, 待后续接入.
"""

from __future__ import annotations

from datasource.schema.stock_basic_data import StockBasicData

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from datasource.service.universe import UniverseService
from datasource.adapter.baostock import fetch_adj_factor_baostock
from datasource.service._financial_common import (
    parallel_chunked_fetch,
    upsert_and_update_state,
)
from datasource.storage.io import DataFile
from datasource.storage.state import StateTracker, DEFAULT_RESUME_START
from utils.parallel_run import parallel
from utils.tools import parse_iso_date
from logger import logger


def _parquet_columns_drift(path: Path) -> bool:
    """检测磁盘 parquet 列集合是否与当前 schema 不一致.

    现状: DataFile._assert_schema_fresh 只比 schema_version 字符串, 加列/减列
    (如 is_activate) 不会被发现. 这里用列集合兜底, 任何漂移 → 触发 truncate.
    """
    if not path.exists():
        return False
    try:
        import pyarrow.parquet as pq

        disk_cols = set(pq.read_schema(path).names)
    except Exception:
        return True  # 读不动也视为漂移, 让上层 truncate 重写
    expected = set(StockBasicData.model_fields.keys())
    return disk_cols != expected

ADJ_FACTOR_COLUMNS: Tuple[str, ...] = (
    "trade_date",
    "stock_code",
    "adj_factor",
    "limit_upper_price",
    "limit_lower_price",
    "sw_industry",
    "total_share",
    "turnover",
    "is_activate",
)


@parallel
def _fetch_one_adj_factor(code: str, start: str, end: str) -> pd.DataFrame:
    """worker: 拉单只股票的 adj_factor. 返回 DataFrame (可能为空).

    fallback 链: bs (authoritative) → akshare (推算, 慢但口径与 KData 一致).
    bs 失败 → 调 akshare fetch_adj_factor (single-stock 后复权日线, 一次
    HTTP 拉全 history, 比 bs per-symbol query 便宜很多, 适合 daily_update
    的 today-only 窗口).
    """
    # 注: 用 attribute access 读 baostock._BS_DISABLED_IN_PROCESS, 不要
    # `from ... import _BS_DISABLED_IN_PROCESS` — 那会创建 import-time 的
    # 本地 binding, 永远看到 False.
    import datasource.adapter.baostock as bs_mod

    if bs_mod._BS_DISABLED_IN_PROCESS:
        # 进程内 bs 已判定不可用 (login 黑名单等), 直接走 akshare, 避免
        # 1000 次重复 login 风暴.
        bs_skip = True
    else:
        bs_skip = False
    if not bs_skip:
        try:
            return fetch_adj_factor_baostock(code, start=start, end=end)
        except Exception as e_bs:
            logger.warning(
                f"[StockBasicData] {code} bs adj_factor failed, fallback to akshare: {e_bs!r}"
            )
    try:
        from datasource.adapter.akshare import fetch_adj_factor as fetch_adj_factor_akshare

        return fetch_adj_factor_akshare(code, start=start, end=end)
    except Exception as e_ak:
        logger.warning(
            f"[StockBasicData] {code} akshare adj_factor failed: {e_ak!r}, "
            f"该股票 adj_factor 留 NaN"
        )
        return pd.DataFrame(columns=["trade_date", "stock_code", "adj_factor"])


class StockBasicDataService:
    """股票基础数据服务 (复权因子 + 涨跌停价 + 申万行业)."""

    def __init__(self, *, root: Optional[str] = None) -> None:
        self.root = Path(root) if root else None
        self.dtype = StockBasicData
        self.file_ = DataFile(self.dtype, root=self.root) if root else DataFile(self.dtype)
        if root:
            self.state = StateTracker(self.file_.path.parent / "_Manifest" / "Data_State.json")
        else:
            self.state = StateTracker()  # 走 default_state_path() → datasource_dir/_Manifest/
        self.universe = UniverseService(root=str(self.root) if self.root else None)

    def full_init(self, *, start: str = DEFAULT_RESUME_START) -> dict[str, int]:
        """通过 baostock 拉所有 universe 股票的 adj_factor → 落盘.

        Args:
            start: 起始日期 ISO string (adj_factor 起点).
        """
        codes = self.universe.stock_pool()
        if not codes:
            raise RuntimeError("universe 为空, 请先跑 UniverseService.full_init")

        today_str = date.today().isoformat()
        # bs 可用性预检: 避免 1000 个 worker 子进程都重试 login 风暴. 失败时
        # _BS_DISABLED_IN_PROCESS 短路后续 bs 调用, 让所有 worker 直接走 akshare.
        import datasource.adapter.baostock as bs_mod

        try:
            if not bs_mod._BS_DISABLED_IN_PROCESS:
                _ensure_login()
                logger.info(
                    f"[StockBasicData] full_init: bs query_adjust_factor for "
                    f"{len(codes)} stocks, [{start}..{today_str}]"
                )
            else:
                logger.info(
                    f"[StockBasicData] full_init: bs 已被本进程禁用 (login 失败), "
                    f"全部 {len(codes)} 只走 akshare fallback, [{start}..{today_str}]"
                )
        except Exception as e:
            logger.warning(
                f"[StockBasicData] bs 不可用 ({e!r}), 全部 {len(codes)} 只走 akshare fallback"
            )

        # 并发按 chunk 拉 (M5.5 重构: 走 parallel_chunked_fetch, 与 5 个财务 service 一致).
        # bs 接口是 serial-style 的伪 batch (per-symbol query), 用 worker 池并发提速.
        summary, all_dfs = parallel_chunked_fetch(
            "Stock_Basic_Data",
            codes,
            _fetch_one_adj_factor,
            extra_args=(start, today_str),
            desc=f"[StockBasicData] adj_factor {{}}-",
        )

        df = (
            pd.concat(all_dfs, ignore_index=True)
            if all_dfs
            else pd.DataFrame(columns=["trade_date", "stock_code", "adj_factor"])
        )

        if df.empty:
            logger.warning(
                "[StockBasicData] bs adj_factor 全失败 (parallel_chunked_fetch), "
                f"failed={len(summary.failed)}/{len(codes)}, adj_factor 全留 NaN"
            )
            empty = pd.DataFrame(columns=list(ADJ_FACTOR_COLUMNS))
            empty = empty.assign(
                limit_upper_price=pd.Series(dtype="float32"),
                limit_lower_price=pd.Series(dtype="float32"),
                sw_industry=pd.Series(dtype="string"),
                total_share=pd.Series(dtype="float64"),
                turnover=pd.Series(dtype="float64"),
                is_activate=pd.Series(dtype="bool"),
            )
            self.file_.write(empty)
            stats = self.file_.stats()
            self.state.update("Stock_Basic_Data", stats)
            return {"rows": 0, "stocks_with_factor": 0, "bs_failed": len(summary.failed)}

        # 填充其余字段为 None (与 schema 对齐). 用 object dtype + python None
        # 以避免 pd.NA 在 Pydantic string 校验时被拒绝.
        for col, dtype in (
            ("limit_upper_price", "float32"),
            ("limit_lower_price", "float32"),
            ("total_share", "float64"),
            ("turnover", "float64"),
        ):
            df[col] = pd.Series([None] * len(df), dtype=object).astype(dtype, errors="ignore")
        df["sw_industry"] = pd.Series([None] * len(df), dtype=object)
        # is_activate: M1 baostock 仅提供活跃股票 adj_factor, 故默认 True.
        # Trade_StatusService 可在 daily_update 时按需覆写.
        df["is_activate"] = True
        df = df[list(ADJ_FACTOR_COLUMNS)].copy()

        self.file_.upsert(df, conflict="replace")
        stats = self.file_.stats()
        self.state.update("Stock_Basic_Data", stats)
        return {
            "rows": int(stats.row_count),
            "stocks_with_factor": int(df["stock_code"].nunique()),
            "bs_failed": len(summary.failed),
        }

    def daily_update(self, *, today: Optional[str] = None) -> dict[str, int]:
        today_str = today or date.today().isoformat()
        # 自愈: 磁盘 parquet 列结构与当前 schema 漂移 (历史脏数据 / 加减列)
        # → 自动 truncate, 让后续 full_init 全量重建.
        if _parquet_columns_drift(self.file_.path):
            logger.warning(
                "[StockBasicData] 检测到磁盘 parquet 列结构漂移, "
                "自动 truncate 后从 bs 全量重建"
            )
            self.file_.truncate()
            self.state.delete("Stock_Basic_Data")
        # 自愈: 磁盘 row_count 与 universe 规模严重不匹配 (说明历史只写
        # 了 1 行 stub 之类) → 视为脏数据, truncate 后重建. universe 是
        # 拉取目标, row_count 应当 ≥ universe size; 阈值取 min(10, len/4)
        # 给小幅波动留余地, 严防「1 行」之类的死锁.
        elif self.file_.path.exists():
            try:
                stats = self.file_.stats()
                n_uni = len(self.universe.stock_pool())
                if n_uni > 0 and stats.row_count > 0 and stats.row_count < min(10, n_uni // 4):
                    logger.warning(
                        f"[StockBasicData] 检测到磁盘 row_count={stats.row_count} "
                        f"过小 (universe={n_uni}), 视为脏数据, 自动 truncate 重建"
                    )
                    self.file_.truncate()
                    self.state.delete("Stock_Basic_Data")
            except Exception as e:
                logger.warning(f"[StockBasicData] 自愈检查失败 (非致命): {e!r}")
        start, end = self.state.resume_range("Stock_Basic_Data", latest=today_str)
        if start > end:
            return {"rows": int(self.file_.stats().row_count), "skipped": True}
        # 增量窗口: [start-1, today] 避免边界遗漏 (start = 上次 last_date + 1)
        bs_start = (parse_iso_date(start) - timedelta(days=1)).isoformat()
        return self.full_init(start=bs_start)


__all__ = ["StockBasicDataService", "ADJ_FACTOR_COLUMNS"]
