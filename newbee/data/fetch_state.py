"""增量拉取状态跟踪 (单一事实源).

记录每种数据类型 (`raw` / `adj` / `universe` / `pit` / `alpha` / `features`)
的覆盖范围,作为增量拉取的输入与状态输出.

设计要点:
- 单一文件: `data/_manifest/fetch_state.json`,可 `git diff`,人类可读
- 原子写: tempfile + os.replace,崩溃安全
- 自动 bootstrap: 缺失文件时通过扫盘推算 first/last date
- 不持久化 last_run_status / 失败列表,失败重试由调用方通过 FetchSummary log 决定
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from newbee.data.storage import DEFAULT_DATA_ROOT, infer_first_date_global, infer_last_date_global

# ---------- 常量 ----------

# fetch_state.json 的 schema 版本 (便于未来不兼容升级)
STATE_VERSION = "1.0"

# 默认落盘位置
MANIFEST_DIR_NAME = "_manifest"
STATE_FILE_NAME = "fetch_state.json"

# 支持的数据类型 (与 design.md 中定义对齐)
SUPPORTED_CATEGORIES: tuple[str, ...] = (
    "raw",
    "adj",
    "universe",
    "pit",
    "alpha",
    "features",
)


def _state_path(root: Path) -> Path:
    return root / MANIFEST_DIR_NAME / STATE_FILE_NAME


# ---------- dataclass ----------


@dataclass
class CategoryCoverage:
    """单类数据的覆盖范围."""

    first_date: date | None = None
    last_date: date | None = None
    row_count: int = 0
    file_count: int = 0
    updated_at: str = ""

    @property
    def is_empty(self) -> bool:
        return self.first_date is None and self.last_date is None

    @property
    def days_covered(self) -> int:
        if self.first_date is None or self.last_date is None:
            return 0
        return (self.last_date - self.first_date).days + 1

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        # date -> iso
        d["first_date"] = self.first_date.isoformat() if self.first_date else None
        d["last_date"] = self.last_date.isoformat() if self.last_date else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> CategoryCoverage:
        first_raw = d.get("first_date")
        last_raw = d.get("last_date")
        first = date.fromisoformat(first_raw) if isinstance(first_raw, str) else None
        last = date.fromisoformat(last_raw) if isinstance(last_raw, str) else None
        return cls(
            first_date=first,
            last_date=last,
            row_count=int(d.get("row_count", 0)),
            file_count=int(d.get("file_count", 0)),
            updated_at=str(d.get("updated_at", "")),
        )


@dataclass
class FetchState:
    """fetch_state.json 的内存表示."""

    version: str = STATE_VERSION
    universe_sha: str | None = None
    categories: dict[str, CategoryCoverage] = field(default_factory=dict)
    updated_at: str = ""

    @property
    def is_fresh(self) -> bool:
        """True 当从未写入过 (read_state 在缺失文件时返回)."""
        return not self.categories and self.updated_at == ""

    def get(self, category: str) -> CategoryCoverage | None:
        return self.categories.get(category)


# ---------- Read / Write ----------


def read_state(root: Path = DEFAULT_DATA_ROOT) -> FetchState:
    """读 fetch_state.json. 文件不存在时返回 fresh FetchState.

    Args:
        root: data 根目录 (默认 `data/`)

    Returns:
        FetchState 实例. 缺失文件时 `is_fresh=True`.
    """
    path = _state_path(root)
    if not path.exists():
        return FetchState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    cats_raw = payload.get("categories", {})
    cats: dict[str, CategoryCoverage] = {}
    for name, entry in cats_raw.items():
        if isinstance(entry, dict):
            cats[name] = CategoryCoverage.from_dict(entry)
    return FetchState(
        version=str(payload.get("version", STATE_VERSION)),
        universe_sha=payload.get("universe_sha"),
        categories=cats,
        updated_at=str(payload.get("updated_at", "")),
    )


def update_state(
    category: str,
    *,
    first_date: date | None,
    last_date: date | None,
    row_count: int,
    file_count: int,
    universe_sha: str | None = None,
    root: Path = DEFAULT_DATA_ROOT,
) -> FetchState:
    """原子更新单类数据的覆盖范围.

    - 仅修改目标 category 的字段,其他 category 保持不变
    - `first_date=None` 时保留旧值;若旧值也是 None,则用 `last_date` bootstrap
    - 写入用 tempfile + os.replace,崩溃安全

    Returns:
        更新后的 FetchState
    """
    state = read_state(root)
    cov = state.categories.get(category) or CategoryCoverage()
    # first_date: 显式非 None 才覆盖 (bootstrap 路径允许 None 表示"用 last_date")
    if first_date is not None:
        cov.first_date = first_date
    elif cov.first_date is None and last_date is not None:
        # bootstrap: 仅在完全没有 first_date 时用 last_date 兜底
        cov.first_date = last_date
    cov.last_date = last_date
    cov.row_count = int(row_count)
    cov.file_count = int(file_count)
    cov.updated_at = datetime.now(timezone.utc).isoformat()
    state.categories[category] = cov

    if universe_sha is not None:
        state.universe_sha = universe_sha
    state.updated_at = datetime.now(timezone.utc).isoformat()

    _atomic_write(state, root)
    return state


def _atomic_write(state: FetchState, root: Path) -> None:
    """原子写 fetch_state.json (tempfile + os.replace)."""
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "version": state.version,
        "universe_sha": state.universe_sha,
        "categories": {name: cov.to_dict() for name, cov in state.categories.items()},
        "updated_at": state.updated_at,
    }
    # NamedTemporaryFile + delete=False + os.replace → atomic on POSIX & Windows
    fd, tmp_path = tempfile.mkstemp(
        prefix=".fetch_state_", suffix=".json.tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        # 清理临时文件,避免遗留
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


# ---------- Resume range 推断 ----------


def infer_resume_range(
    category: str,
    *,
    latest: date,
    root: Path = DEFAULT_DATA_ROOT,
) -> tuple[date, date]:
    """推断 category 的待补区间 `[start, latest]`.

    优先级:
    1. fetch_state.json 有 `last_date` → 从 last_date + 1 开始
    2. 否则扫盘取全局 max date 作为 last_date;若无文件,用 universe created_at (作为 start)
    3. 若 `start > latest`,返回空区间 `(latest+1, latest)`

    Args:
        category: 'raw' / 'adj' / 'universe' / 'pit' / 'alpha' / 'features'
        latest: 本次允许拉到的最大日期 (一般是 `latest_trading_day(today)`)

    Returns:
        (start, end) tuple of `date`. 当已 up-to-date 时 `start > end`.
    """
    state = read_state(root)
    cov = state.categories.get(category)

    if cov is not None and cov.last_date is not None:
        last = cov.last_date
        if last >= latest:
            return (latest + _one_day(), latest)
        return (last + _one_day(), latest)

    # 无 fetch_state, 扫盘看实际是否有数据
    last = infer_last_date_global(category, root=root)
    if last is not None:
        if last >= latest:
            return (latest + _one_day(), latest)
        return (last + _one_day(), latest)

    # 完全无数据 → 从 universe 默认开始 (语义上是 first_date, 不 +1)
    return (_universe_default_start(root), latest)


def _one_day() -> "timedelta":  # type: ignore[name-defined]
    from datetime import timedelta

    return timedelta(days=1)


def _universe_default_start(root: Path) -> date:
    """当全无数据时,返回 universe pool 的 created_at;否则兜底为 2020-01-01."""
    from newbee.data.universe import StockPool

    fallback = date(2020, 1, 1)
    pool_path = root / "universe" / "pool.parquet"
    if not pool_path.exists():
        return fallback
    try:
        pool = StockPool.load(pool_path)
        manifest = pool.manifest_path
        if manifest.exists():
            import json as _json

            m = _json.loads(manifest.read_text())
            # manifest 可能没有 created_at 字段,退化到 fallback
            ts = m.get("updated_at") or m.get("created_at")
            if isinstance(ts, str):
                # ISO 格式 → 取前 10 字符 (YYYY-MM-DD)
                return date.fromisoformat(ts[:10])
    except Exception:
        pass
    return fallback


# ---------- Summary ----------


def progress_summary(state: FetchState) -> dict[str, str]:
    """生成可打印的 per-category summary 字符串字典.

    仅包含 `first_date` 与 `last_date` 都不为 None 的 category.
    格式: `first=YYYY-MM-DD last=YYYY-MM-DD days=N`
    """
    out: dict[str, str] = {}
    for name, cov in state.categories.items():
        if cov.is_empty:
            continue
        first = cov.first_date.isoformat() if cov.first_date else "?"
        last = cov.last_date.isoformat() if cov.last_date else "?"
        out[name] = f"first={first} last={last} days={cov.days_covered}"
    return out


# ---------- Universe stale 判定 ----------


def is_universe_stale(state: FetchState, current_sha: str | None) -> bool:
    """fetch_state 记录的 universe_sha 与当前 pool 不一致 → True."""
    if current_sha is None:
        return False
    if state.universe_sha is None:
        return False  # 第一次,不算 stale
    return state.universe_sha != current_sha


__all__ = [
    "CategoryCoverage",
    "FetchState",
    "STATE_VERSION",
    "SUPPORTED_CATEGORIES",
    "infer_resume_range",
    "is_universe_stale",
    "progress_summary",
    "read_state",
    "update_state",
]