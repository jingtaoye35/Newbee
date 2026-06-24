"""`Bars` adapter — 矩阵化 K 线读取 (T, N).

读取 `data/KData.parquet` (long format), 按 (trading_date, stock_code) pivot
出 (T, N) 矩阵, 返回 `Bars` dataclass. 供 `newbee.engines` 和 `scripts/`
使用, 替代旧的 `newbee.data.storage.load_bars_from_parquet`.

设计要点:
  - stock_ids 始终是 9 字符 stock_code (与 Universe.parquet 对齐),
    不再使用 6 位 stock_id
  - close = 未复权收盘价; adj_close = 前复权收盘价
  - `matrix` 属性: (T, N, 6) 堆叠 [open, high, low, close, volume, adj_close],
    列序与 `Bars` 字段顺序一致 (适配 `bars.matrix[:, :, 5]` 这种用法)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile

# ---------- 默认路径 ----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# ---------- 数据结构 ----------


@dataclass
class Bars:
    """全市场 K 线 (矩阵化, 速度优先).

    Attributes:
        dates: 长度 T 的日期列表 (升序)
        stock_ids: 长度 N 的 stock_code 列表 (与 idx 对齐, 9 字符 .SH/.SZ)
        open / high / low / close / volume: ndarray(T, N), NaN 表示缺失/停牌
        adj_close: 前复权收盘价, ndarray(T, N)
    """

    dates: list[date]
    stock_ids: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    adj_close: np.ndarray

    @property
    def T(self) -> int:
        return len(self.dates)

    @property
    def N(self) -> int:
        return len(self.stock_ids)

    @property
    def matrix(self) -> np.ndarray:
        """(T, N, 6) 堆叠 — 列序: open, high, low, close, volume, adj_close.

        供 `run_portfolio_backtest_from_store` 等需要单一切片的代码使用
        (例如 `bars.matrix[:, :, 5]` 取 adj_close).
        """
        return np.stack(
            [self.open, self.high, self.low, self.close, self.volume, self.adj_close],
            axis=-1,
        )

    def returns(self, kind: str = "simple") -> np.ndarray:
        """算日收益 (基于 adj_close).

        Args:
            kind: 'simple' (close[t]/close[t-1]-1) 或 'log' (log)
        Returns:
            ndarray(T, N), 第一行 NaN (无前一天, 无法算收益)
        """
        p = self.adj_close
        if kind == "simple":
            r = p / np.roll(p, 1, axis=0) - 1.0
        elif kind == "log":
            r = np.log(p / np.roll(p, 1, axis=0))
        else:
            raise ValueError(f"kind 必须是 'simple' 或 'log', 得到 {kind!r}")
        # 第一行没有前一天, 强制 NaN (np.roll 会把最后一行 wrap 到第一行, 需要清掉)
        r[0, :] = np.nan
        return r

    def active_mask_at(self, asof: date) -> np.ndarray:
        """当期活跃股票 mask (N,).

        简化实现: 直接看 adj_close 矩阵中 asof 对应行是否非 NaN.
        如需更精细 (基于 Universe.ipo_date), 应在外层调
        ``UniverseService.active_mask(asof)`` 后映射到本实例 stock_ids.
        """
        if asof not in self.dates:
            return np.zeros(self.N, dtype=bool)
        t = self.dates.index(asof)
        return ~np.isnan(self.adj_close[t])


# ---------- 工厂函数 ----------


def load_bars(
    stock_codes: Iterable[str],
    start: date,
    end: date,
    *,
    kind: str = "adj",
    root: Path | None = None,
) -> Bars:
    """从 `data/KData.parquet` 读全市场 K 线, 拼成 ndarray(T, N).

    Args:
        stock_codes: 要加载的股票列表 (9 字符 .SH/.SZ), 顺序即最终 N 列顺序
        start / end: 日期范围 (闭区间)
        kind: 'adj' (返回 adj_close = close_adj) 或 'raw' (返回 adj_close = close).
              新 KData 同时有 close + close_adj, 此参数决定 adj_close 的取值.
        root: data 根目录, 默认 `newbee/datasource/storage/bars_adapter.py` 之上 3 级 (PROJECT_ROOT)

    Returns:
        Bars (dates 升序; 缺数据的 cell 为 NaN)

    Raises:
        FileNotFoundError: KData.parquet 不存在
    """
    if kind not in ("adj", "raw"):
        raise ValueError(f"kind 必须是 'adj' 或 'raw', 得到 {kind!r}")

    codes = list(stock_codes)
    dtype = REGISTRY.get("KData")
    file_ = DataFile(dtype, root=root)

    df = file_.read(
        start=start.isoformat(),
        end=end.isoformat(),
        stock_codes=codes if codes else None,
        columns=[
            "trading_date",
            "stock_code",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_adj",
        ],
    )
    if df.empty:
        # 全部缺失 → 空 Bars (T=0, N=len(codes))
        empty = np.empty((0, len(codes)))
        return Bars(
            dates=[],
            stock_ids=codes,
            open=empty.copy(),
            high=empty.copy(),
            low=empty.copy(),
            close=empty.copy(),
            volume=empty.copy(),
            adj_close=empty.copy(),
        )

    # pivot 到 (T, N); 缺失 cell → NaN
    def _pivot(col: str) -> np.ndarray:
        wide = df.pivot(index="trading_date", columns="stock_code", values=col)
        # 强制 columns 顺序与 codes 一致; 缺失的 code 会变成全 NaN 列
        wide = wide.reindex(columns=codes)
        return wide.to_numpy(dtype=np.float64, na_value=np.nan)

    open_mat = _pivot("open")
    high_mat = _pivot("high")
    low_mat = _pivot("low")
    close_mat = _pivot("close")
    volume_mat = _pivot("volume")
    close_adj_mat = _pivot("close_adj")

    # trading_date 已是 ISO string, 按 ISO 升序即是交易日顺序
    trading_dates_asc = sorted(df["trading_date"].unique())
    dates = [date.fromisoformat(d) for d in trading_dates_asc]

    # kind 决定 adj_close
    if kind == "adj":
        adj_close_mat = close_adj_mat
    else:  # raw
        adj_close_mat = close_mat

    return Bars(
        dates=dates,
        stock_ids=codes,
        open=open_mat,
        high=high_mat,
        low=low_mat,
        close=close_mat,
        volume=volume_mat,
        adj_close=adj_close_mat,
    )


__all__ = ["Bars", "load_bars"]