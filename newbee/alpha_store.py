"""Alpha 矩阵统一读写 (回测 / 实盘 同源).

设计:
  - `data/alpha/{strategy_id}/{date}.npy` 每天一个 ndarray(N,)
  - `data/alpha/{strategy_id}/manifest.json` 记录 universe_sha / model_v / range / sha
  - 写入时强制 shape 校验 == universe.size()
  - 读出时校验 universe_sha 一致 (防 stale cache)

API:
  - write(strategy_id, asof, scores, universe_size, model_v=..., extra_meta=...)
  - read(strategy_id, asof) -> ndarray(N,)
  - read_range(strategy_id, start, end) -> (ndarray(T, N), list[date])
  - has(strategy_id, asof) -> bool
  - list_dates(strategy_id) -> list[date]
  - delete(strategy_id, asof=None)  # None 时清整个 strategy
  - check_universe(strategy_id, pool) -> bool / raise
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from newbee.data.universe import StockPool

logger = logging.getLogger(__name__)

# ---------- 路径 ----------

DEFAULT_DATA_ROOT = Path("data")
ALPHA_DIR_NAME = "alpha"
MANIFEST_NAME = "manifest.json"

# ---------- 异常 ----------


class AlphaStoreError(Exception):
    pass


class AlphaShapeError(AlphaStoreError):
    pass


class AlphaStaleError(AlphaStoreError):
    pass


# ---------- 工具 ----------


def _dir(strategy_id: str, root: Path = DEFAULT_DATA_ROOT) -> Path:
    return root / ALPHA_DIR_NAME / strategy_id


def _manifest_path(strategy_id: str, root: Path = DEFAULT_DATA_ROOT) -> Path:
    return root / ALPHA_DIR_NAME / strategy_id / MANIFEST_NAME


def _npy_path(strategy_id: str, asof: date, root: Path = DEFAULT_DATA_ROOT) -> Path:
    return root / ALPHA_DIR_NAME / strategy_id / f"{asof.isoformat()}.npy"


def _universe_sha(pool: StockPool) -> str:
    import hashlib

    ids = sorted(pool.export()["stock_id"].tolist())
    payload = "|".join(ids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------- 核心 API ----------


def write(
    strategy_id: str,
    asof: date,
    scores: np.ndarray,
    *,
    universe_size: int,
    model_v: str = "1.0",
    extra_meta: dict[str, Any] | None = None,
    root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    """写一天的 alpha scores 到 npy + 更新 manifest.

    Args:
        strategy_id: 策略 id (如 "momentum_baseline")
        asof: 截面日期
        scores: ndarray(N,), 必须长度 == universe_size
        universe_size: 当前 universe.size(), 用于 shape 校验
        model_v: 模型版本, 写入 manifest
        extra_meta: 其它写入 manifest 的元信息 (e.g. factor_set)
        root: data 根目录

    Returns:
        写入的 npy 路径

    Raises:
        AlphaShapeError: shape 不对
    """
    if scores.ndim != 1:
        raise AlphaShapeError(f"scores 必须是 1-D, got shape={scores.shape}")
    if scores.shape[0] != universe_size:
        raise AlphaShapeError(
            f"写入 {strategy_id}/{asof}.npy shape={scores.shape[0]} "
            f"!= universe_size={universe_size}"
        )

    out_dir = _dir(strategy_id, root)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{asof.isoformat()}.npy"
    np.save(out_path, scores.astype(np.float64, copy=False))

    _update_manifest(
        strategy_id=strategy_id,
        asof=asof,
        model_v=model_v,
        extra_meta=extra_meta,
        root=root,
    )
    return out_path


def read(
    strategy_id: str, asof: date, *, root: Path = DEFAULT_DATA_ROOT
) -> np.ndarray:
    """读一天的 alpha scores, 不存在抛 FileNotFoundError."""
    path = _npy_path(strategy_id, asof, root)
    if not path.exists():
        raise FileNotFoundError(f"alpha 不存在: {path}")
    return np.load(path)


def has(
    strategy_id: str, asof: date, *, root: Path = DEFAULT_DATA_ROOT
) -> bool:
    return _npy_path(strategy_id, asof, root).exists()


def read_range(
    strategy_id: str,
    start: date,
    end: date,
    *,
    root: Path = DEFAULT_DATA_ROOT,
) -> tuple[np.ndarray, list[date]]:
    """批量读区间内的 alpha scores.

    Returns:
        (ndarray(T, N), list[date]) 按日期升序.
    """
    d = _dir(strategy_id, root)
    if not d.exists():
        return np.empty((0, 0), dtype=np.float64), []

    start_iso = start.isoformat()
    end_iso = end.isoformat()
    files: list[tuple[date, Path]] = []
    for p in d.glob("*.npy"):
        if start_iso <= p.stem <= end_iso:
            try:
                dt = date.fromisoformat(p.stem)
            except ValueError:
                continue
            files.append((dt, p))
    files.sort(key=lambda x: x[0])
    if not files:
        return np.empty((0, 0), dtype=np.float64), []
    dates = [dt for dt, _ in files]
    arr = np.stack([np.load(p) for _, p in files], axis=0)
    return arr, dates


def list_dates(
    strategy_id: str, *, root: Path = DEFAULT_DATA_ROOT
) -> list[date]:
    """列已写入的日期 (升序)."""
    d = _dir(strategy_id, root)
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.npy"):
        try:
            out.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(out)


def delete(
    strategy_id: str,
    asof: date | None = None,
    *,
    root: Path = DEFAULT_DATA_ROOT,
) -> int:
    """删一天的 npy (asof=date) 或整个 strategy 目录 (asof=None).

    Returns:
        删除的文件数
    """
    d = _dir(strategy_id, root)
    if not d.exists():
        return 0
    if asof is None:
        n = sum(1 for _ in d.glob("*.npy"))
        import shutil

        shutil.rmtree(d)
        return n
    target = d / f"{asof.isoformat()}.npy"
    if target.exists():
        target.unlink()
        return 1
    return 0


# ---------- manifest ----------


def _update_manifest(
    *,
    strategy_id: str,
    asof: date,
    model_v: str,
    extra_meta: dict[str, Any] | None,
    root: Path,
) -> Path:
    path = _manifest_path(strategy_id, root)
    if path.exists():
        manifest = json.loads(path.read_text())
    else:
        manifest = {
            "strategy_id": strategy_id,
            "model_v": model_v,
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


def set_universe_sha(
    strategy_id: str, pool: StockPool, *, root: Path = DEFAULT_DATA_ROOT
) -> None:
    """把当前 universe 的 sha 写入 manifest (一次性 stamp)."""
    path = _manifest_path(strategy_id, root)
    if not path.exists():
        raise FileNotFoundError(f"manifest 不存在: {path}")
    manifest = json.loads(path.read_text())
    manifest["universe_sha"] = _universe_sha(pool)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def check_universe(
    strategy_id: str,
    pool: StockPool,
    *,
    root: Path = DEFAULT_DATA_ROOT,
    strict: bool = True,
) -> bool:
    """校验 manifest 记录的 universe_sha 与当前 pool 一致.

    Returns:
        True 一致, False 不一致. strict=True 时不一致抛 AlphaStaleError.
    """
    path = _manifest_path(strategy_id, root)
    if not path.exists():
        if strict:
            raise FileNotFoundError(f"manifest 不存在: {path}")
        return False
    manifest = json.loads(path.read_text())
    cached = manifest.get("universe_sha")
    current = _universe_sha(pool)
    if cached is None:
        if strict:
            raise AlphaStaleError(f"manifest 未记录 universe_sha: {path}")
        return False
    if cached != current:
        msg = f"universe 不一致: manifest={cached}, current={current}"
        if strict:
            raise AlphaStaleError(msg)
        return False
    return True


def load_manifest(
    strategy_id: str, *, root: Path = DEFAULT_DATA_ROOT
) -> dict[str, Any]:
    path = _manifest_path(strategy_id, root)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# ---------- 面向对象封装 ----------


class AlphaStore:
    """面向对象入口, 绑定 (strategy_id, root, pool), 复用模块级函数.

    路径约定:
      - `path` 应指向 strategy 目录, 即 ``<root>/alpha/<strategy_id>``
      - 例: ``AlphaStore(Path("data/alpha/momentum_20_1.0"), pool)``

    模块级函数保持可用 (cli / 历史脚本不破坏), 本类只做薄包装.
    """

    def __init__(self, path: Path | str, pool: StockPool) -> None:
        self.path: Path = Path(path)
        self.pool: StockPool = pool
        # 从 path 推导 (root, strategy_id)
        # 约定: path = <root>/alpha/<strategy_id>
        if self.path.name == ALPHA_DIR_NAME:
            # 用户传入的是 <root>/alpha 目录, 无 strategy_id
            self.root: Path = self.path.parent
            self.strategy_id: str = ""
        else:
            self.root = self.path.parent
            self.strategy_id = self.path.name
        if not self.strategy_id:
            raise ValueError(
                f"无法从 path={self.path} 推导 strategy_id, "
                f"应传入 <root>/alpha/<strategy_id> 形式的路径"
            )

    def list_dates(self) -> list[date]:
        return list_dates(self.strategy_id, root=self.root)

    def has(self, asof: date) -> bool:
        return has(self.strategy_id, asof, root=self.root)

    def read(self, asof: date) -> np.ndarray:
        return read(self.strategy_id, asof, root=self.root)

    def read_range(
        self, start: date, end: date
    ) -> tuple[np.ndarray, list[date]]:
        return read_range(self.strategy_id, start, end, root=self.root)

    def write(
        self,
        asof: date,
        scores: np.ndarray,
        *,
        strategy_id: str | None = None,
        universe_size: int | None = None,
        model_v: str = "1.0",
        extra_meta: dict[str, Any] | None = None,
    ) -> Path:
        """写一天 alpha.

        Args:
            asof: 截面日期
            scores: ndarray(N,)
            strategy_id: 覆盖 strategy_id; 默认用 self.strategy_id
            universe_size: 覆盖 universe_size; 默认 self.pool.size()
            model_v: 写入 manifest 的模型版本
            extra_meta: 额外写入 manifest 的元信息
        """
        sid = strategy_id or self.strategy_id
        if not sid:
            raise ValueError("AlphaStore 未绑定 strategy_id")
        if universe_size is None:
            universe_size = self.pool.size()
        return write(
            sid,
            asof,
            scores,
            universe_size=universe_size,
            model_v=model_v,
            extra_meta=extra_meta,
            root=self.root,
        )

    def set_universe_sha(self) -> None:
        set_universe_sha(self.strategy_id, self.pool, root=self.root)

    def check_universe(self, *, strict: bool = True) -> bool:
        return check_universe(self.strategy_id, self.pool, root=self.root, strict=strict)