"""数据存储层.

双格式 + manifest 校验, 保证:

- **parquet (per-stock)**: 用于 raw K 线与复权后 K 线, 每只股票一个文件
  - `data/raw/{stock_id}.parquet`         原始下载 (含未复权 close)
  - `data/adj/{stock_id}.parquet`         前复权 (M1 默认)
- **npy (per-day)**: 用于因子值 / alpha 矩阵 (矩阵化, 速度优先)
  - `data/features/{factor_name}/{date}.npy`      ndarray(N,)
  - `data/alpha/{strategy_id}/{date}.npy`         ndarray(N,)
  - 强约束: `shape == universe.size()`, 否则写失败
- **manifest.json**: 记录 universe_sha / factor_v / range / sha,
  校验缓存是否与当前 universe 一致 (防 stale cache)

设计原则: 写入即校验, 读出即校验, 让 corrupt 数据尽早暴露。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from newbee.data.universe import StockPool

# ---------- 默认路径 ----------

DEFAULT_DATA_ROOT = Path("data")
RAW_DIR_NAME = "raw"
ADJ_DIR_NAME = "adj"
FEATURES_DIR_NAME = "features"
ALPHA_DIR_NAME = "alpha"
MANIFEST_NAME = "manifest.json"

# ---------- 异常 ----------


class StorageError(Exception):
    """存储层错误的基类."""


class ShapeMismatchError(StorageError):
    """写入时 shape 与 universe.size() 不一致."""


class ManifestMismatchError(StorageError):
    """manifest 校验失败 (stale cache)."""


# ---------- 通用工具 ----------


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------- 日期推断 (per-category 增量拉取用) ----------


def infer_last_date(
    stock_id: str,
    kind: str = "adj",
    root: Path = DEFAULT_DATA_ROOT,
) -> date | None:
    """读 `data/{kind}/{stock_id}.parquet` 的最大 `date`.

    文件缺失或抛错时返回 `None` (用于增量 resume 检测).
    """
    if kind not in ("raw", "adj"):
        raise ValueError(f"kind 必须是 'raw' 或 'adj', 得到 {kind!r}")
    path = root / (RAW_DIR_NAME if kind == "raw" else ADJ_DIR_NAME) / f"{stock_id}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["date"])
    except Exception:
        return None
    if df.empty:
        return None
    ts = df["date"].max()
    return ts.date() if hasattr(ts, "date") else ts


def infer_last_date_global(
    kind: str,
    root: Path = DEFAULT_DATA_ROOT,
) -> date | None:
    """扫 `data/{kind}/*.parquet` 取所有文件的最大 `date`. 空目录返回 `None`."""
    if kind not in ("raw", "adj"):
        raise ValueError(f"kind 必须是 'raw' 或 'adj', 得到 {kind!r}")
    sub = RAW_DIR_NAME if kind == "raw" else ADJ_DIR_NAME
    cat_dir = root / sub
    if not cat_dir.exists():
        return None
    max_ts: pd.Timestamp | None = None
    for p in cat_dir.glob("*.parquet"):
        try:
            df = pd.read_parquet(p, columns=["date"])
        except Exception:
            continue
        if df.empty:
            continue
        cur = df["date"].max()
        if max_ts is None or cur > max_ts:
            max_ts = cur
    if max_ts is None:
        return None
    return max_ts.date() if hasattr(max_ts, "date") else max_ts


def infer_first_date_global(
    kind: str,
    root: Path = DEFAULT_DATA_ROOT,
) -> date | None:
    """扫 `data/{kind}/*.parquet` 取所有文件的最小 `date`. 空目录返回 `None`."""
    if kind not in ("raw", "adj"):
        raise ValueError(f"kind 必须是 'raw' 或 'adj', 得到 {kind!r}")
    sub = RAW_DIR_NAME if kind == "raw" else ADJ_DIR_NAME
    cat_dir = root / sub
    if not cat_dir.exists():
        return None
    min_ts: pd.Timestamp | None = None
    for p in cat_dir.glob("*.parquet"):
        try:
            df = pd.read_parquet(p, columns=["date"])
        except Exception:
            continue
        if df.empty:
            continue
        cur = df["date"].min()
        if min_ts is None or cur < min_ts:
            min_ts = cur
    if min_ts is None:
        return None
    return min_ts.date() if hasattr(min_ts, "date") else min_ts


def _file_sha256(path: Path) -> str:
    """计算文件 sha256 (前 16 位)."""
    h = hashlib.sha256()
    if not path.exists():
        return "missing"
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _universe_sha(pool: StockPool) -> str:
    """用 pool 内容 (排序后的 stock_id 列表) 算 sha."""
    ids = sorted(pool.export()["stock_id"].tolist())
    payload = "|".join(ids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------- Parquet 读写 (per-stock) ----------


def write_stock_parquet(
    df: pd.DataFrame,
    stock_id: str,
    kind: str = "adj",
    root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """写单只股票的 K 线到 parquet.

    Args:
        df: 必含列 `date` / `open` / `high` / `low` / `close` / `volume`
            (kind=raw 还可含复权因子 adj_factor)
        stock_id: 6 位代码
        kind: 'raw' 或 'adj'
        root: data 根目录

    Returns:
        写入的 parquet 路径
    """
    if kind not in ("raw", "adj"):
        raise ValueError(f"kind 必须是 'raw' 或 'adj', 得到 {kind!r}")
    sub = RAW_DIR_NAME if kind == "raw" else ADJ_DIR_NAME
    out_dir = _ensure_dir(root / sub)
    out_path = out_dir / f"{stock_id}.parquet"
    df = df.sort_values("date").reset_index(drop=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")
    return out_path


def read_stock_parquet(
    stock_id: str,
    kind: str = "adj",
    root: Path = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """读单只股票的 K 线, 不存在则抛 FileNotFoundError."""
    if kind not in ("raw", "adj"):
        raise ValueError(f"kind 必须是 'raw' 或 'adj', 得到 {kind!r}")
    sub = RAW_DIR_NAME if kind == "raw" else ADJ_DIR_NAME
    path = root / sub / f"{stock_id}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"未找到 {kind} parquet: {path}")
    return pd.read_parquet(path)


# ---------- npy 读写 (per-day, 矩阵化) ----------


def write_day_npy(
    values: np.ndarray,
    *,
    category: str,
    name: str,
    asof: date,
    universe_size: int,
    version: str = "1.0",
    extra_meta: dict[str, Any] | None = None,
    root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """写一天的横截面 npy (shape=(N,)).

    Args:
        values: ndarray(N,), 必须长度 == universe_size
        category: 'features' 或 'alpha'
        name: 因子名 / 策略 id
        asof: 截面日期
        universe_size: 当前 universe 大小, 用作 shape 校验
        version: 因子版本 / 模型版本 (写到 manifest)
        extra_meta: 其它写到 manifest 的元信息 (e.g. factor_window)
        root: data 根目录
    """
    if category not in ("features", "alpha"):
        raise ValueError(f"category 必须是 'features' 或 'alpha', 得到 {category!r}")
    if values.ndim != 1:
        raise ShapeMismatchError(
            f"期望 1-D ndarray, 得到 shape={values.shape}"
        )
    if values.shape[0] != universe_size:
        raise ShapeMismatchError(
            f"写入 {category}/{name}/{asof}.npy shape={values.shape[0]} "
            f"与 universe.size()={universe_size} 不一致"
        )

    out_dir = _ensure_dir(root / category / name)
    out_path = out_dir / f"{asof.isoformat()}.npy"
    np.save(out_path, values.astype(np.float64, copy=False))

    # 更新 manifest
    _update_manifest(
        root=root,
        category=category,
        name=name,
        asof=asof,
        version=version,
        extra_meta=extra_meta,
    )
    return out_path


def read_day_npy(
    *,
    category: str,
    name: str,
    asof: date,
    root: Path = DEFAULT_DATA_ROOT,
) -> np.ndarray:
    """读一天的 npy. 不存在则抛 FileNotFoundError."""
    path = root / category / name / f"{asof.isoformat()}.npy"
    if not path.exists():
        raise FileNotFoundError(f"未找到: {path}")
    return np.load(path)


def read_range_npy(
    *,
    category: str,
    name: str,
    start: date,
    end: date,
    root: Path = DEFAULT_DATA_ROOT,
) -> tuple[np.ndarray, list[date]]:
    """批量读区间内的 npy, 返回 ndarray(T, N) 与对应的日期列表 (按日期升序).

    缺失的日期自动跳过 (但返回的 T 是实际读到的天数, 调用方应校验是否覆盖期望区间).
    """
    if category not in ("features", "alpha"):
        raise ValueError(f"category 必须是 'features' 或 'alpha', 得到 {category!r}")
    name_dir = root / category / name
    if not name_dir.exists():
        return np.empty((0, 0), dtype=np.float64), []

    # 列出区间内的 .npy
    start_iso = start.isoformat()
    end_iso = end.isoformat()
    files: list[tuple[date, Path]] = []
    for p in name_dir.glob("*.npy"):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if start_iso <= p.stem <= end_iso:
            files.append((d, p))

    files.sort(key=lambda x: x[0])
    if not files:
        return np.empty((0, 0), dtype=np.float64), []

    dates = [d for d, _ in files]
    arrays = [np.load(p) for _, p in files]
    return np.stack(arrays, axis=0), dates


# ---------- manifest 管理 ----------


def _manifest_path(root: Path, category: str, name: str) -> Path:
    return root / category / name / MANIFEST_NAME


def _update_manifest(
    *,
    root: Path,
    category: str,
    name: str,
    asof: date,
    version: str,
    extra_meta: dict[str, Any] | None,
) -> Path:
    """每次写 npy 时更新 manifest.json (增量)."""
    path = _manifest_path(root, category, name)
    if path.exists():
        manifest = json.loads(path.read_text())
    else:
        manifest = {
            "category": category,
            "name": name,
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dates": [],
            "count": 0,
            "universe_sha": None,
        }

    iso = asof.isoformat()
    if iso not in manifest["dates"]:
        manifest["dates"].append(iso)
        manifest["count"] = len(manifest["dates"])
    manifest["last_asof"] = iso
    manifest["last_updated"] = datetime.now(timezone.utc).isoformat()
    if extra_meta:
        manifest.update(extra_meta)

    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return path


def set_manifest_universe_sha(
    *,
    category: str,
    name: str,
    pool: StockPool,
    root: Path = DEFAULT_DATA_ROOT,
) -> None:
    """把当前 universe 的 sha 写入 manifest (一次性 stamp).

    通常在初始化 cache 时调用. 之后每次读 cache 都会校验 universe_sha,
    不一致视为 stale.
    """
    path = _manifest_path(root, category, name)
    if not path.exists():
        raise FileNotFoundError(f"manifest 不存在: {path}")
    manifest = json.loads(path.read_text())
    manifest["universe_sha"] = _universe_sha(pool)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def check_manifest_universe_sha(
    *,
    category: str,
    name: str,
    pool: StockPool,
    root: Path = DEFAULT_DATA_ROOT,
    strict: bool = True,
) -> bool:
    """校验 manifest 记录的 universe_sha 与当前 pool 是否一致.

    Returns:
        True 一致, False 不一致. strict=True 时不一致抛 ManifestMismatchError.
    """
    path = _manifest_path(root, category, name)
    if not path.exists():
        if strict:
            raise FileNotFoundError(f"manifest 不存在: {path}")
        return False
    manifest = json.loads(path.read_text())
    cached = manifest.get("universe_sha")
    current = _universe_sha(pool)
    if cached is None:
        if strict:
            raise ManifestMismatchError(
                f"manifest {path} 未记录 universe_sha (stale)"
            )
        return False
    if cached != current:
        msg = (
            f"universe 不一致: manifest={cached}, current={current}. "
            f"该 cache 视为 stale, 请重算."
        )
        if strict:
            raise ManifestMismatchError(msg)
        return False
    return True


def load_manifest(
    category: str,
    name: str,
    root: Path = DEFAULT_DATA_ROOT,
) -> dict[str, Any]:
    """读 manifest 字典, 不存在返回空 dict."""
    path = _manifest_path(root, category, name)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# ---------- 高层 helpers (回测常用) ----------


@dataclass
class Bars:
    """全市场 K 线 (矩阵化, 速度优先).

    Attributes:
        dates: 长度 T 的日期列表 (升序)
        stock_ids: 长度 N 的 stock_id 列表 (与 idx 对齐)
        open / high / low / close / volume: ndarray(T, N), NaN 表示缺失/停牌
        adj_close: 前复权收盘价 (M1 默认), ndarray(T, N)
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

    def returns(self, kind: str = "simple") -> np.ndarray:
        """算日收益.

        Args:
            kind: 'simple' (close[t]/close[t-1]-1) 或 'log' (log)
        Returns:
            ndarray(T, N), 第一行 NaN
        """
        p = self.adj_close
        if kind == "simple":
            return p / np.roll(p, 1, axis=0) - 1.0
        elif kind == "log":
            return np.log(p / np.roll(p, 1, axis=0))
        else:
            raise ValueError(f"kind 必须是 'simple' 或 'log', 得到 {kind!r}")

    def active_mask_at(self, asof: date) -> np.ndarray:
        """当期活跃股票 mask (基于加入时间, 不基于 K 线存在性)."""
        # 简单实现: 直接看 adj_close[当天] 是否非 NaN
        # (实际可能需要更精细处理, 但 M1 阶段够用)
        from newbee.data.universe import StockPool

        pool = StockPool.load()
        full_mask = pool.active_mask(asof)  # ndarray(N,)
        # 落到 self.stock_ids 上
        idx_map = {sid: i for i, sid in enumerate(pool.export()["stock_id"].tolist())}
        mask = np.zeros(self.N, dtype=bool)
        for i, sid in enumerate(self.stock_ids):
            j = idx_map.get(sid)
            if j is not None and full_mask[j]:
                mask[i] = True
        return mask


def load_bars_from_parquet(
    stock_ids: Iterable[str],
    start: date,
    end: date,
    *,
    kind: str = "adj",
    root: Path = DEFAULT_DATA_ROOT,
) -> Bars:
    """从 per-stock parquet 读全市场 K 线, 拼成 ndarray(T, N).

    Args:
        stock_ids: 要加载的股票列表 (按 pool idx 顺序)
        start / end: 日期范围
        kind: 'adj' (前复权) 或 'raw' (未复权)
        root: data 根目录
    """
    stock_ids = list(stock_ids)
    dfs: dict[str, pd.DataFrame] = {}
    for sid in stock_ids:
        try:
            df = read_stock_parquet(sid, kind=kind, root=root)
        except FileNotFoundError:
            continue
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
        dfs[sid] = df.set_index("date").sort_index()

    if not dfs:
        # 全部缺失, 返回空 Bars
        return Bars(
            dates=[],
            stock_ids=stock_ids,
            open=np.empty((0, len(stock_ids))),
            high=np.empty((0, len(stock_ids))),
            low=np.empty((0, len(stock_ids))),
            close=np.empty((0, len(stock_ids))),
            volume=np.empty((0, len(stock_ids))),
            adj_close=np.empty((0, len(stock_ids))),
        )

    # 取并集日期
    all_dates = sorted(set().union(*(df.index for df in dfs.values())))
    date_to_pos = {d: i for i, d in enumerate(all_dates)}
    T = len(all_dates)
    N = len(stock_ids)
    sid_to_pos = {sid: i for i, sid in enumerate(stock_ids)}

    opens = np.full((T, N), np.nan)
    highs = np.full((T, N), np.nan)
    lows = np.full((T, N), np.nan)
    closes = np.full((T, N), np.nan)
    volumes = np.full((T, N), np.nan)
    adj_closes = np.full((T, N), np.nan)

    for sid, df in dfs.items():
        j = sid_to_pos[sid]
        for d, row in df.iterrows():
            # date_to_pos 的 key 是 Timestamp (来自 DatetimeIndex)
            # d 来自 iterrows 的 DatetimeIndex, 也是 Timestamp, 直接 lookup
            i = date_to_pos[d]
            opens[i, j] = row.get("open", np.nan)
            highs[i, j] = row.get("high", np.nan)
            lows[i, j] = row.get("low", np.nan)
            closes[i, j] = row.get("close", np.nan)
            volumes[i, j] = row.get("volume", np.nan)
            # adj_close 优先 adj 列, 否则等于 close (raw 时)
            adj_closes[i, j] = row.get("adj_close", row.get("close", np.nan))

    return Bars(
        dates=[d.date() if hasattr(d, "date") else d for d in all_dates],
        stock_ids=stock_ids,
        open=opens,
        high=highs,
        low=lows,
        close=closes,
        volume=volumes,
        adj_close=adj_closes,
    )