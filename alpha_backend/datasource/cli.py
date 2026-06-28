"""alpha_backend datasource CLI.

子命令:
  - datas status: 打印 Data_State.json + 每类型 stats
  - datas update [--type KData|Trade_Status|Stock_Basic_Data|Trading_Date|Universe] [--source sina|em|tx]: 增量拉取
    (Trading_Date 目标日期 = today + 1 天;CSV 缺失时自动 bootstrap)
  - datas init-universe [--index csi1000] [--backdate 2020-01-01]: 初始化 universe
  - datas codegen: 重跑 codegen
  - datas verify: 跑 dict_sync + storage_io + state_tracker 测试
  - datas migrate-legacy-kdata: 从 _Deprecated_raw/_adj 一次性迁出 KData + Stock_Basic_Data
  - datas populate-stock-basic-adj: 从 KData 算 adj_factor 并写回 Stock_Basic_Data
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from alpha_backend.utils import attach_file_log, logger


# CLI 自身文件位置, 用于解析仓库根 (alpha_backend/datasource/cli.py → parents[2] = repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_data_status(args: argparse.Namespace) -> int:
    from alpha_backend.datasource.registry import REGISTRY
    from alpha_backend.datasource.storage.io import DataFile
    from alpha_backend.datasource.storage.state import StateTracker

    root = Path(args.data_root) if args.data_root else Path.cwd()
    tracker = StateTracker(root / "datas" / "_Manifest" / "Data_State.json")
    states = tracker.read()
    print(f"=== alpha_backend datasource status ===")
    print(f"data_root: {root}")
    print(f"universe_sha: {tracker.get_universe_sha() or '(unset)'}")
    print()
    print(f"{'Type':<16} {'frequency':<10} {'first':<12} {'last':<12} {'rows':<10} {'stocks':<8} {'updated_at'}")
    print("-" * 90)
    for dtype in REGISTRY.all():
        f = DataFile(dtype, root=root)
        stats = f.stats()
        first = stats.first_date or "-"
        last = stats.last_date or "-"
        upd = stats.updated_at[:19] if stats.updated_at else "-"
        print(
            f"{dtype.name:<16} {dtype.frequency:<10} {first:<12} {last:<12} "
            f"{stats.row_count:<10} {stats.stock_count:<8} {upd}"
        )
    return 0


def cmd_data_update(args: argparse.Namespace) -> int:
    from alpha_backend.datasource.registry import REGISTRY

    type_name = args.type
    dtype = REGISTRY.get(type_name)

    root = Path(args.data_root) if args.data_root else Path.cwd()
    if dtype.name == "KData":
        from alpha_backend.datasource.service.kdata import KDataService

        # KData 更新耗时长, 挂个时间戳化日志文件, 跑完可复盘
        log_path = _REPO_ROOT / "logs" / f"kdata-update-{datetime.now():%Y%m%d-%H%M%S}.log"
        attach_file_log(log_path)
        logger.info(f"[KData] log file: {log_path}")

        svc = KDataService(root=str(root))
        summary = svc.daily_update(today=date.today(), source=args.source)
        print(f"KData update: success={summary.success} failed={len(summary.failed)} elapsed={summary.elapsed_sec:.1f}s")
        print(f"  first={summary.first_date} last={summary.last_date} rows={summary.row_count}")
    elif dtype.name == "Trade_Status":
        from alpha_backend.datasource.service.trade_status import TradeStatusService

        result = TradeStatusService(root=str(root)).daily_update(today=date.today())
        print(f"Trade_Status update: {result}")
    elif dtype.name == "Stock_Basic_Data":
        from alpha_backend.datasource.service.stock_basic_data import StockBasicDataService

        result = StockBasicDataService(root=str(root)).daily_update(today=date.today())
        print(f"Stock_Basic_Data update: {result}")
    elif dtype.name == "Trading_Date":
        from alpha_backend.datasource.service.trading_date import Trading_DateService

        # 目标日期 = 执行日 + 1 天;CSV 缺失时 daily_update 会自动 fallback 到 full_init.
        target = date.today() + timedelta(days=1)
        result = Trading_DateService(root=str(root)).daily_update(today=target)
        print(
            f"Trading_Date update: rows_added={result.rows_added} "
            f"last={result.last_date} rows={result.row_count} "
            f"elapsed={result.elapsed_sec:.1f}s"
        )
    elif dtype.name == "Universe":
        from alpha_backend.datasource.service.universe import UniverseService

        result = UniverseService(root=str(root)).full_init(
            index_name=args.index, backdate_to=args.backdate
        )
        print(f"Universe init: {result}")
    else:
        print(f"ERROR: unknown type {type_name!r}", file=sys.stderr)
        return 1
    return 0


def cmd_data_init_universe(args: argparse.Namespace) -> int:
    from alpha_backend.datasource.service.universe import UniverseService

    root = Path(args.data_root) if args.data_root else Path.cwd()
    result = UniverseService(root=str(root)).full_init(
        index_name=args.index, backdate_to=args.backdate
    )
    print(f"Universe init: {result}")
    return 0


def cmd_data_codegen(args: argparse.Namespace) -> int:
    from alpha_backend.datasource import codegen

    return codegen.main([])


def cmd_data_verify(args: argparse.Namespace) -> int:
    import subprocess

    cmds = [
        ["pytest", "tests/test_dict_sync.py", "-q"],
        ["pytest", "tests/test_storage_io.py", "-q"],
        ["pytest", "tests/test_state_tracker.py", "-q"],
    ]
    rc = 0
    for cmd in cmds:
        print(f"\n$ {' '.join(cmd)}")
        ret = subprocess.run(cmd, check=False)
        if ret.returncode != 0:
            rc = ret.returncode
    return rc


def cmd_data_migrate_legacy_kdata(args: argparse.Namespace) -> int:
    """从 datas/_Deprecated_raw + datas/_Deprecated_adj 一次性产出 KData + Stock_Basic_Data.

    两文件均通过 DataFile.upsert(replace) 原子写, 完成后更新 Data_State.json.
    """
    from dataclasses import replace as dc_replace

    from alpha_backend.datasource.migration.legacy_kdata import (
        build_kdata_from_legacy,
        build_stock_basic_data_from_legacy,
    )
    from alpha_backend.datasource.registry import REGISTRY
    from alpha_backend.datasource.storage.io import DataFile
    from alpha_backend.datasource.storage.state import StateTracker

    raw_dir = Path(args.raw_dir)
    adj_dir = Path(args.adj_dir)
    target_kdata = Path(args.target_kdata)
    target_stock_basic_data = Path(args.target_stock_basic_data)

    if not raw_dir.exists() or not raw_dir.is_dir():
        print(
            f"ERROR: raw_dir 不存在或不是目录: {raw_dir}",
            file=sys.stderr,
        )
        return 2
    if not adj_dir.exists() or not adj_dir.is_dir():
        print(
            f"ERROR: adj_dir 不存在或不是目录: {adj_dir}",
            file=sys.stderr,
        )
        return 2

    # DataFile 计算路径 = root / dtype.storage_path. 默认 root=PROJECT_ROOT (来自 io.py).
    # 当 target 是绝对路径时, 我们用一个临时 DataType (storage_path = 文件名) 配合
    # root=target.parent, 这样 DataFile 就写到 target 全路径.
    root = Path.cwd()
    state_path = root / "datas" / "_Manifest" / "Data_State.json"
    tracker = StateTracker(state_path)

    # ---- KData ----
    kdata_dtype = REGISTRY.get("KData")
    kdata_file = DataFile(
        dc_replace(
            kdata_dtype,
            storage_path=Path(target_kdata.name),
        ),
        root=target_kdata.parent,
    )
    logger.info(f"[migrate-legacy-kdata] KData: building from {raw_dir} + {adj_dir}")
    kdf = build_kdata_from_legacy(raw_dir, adj_dir)
    logger.info(
        f"[migrate-legacy-kdata] KData: {len(kdf)} rows, "
        f"{kdf['stock_code'].nunique() if len(kdf) else 0} stocks; writing to {target_kdata}"
    )
    kdata_file.upsert(kdf, conflict="replace")
    kstats = kdata_file.stats()
    tracker.update("KData", kstats)
    print(
        f"KData: {kstats.row_count} rows, {kstats.stock_count} stocks → {target_kdata}"
    )

    # ---- Stock_Basic_Data ----
    sbd_dtype = REGISTRY.get("Stock_Basic_Data")
    sbd_file = DataFile(
        dc_replace(
            sbd_dtype,
            storage_path=Path(target_stock_basic_data.name),
        ),
        root=target_stock_basic_data.parent,
    )
    logger.info(
        f"[migrate-legacy-kdata] Stock_Basic_Data: building from {raw_dir}"
    )
    sdf = build_stock_basic_data_from_legacy(raw_dir)
    logger.info(
        f"[migrate-legacy-kdata] Stock_Basic_Data: {len(sdf)} rows, "
        f"{sdf['stock_code'].nunique() if len(sdf) else 0} stocks; writing to {target_stock_basic_data}"
    )
    sbd_file.upsert(sdf, conflict="replace")
    sstats = sbd_file.stats()
    tracker.update("Stock_Basic_Data", sstats)
    print(
        f"Stock_Basic_Data: {sstats.row_count} rows, {sstats.stock_count} stocks → {target_stock_basic_data}"
    )

    return 0


def cmd_data_populate_stock_basic_adj(args: argparse.Namespace) -> int:
    """从 KData 算 adj_factor (= close_adj / close), 写回现有 Stock_Basic_Data."""
    from alpha_backend.datasource.migration.populate_adj_factor import (
        apply_adj_factor_to_stock_basic,
        compute_adj_factor_from_kdata,
    )
    from alpha_backend.datasource.registry import REGISTRY
    from alpha_backend.datasource.storage.io import DataFile
    from alpha_backend.datasource.storage.state import StateTracker

    kdata_path = Path(args.kdata_path)
    sbd_path = Path(args.sbd_path)

    if not kdata_path.exists():
        print(f"ERROR: kdata_path 不存在: {kdata_path}", file=sys.stderr)
        return 2
    if not sbd_path.exists():
        print(f"ERROR: sbd_path 不存在: {sbd_path}", file=sys.stderr)
        return 2

    root = Path.cwd()
    state_path = root / "datas" / "_Manifest" / "Data_State.json"
    tracker = StateTracker(state_path)

    logger.info(f"[populate-stock-basic-adj] reading KData from {kdata_path}")
    adj_df = compute_adj_factor_from_kdata(kdata_path)

    n_total = len(adj_df)
    n_null = int(adj_df["adj_factor"].isna().sum())
    n_real = n_total - n_null

    # 找出 adj_factor 为 None 的 stock_code (一般是 close_adj=None 的那些股票, e.g. 600000.SH)
    if n_null > 0:
        none_codes = sorted(adj_df.loc[adj_df["adj_factor"].isna(), "stock_code"].unique().tolist())
        logger.warning(
            f"[populate-stock-basic-adj] {n_null} rows 的 adj_factor 为 None, "
            f"涉及的 stock_code: {none_codes}"
        )

    logger.info(
        f"[populate-stock-basic-adj] KData: {n_total} rows, {n_real} non-null adj_factor"
    )

    written = apply_adj_factor_to_stock_basic(adj_df, sbd_path)
    logger.info(f"[populate-stock-basic-adj] wrote {written} rows to {sbd_path}")

    sbd_dtype = REGISTRY.get("Stock_Basic_Data")
    sbd_file = DataFile(sbd_dtype)
    stats = sbd_file.stats()
    tracker.update("Stock_Basic_Data", stats)

    print(f"Stock_Basic_Data: {stats.row_count} rows total")
    print(f"  adj_factor populated: {n_real}")
    print(f"  adj_factor None:      {n_null}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alpha_backend-datasource",
        description="alpha_backend 数据层 CLI",
    )
    parser.add_argument("--version", action="version", version="alpha_backend-datasource 0.1.0")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # status
    p_status = sub.add_parser("status", help="打印 Data_State + 每类型 stats")
    p_status.add_argument("--datas-root", type=Path, default=Path.cwd())
    p_status.set_defaults(func=cmd_data_status)

    # update
    p_update = sub.add_parser("update", help="增量拉取")
    p_update.add_argument(
        "--type",
        default="KData",
        help="类型名 (KData / Trade_Status / Stock_Basic_Data / Trading_Date / Universe)",
    )
    p_update.add_argument("--source", default="sina", choices=["sina", "em", "tx"])
    p_update.add_argument("--index", default="csi1000", help="universe 指数名 (仅 Universe)")
    p_update.add_argument("--backdate", default="2020-01-01", help="backdate (仅 Universe)")
    p_update.add_argument("--datas-root", type=Path, default=Path.cwd())
    p_update.set_defaults(func=cmd_data_update)

    # init-universe
    p_uni = sub.add_parser("init-universe", help="初始化 universe")
    p_uni.add_argument("--index", default="csi1000")
    p_uni.add_argument("--backdate", default="2020-01-01")
    p_uni.add_argument("--datas-root", type=Path, default=Path.cwd())
    p_uni.set_defaults(func=cmd_data_init_universe)

    # codegen
    p_codegen = sub.add_parser("codegen", help="跑 codegen")
    p_codegen.set_defaults(func=cmd_data_codegen)

    # verify
    p_verify = sub.add_parser("verify", help="跑全部 datasource 测试")
    p_verify.set_defaults(func=cmd_data_verify)

    # migrate-legacy-kdata
    p_mig = sub.add_parser(
        "migrate-legacy-kdata",
        help="从 _Deprecated_raw + _Deprecated_adj 一次性生成 KData + Stock_Basic_Data",
    )
    p_mig.add_argument("--raw-dir", type=Path, default=Path("datas/_Deprecated_raw"))
    p_mig.add_argument("--adj-dir", type=Path, default=Path("datas/_Deprecated_adj"))
    p_mig.add_argument("--target-kdata", type=Path, default=Path("datas/KData.parquet"))
    p_mig.add_argument(
        "--target-stock-basic-datas",
        type=Path,
        default=Path("datas/Stock_Basic_Data.parquet"),
    )
    p_mig.set_defaults(func=cmd_data_migrate_legacy_kdata)

    # populate-stock-basic-adj
    p_pop = sub.add_parser(
        "populate-stock-basic-adj",
        help="从 KData 算 adj_factor (= close_adj / close), 写回 Stock_Basic_Data",
    )
    p_pop.add_argument("--kdata-path", type=Path, default=Path("datas/KData.parquet"))
    p_pop.add_argument("--sbd-path", type=Path, default=Path("datas/Stock_Basic_Data.parquet"))
    p_pop.set_defaults(func=cmd_data_populate_stock_basic_adj)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())