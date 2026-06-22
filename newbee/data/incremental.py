"""增量拉取核心逻辑.

被 `scripts/fetch_incremental.py` 与 `newbee data update` CLI 复用.
职责:
- 对每个 category (`raw` / `adj`) 计算 resume range
- 调 `fetch_stock_hist(append=True)` 拉缺口
- 失败聚合到 FetchSummary, 不阻塞整体
- 成功跑完后调 `update_state` 持久化覆盖范围
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from newbee.data.calendar import latest_trading_day
from newbee.data.fetch_state import (
    SUPPORTED_CATEGORIES,
    infer_resume_range,
    update_state,
)
from newbee.data.sources.akshare import FetchSummary, fetch_stock_hist
from newbee.data.storage import DEFAULT_DATA_ROOT, infer_first_date_global, infer_last_date_global
from newbee.data.universe import StockPool

from newbee.utils import logger


# ---------- 计划 (dry-run 用) ----------


@dataclass
class CategoryPlan:
    """单个 category 的拉取计划 (dry-run 输出)."""

    category: str
    universe_size: int
    first_date: date | None
    last_date: date | None
    missing_days: int
    est_rows: int  # 估算行数 = universe_size * missing_days
    up_to_date: bool

    def render(self) -> str:
        if self.up_to_date:
            return (
                f"{self.category}: up-to-date (last={self.last_date}, "
                f"universe_size={self.universe_size})"
            )
        return (
            f"{self.category}: first={self.first_date} last={self.last_date} "
            f"missing_days={self.missing_days} universe_size={self.universe_size} "
            f"est_rows~={self.est_rows}"
        )


@dataclass
class UpdatePlan:
    """整个 update 的计划 (含全部 category)."""

    plans: list[CategoryPlan] = field(default_factory=list)
    latest_trading_day: date | None = None

    def render_table(self) -> str:
        lines = ["# update plan"]
        if self.latest_trading_day is not None:
            lines.append(f"latest_trading_day = {self.latest_trading_day.isoformat()}")
        for p in self.plans:
            lines.append(p.render())
        return "\n".join(lines)


# ---------- 计划生成 ----------


def build_plan(
    *,
    categories: Iterable[str] | None = None,
    today: date | None = None,
    now: None = None,  # 留作未来扩展; calendar.latest_trading_day 已自取 now
    root: Path = DEFAULT_DATA_ROOT,
) -> UpdatePlan:
    """生成 dry-run 用的更新计划 (不调网络)."""
    cats = list(categories) if categories else list(SUPPORTED_CATEGORIES)
    latest = latest_trading_day(today)
    pool = StockPool.load()
    n = pool.size()

    plans: list[CategoryPlan] = []
    for cat in cats:
        if cat in ("raw", "adj"):
            start, end = infer_resume_range(cat, latest=latest, root=root)
            if start > end:
                # 已 up-to-date
                last = infer_last_date_global(cat, root=root)
                plans.append(
                    CategoryPlan(
                        category=cat,
                        universe_size=n,
                        first_date=infer_first_date_global(cat, root=root),
                        last_date=last,
                        missing_days=0,
                        est_rows=0,
                        up_to_date=True,
                    )
                )
            else:
                missing = (end - start).days + 1
                plans.append(
                    CategoryPlan(
                        category=cat,
                        universe_size=n,
                        first_date=start,
                        last_date=end,
                        missing_days=missing,
                        est_rows=missing * n,
                        up_to_date=False,
                    )
                )
        else:
            # universe / pit / alpha / features: M1 阶段仅占位, 后续 PR 实现
            last = infer_last_date_global(cat, root=root)
            plans.append(
                CategoryPlan(
                    category=cat,
                    universe_size=n,
                    first_date=infer_first_date_global(cat, root=root),
                    last_date=last,
                    missing_days=0,
                    est_rows=0,
                    up_to_date=True,
                )
            )
    return UpdatePlan(plans=plans, latest_trading_day=latest)


# ---------- 实际执行 ----------


@dataclass
class UpdateResult:
    """update 执行结果汇总."""

    summaries: dict[str, FetchSummary] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_success(self) -> int:
        return sum(s.success for s in self.summaries.values())

    @property
    def total_failed(self) -> int:
        return sum(len(s.failed) for s in self.summaries.values())

    def has_failures(self) -> bool:
        return self.total_failed > 0


def run_update(
    *,
    categories: Iterable[str] | None = None,
    today: date | None = None,
    root: Path = DEFAULT_DATA_ROOT,
    progress: bool = True,
    source: str = "sina",
) -> UpdateResult:
    """执行增量拉取 (实际下载).

    Args:
        categories: 要更新的 category 列表 (默认 raw + adj)
        today: 基准日期 (用于 latest_trading_day 计算)
        root: data 根目录
        progress: 是否打 tqdm 进度条
        source: 数据源

    Returns:
        UpdateResult 含每类数据的 FetchSummary
    """
    cats = list(categories) if categories else ["raw", "adj"]
    latest = latest_trading_day(today)
    pool = StockPool.load(root / "universe" / "pool.parquet")
    stock_ids = pool.export()["stock_id"].tolist()

    if not stock_ids:
        raise RuntimeError("universe 为空, 请先跑 init_universe.py")

    result = UpdateResult()
    for cat in cats:
        if cat not in ("raw", "adj"):
            logger.warning(f"[update] skip unsupported category: {cat}")
            result.skipped.append(cat)
            continue

        start, end = infer_resume_range(cat, latest=latest, root=root)
        if start > end:
            logger.info(
                f"[update] {cat}: up-to-date (last={infer_last_date_global(cat, root=root)})"
            )
            result.skipped.append(cat)
            continue

        logger.info(f"[update] {cat}: resume {start} ~ {end} for {len(stock_ids)} stocks")
        summary = _fetch_panel_append(
            stock_ids=stock_ids,
            start=start,
            end=end,
            kind=cat,
            root=root,
            progress=progress,
            source=source,
        )
        result.summaries[cat] = summary
        logger.info(f"[update] {cat}: {summary}")

        # 落 fetch_state
        if summary.success > 0:
            first = infer_first_date_global(cat, root=root)
            last = infer_last_date_global(cat, root=root)
            row_count = _estimate_row_count(cat, root=root)
            update_state(
                cat,
                first_date=first,
                last_date=last,
                row_count=row_count,
                file_count=_count_files(cat, root=root),
                root=root,
            )
            logger.info(f"[update] {cat}: fetch_state updated ({first} ~ {last})")

    return result


def _fetch_panel_append(
    *,
    stock_ids: list[str],
    start: date,
    end: date,
    kind: str,
    root: Path,
    progress: bool,
    source: str,
) -> FetchSummary:
    """对每只股票调 fetch_stock_hist(append=True). 失败聚合到 FetchSummary."""
    import time

    from newbee.data.sources.akshare import DEFAULT_RAW_DIR, DEFAULT_ADJ_DIR, with_retry

    raw_dir = (root / "raw") if kind == "raw" else (root / "adj")
    # NOTE: fetch_stock_hist 默认落盘到 DEFAULT_RAW_DIR/DEFAULT_ADJ_DIR (Path("data/raw"))
    # 这里通过 raw_dir 参数显式控制
    if kind == "adj":
        # adj 时 adjust='qfq'
        adjust = "qfq"
    else:
        adjust = ""

    iter_ids: Iterable[str] = stock_ids
    if progress:
        try:
            from tqdm import tqdm

            iter_ids = tqdm(stock_ids, desc=f"[update:{kind}]")
        except ImportError:
            pass

    failed: list[str] = []
    t0 = time.time()
    for sid in iter_ids:
        try:
            fetch_stock_hist(
                sid,
                start=start,
                end=end,
                adjust=adjust,
                source=source,
                use_cache=False,  # append 路径不走 cache 分支
                raw_dir=raw_dir,
                append=True,
            )
        except Exception as e:
            logger.error(f"[update:{kind}] {sid} 失败: {e!r}")
            failed.append(sid)

    elapsed = time.time() - t0
    return FetchSummary(
        total=len(stock_ids),
        success=len(stock_ids) - len(failed),
        failed=failed,
        elapsed_sec=elapsed,
    )


def _count_files(kind: str, root: Path) -> int:
    sub = "raw" if kind == "raw" else "adj"
    cat_dir = root / sub
    if not cat_dir.exists():
        return 0
    return sum(1 for _ in cat_dir.glob("*.parquet"))


def _estimate_row_count(kind: str, root: Path) -> int:
    """估算某 category 的总行数 (扫所有 parquet 的 row count)."""
    sub = "raw" if kind == "raw" else "adj"
    cat_dir = root / sub
    if not cat_dir.exists():
        return 0
    total = 0
    for p in cat_dir.glob("*.parquet"):
        try:
            import pyarrow.parquet as pq

            total += pq.ParquetFile(p).metadata.num_rows
        except Exception:
            continue
    return total


__all__ = [
    "CategoryPlan",
    "UpdatePlan",
    "UpdateResult",
    "build_plan",
    "run_update",
]