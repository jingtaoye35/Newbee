"""newbee datasource CLI.

子命令:
  - datas status: 打印 Data_State.json + 每类型 stats
  - datas update [--type Stock_KData|Stock_Basic_Data|Trade_Date|Universe] [--source sina|em|tx]: 增量拉取 (Trade_Date 目标日期 = today + 1 天;CSV 缺失时自动 bootstrap)
  - datas init-universe [--index csi1000] [--backdate 2020-01-01]: 初始化 universe
  - datas kdata-validate [--root PATH] [--null-threshold FLOAT]: Stock_KData 完整性校验
    (退出码 0=ok / 1=warn / 2=fail)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from logger import attach_file_log, logger
from utils.tools import now_date
from datasource.storage.io import default_datasource_dir
from datasource.storage.state import DEFAULT_RESUME_START

# CLI 自身文件位置, 用于解析仓库根 (newbee/datasource/cli.py → parents[2] = repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_data_status(args: argparse.Namespace) -> int:
    from datasource.schema import SCHEMAS
    from datasource.storage.io import DataFile
    from datasource.storage.state import StateTracker

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    tracker = StateTracker(root / "_Manifest" / "Data_State.json")
    states = tracker.read()
    print(f"=== newbee datasource status ===")
    print(f"args.datasource_dir: {root}")
    print(f"universe_sha: {tracker.get_universe_sha() or '(unset)'}")
    print()
    print(
        f"{'Type':<16} {'frequency':<10} {'first':<12} {'last':<12} {'rows':<10} {'stocks':<8} {'updated_at'}"
    )
    print("-" * 90)
    for Model in SCHEMAS.values():
        dtype = Model
        f = DataFile(dtype, root=root)
        stats = f.stats()
        first = stats.first_date or "-"
        last = stats.last_date or "-"
        upd = stats.updated_at[:19] if stats.updated_at else "-"
        print(
            f"{dtype.type_name:<16} {dtype.frequency:<10} {first:<12} {last:<12} "
            f"{stats.row_count:<10} {stats.stock_count:<8} {upd}"
        )
    return 0


def cmd_data_update(args: argparse.Namespace) -> int:
    """按 TYPE_DISPATCH 路由到对应 _update_<type> handler.

    新增 type 只需在 TYPE_DISPATCH 加一行 + 写一个 _update_<type> 函数;
    不再修改本函数体.

    handler 解析在调用时进行, 方便测试时 monkeypatch setattr 替换.
    """
    handler_name = TYPE_DISPATCH.get(args.type)
    if handler_name is None:
        print(f"ERROR: unknown type {args.type!r}", file=sys.stderr)
        return 1
    handler = globals().get(handler_name)
    if handler is None:
        print(f"ERROR: dispatcher 未找到 handler {handler_name!r}", file=sys.stderr)
        return 1
    return handler(args)


# ---------- TYPE_DISPATCH handlers ----------
# 每个 handler 是顶层函数, 签名 (args) -> int, 不调用 sys.exit.
# 这样 cmd_data_update 永远只是一行 dispatch + 一行 unknown 检查.
def _update_universe(args: argparse.Namespace) -> int:
    from datasource.service.universe import UniverseService

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = UniverseService(root=str(root)).full_init(index_name=args.index, backdate_to=args.backdate)
    print(f"Universe init: {result}")
    return 0


def _update_trade_date(args: argparse.Namespace) -> int:
    from datasource.service.trade_date import TradeDateService
    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    today = now_date()
    if root:
        result = TradeDateService(root=str(root)).daily_update(today=today)
    else:
        result = TradeDateService().daily_update(today=today)
    
    print(
        f"Trade_Date update: rows_added={result.rows_added} "
        f"last={result.last_date} rows={result.row_count} "
        f"elapsed={result.elapsed_sec:.1f}s"
    )
    return 0


def _update_kdata(args: argparse.Namespace) -> int:
    from datasource.service.stock_kdata import KDataService

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()

    # Stock_KData 更新耗时长, 挂个时间戳化日志文件, 跑完可复盘
    log_path = _REPO_ROOT / "logs" / f"kdata-update-{datetime.now():%Y%m%d-%H%M%S}.log"
    attach_file_log(log_path)
    logger.info(f"[Stock_KData] log file: {log_path}")

    svc = KDataService(root=str(root))
    summary = svc.daily_update(source=args.source)
    print(
        f"Stock_KData update: success={summary.success} failed={len(summary.failed)} elapsed={summary.elapsed_sec:.1f}s"
    )
    print(f"  first={summary.first_date} last={summary.last_date} rows={summary.row_count}")
    return 0


def _update_stock_basic_data(args: argparse.Namespace) -> int:
    from datasource.service.stock_basic_data import StockBasicDataService

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = StockBasicDataService(root=str(root)).daily_update()
    print(f"Stock_Basic_Data update: {result}")
    return 0


def _update_financial_report_income(args: argparse.Namespace) -> int:
    from datasource.service.financial_report_income import (
        FinancialReportIncomeService,
    )

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = FinancialReportIncomeService(root=str(root)).daily_update()
    print(f"Financial_Report_Income update: {result}")
    return 0


def _update_financial_report_balance(args: argparse.Namespace) -> int:
    from datasource.service.financial_report_balance import (
        FinancialReportBalanceService,
    )

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = FinancialReportBalanceService(root=str(root)).daily_update()
    print(f"Financial_Report_Balance update: {result}")
    return 0


def _update_financial_report_cashflow(args: argparse.Namespace) -> int:
    from datasource.service.financial_report_cashflow import (
        FinancialReportCashflowService,
    )

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = FinancialReportCashflowService(root=str(root)).daily_update()
    print(f"Financial_Report_Cashflow update: {result}")
    return 0


def _update_financial_indicator(args: argparse.Namespace) -> int:
    from datasource.service.financial_indicator import (
        FinancialIndicatorService,
    )

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = FinancialIndicatorService(root=str(root)).daily_update()
    print(f"Financial_Indicator update: {result}")
    return 0


def _update_dividend_history(args: argparse.Namespace) -> int:
    from datasource.service.dividend_history import (
        DividendHistoryService,
    )

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = DividendHistoryService(root=str(root)).daily_update()
    print(f"Dividend_History update: {result}")
    return 0


# ---------- Dispatch 表 ----------
# 添加新 type:
#   1. 写一个 _update_<type>(args) -> int handler (name in lower-case, e.g. _update_kdata)
#   2. 在 TYPE_DISPATCH 加一行 "<Type>": "_update_<type_lower>"
# 不需要修改 cmd_data_update 本身.
#
# value 是 handler 函数名 (字符串), 而非函数引用: 这样 monkeypatch.setattr
# 替换 _update_<type> 函数后, 下次 dispatch 自动拿到新引用, 测试友好.

TYPE_DISPATCH: dict[str, str] = {
    "Stock_KData": "_update_kdata",
    "Stock_Basic_Data": "_update_stock_basic_data",
    "Trade_Date": "_update_trade_date",
    "Universe": "_update_universe",
    "Financial_Report_Income": "_update_financial_report_income",
    "Financial_Report_Balance": "_update_financial_report_balance",
    "Financial_Report_Cashflow": "_update_financial_report_cashflow",
    "Financial_Indicator": "_update_financial_indicator",
    "Dividend_History": "_update_dividend_history",
}

# Type alias kept for documentation purposes.
UpdateHandler = "callable"


def cmd_data_init_universe(args: argparse.Namespace) -> int:
    from datasource.service.universe import UniverseService

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    result = UniverseService(root=str(root)).full_init(
        index_name=args.index, backdate_to=args.backdate
    )
    print(f"Universe init: {result}")
    return 0


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
    """从 datas/_Deprecated_raw + datas/_Deprecated_adj 一次性产出 Stock_KData + Stock_Basic_Data.

    两文件均通过 DataFile.upsert(replace) 原子写, 完成后更新 Data_State.json.
    """
    from datasource.migration.legacy_kdata import (
        build_kdata_from_legacy,
        build_stock_basic_data_from_legacy,
    )
    from datasource.schema.stock_basic_data import StockBasicData
    from datasource.schema.stock_kdata import StockKData
    from datasource.storage.io import DataFile
    from datasource.storage.state import StateTracker

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
    # state 路径 = target.parent / _Manifest / Data_State.json (sibling 模式,
    # 与 io.py._assert_schema_fresh 的解析一致).
    state_path = target_kdata.parent / "_Manifest" / "Data_State.json"
    tracker = StateTracker(state_path)

    # ---- Stock_KData ----
    kdata_file = DataFile(
        StockKData, root=target_kdata.parent, storage_path_override=target_kdata.name
    )
    logger.info(f"[migrate-legacy-kdata] Stock_KData: building from {raw_dir} + {adj_dir}")
    kdf = build_kdata_from_legacy(raw_dir, adj_dir)
    logger.info(
        f"[migrate-legacy-kdata] Stock_KData: {len(kdf)} rows, "
        f"{kdf['stock_code'].nunique() if len(kdf) else 0} stocks; writing to {target_kdata}"
    )
    kdata_file.upsert(kdf, conflict="replace")
    kstats = kdata_file.stats()
    tracker.update("Stock_KData", kstats)
    print(f"Stock_KData: {kstats.row_count} rows, {kstats.stock_count} stocks → {target_kdata}")

    # ---- Stock_Basic_Data ----
    sbd_file = DataFile(
        StockBasicData,
        root=target_stock_basic_data.parent,
        storage_path_override=target_stock_basic_data.name,
    )
    logger.info(f"[migrate-legacy-kdata] Stock_Basic_Data: building from {raw_dir}")
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


def cmd_kdata_validate(args: argparse.Namespace) -> int:
    """校验 Stock_KData 完整性 (三维度 + 聚合). 退出码 0/1/2 = ok/warn/fail.

    输出到 stdout 的人类可读摘要 + per-dimension 详情, 便于 cron / 报警脚本消费.
    """
    from datasource.service.stock_kdata import KDataService

    root = Path(args.datasource_dir) if args.datasource_dir else default_datasource_dir()
    svc = KDataService(root=str(root))
    report = svc.validate(null_threshold=args.null_threshold)

    print(f"=== Stock_KData validate ===")
    print(f"root: {root}")
    print(f"rows={report.row_count} stocks={report.stock_count} elapsed={report.elapsed_sec:.2f}s")
    print()

    def _fmt_dim(
        name: str, status: str, missing: int, samples: List[Tuple[str, ...]], reason: Optional[str]
    ) -> None:
        line = f"{name:<16} {status:<5} missing={missing}"
        if reason:
            line += f"  reason={reason}"
        print(line)
        if samples:
            for s in samples[:5]:
                print(f"    sample: {s}")
            if len(samples) > 5:
                print(f"    ... ({len(samples) - 5} more)")

    sc = report.stock_coverage
    _fmt_dim("stock_coverage", sc.status, sc.missing_count, sc.samples, sc.reason)

    dc = report.date_coverage
    _fmt_dim("date_coverage", dc.status, dc.missing_count, dc.samples, dc.reason)

    nr = report.null_ratio
    nr_line = f"{'null_ratio':<16} {nr.status:<5} threshold={nr.threshold}"
    if nr.worst_column is not None:
        nr_line += f"  worst={nr.worst_column}={nr.per_column.get(nr.worst_column, 0):.4f}"
    print(nr_line)
    for col, ratio in sorted(nr.per_column.items(), key=lambda kv: -kv[1])[:5]:
        if ratio > 0:
            print(f"    {col}: {ratio:.4f}")

    print()
    print(f"overall: {report.overall}")
    return {"ok": 0, "warn": 1, "fail": 2}[report.overall]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newbee-datasource",
        description="newbee 数据层 CLI",
    )
    parser.add_argument("--version", action="version", version="newbee-datasource 0.1.0")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # status
    p_status = sub.add_parser("status", help="打印 Data_State + 每类型 stats")
    # default=None → 未传时走 default_datasource_dir() (config.paths.datasource_dir → <repo> fallback)
    p_status.add_argument("--datasource-dir", type=Path, default=None)
    p_status.set_defaults(func=cmd_data_status)

    # update
    p_update = sub.add_parser("update", help="增量拉取")
    p_update.add_argument(
        "--type",
        default="Stock_KData",
        help=(
            "类型名 (Stock_KData / Stock_Basic_Data / Trade_Date / Universe / "
            "Financial_Report_Income / Financial_Report_Balance / Financial_Report_Cashflow / "
            "Financial_Indicator / Dividend_History)"
        ),
    )
    p_update.add_argument(
        "--source",
        default="auto",
        choices=["auto", "sina", "em", "tx", "bs"],
        help=(
            "数据源: auto (4-tier fallback: sina→em→tx→baostock), 或显式指定单源 (sina/em/tx/bs)"
        ),
    )
    p_update.add_argument("--index", default="csi1000", help="universe 指数名 (仅 Universe)")
    p_update.add_argument("--backdate", default=DEFAULT_RESUME_START, help="backdate (仅 Universe)")
    # default=None → 未传时走 default_datasource_dir() (config.paths.datasource_dir → <repo> fallback)
    p_update.add_argument("--datasource-dir", type=Path, default=None)
    p_update.set_defaults(func=cmd_data_update)

    # init-universe
    p_uni = sub.add_parser("init-universe", help="初始化 universe")
    p_uni.add_argument("--index", default="csi1000")
    p_uni.add_argument("--backdate", default=DEFAULT_RESUME_START)
    # default=None → 未传时走 default_datasource_dir() (config.paths.datasource_dir → <repo> fallback)
    p_uni.add_argument("--datasource-dir", type=Path, default=None)
    p_uni.set_defaults(func=cmd_data_init_universe)

    # verify
    p_verify = sub.add_parser("verify", help="跑全部 datasource 测试")
    p_verify.set_defaults(func=cmd_data_verify)

    # migrate-legacy-kdata
    p_mig = sub.add_parser(
        "migrate-legacy-kdata",
        help="从 _Deprecated_raw + _Deprecated_adj 一次性生成 Stock_KData + Stock_Basic_Data",
    )
    p_mig.add_argument("--raw-dir", type=Path, default=Path("datas/_Deprecated_raw"))
    p_mig.add_argument("--adj-dir", type=Path, default=Path("datas/_Deprecated_adj"))
    p_mig.add_argument("--target-kdata", type=Path, default=Path("datas/Stock_KData.parquet"))
    p_mig.add_argument(
        "--target-stock-basic-datas",
        type=Path,
        default=Path("datas/Stock_Basic_Data.parquet"),
    )
    p_mig.set_defaults(func=cmd_data_migrate_legacy_kdata)

    # kdata-validate
    p_val = sub.add_parser(
        "kdata-validate",
        help="校验 Stock_KData 完整性 (stock/date coverage + null ratio); 退出码 0/1/2 = ok/warn/fail",
    )
    # default=None → 未传时走 default_datasource_dir() (config.paths.datasource_dir → <repo> fallback)
    p_val.add_argument("--datasource-dir", type=Path, default=None)
    p_val.add_argument(
        "--null-threshold",
        type=float,
        default=0.01,
        help="OHLCV 列 null 占比上限, 默认 0.01 (1%%)",
    )
    p_val.set_defaults(func=cmd_kdata_validate)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        from config import load_config
        # 走 NEWBEE_CONFIG env 或默认 ./configs/global.yaml
        load_config("/Users/yejingtao/JohnsonProject/Newbee/configs/global.yaml")
    except Exception as exc:
        print(f"WARNING: load_config 失败, fallback 到 PROJECT_ROOT: {exc!r}", file=sys.stderr)

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
