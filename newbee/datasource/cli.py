"""newbee datasource CLI.

子命令:
  - data status: 打印 Data_State.json + 每类型 stats
  - data update [--type KData|...] [--source sina|em|tx]: 增量拉取
  - data init-universe [--index csi1000] [--backdate 2020-01-01]: 初始化 universe
  - data codegen: 重跑 codegen
  - data verify: 跑 dict_sync + storage_io + state_tracker 测试
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def cmd_data_status(args: argparse.Namespace) -> int:
    from newbee.datasource.registry import REGISTRY
    from newbee.datasource.storage.io import DataFile
    from newbee.datasource.storage.state import StateTracker

    root = Path(args.data_root) if args.data_root else Path.cwd()
    tracker = StateTracker(root / "data" / "_Manifest" / "Data_State.json")
    states = tracker.read()
    print(f"=== newbee datasource status ===")
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
    from newbee.datasource.registry import REGISTRY

    type_name = args.type
    dtype = REGISTRY.get(type_name)

    root = Path(args.data_root) if args.data_root else Path.cwd()
    if dtype.name == "KData":
        from newbee.datasource.service.kdata import KDataService

        svc = KDataService(root=str(root))
        summary = svc.daily_update(today=date.today(), source=args.source)
        print(f"KData update: success={summary.success} failed={len(summary.failed)} elapsed={summary.elapsed_sec:.1f}s")
        print(f"  first={summary.first_date} last={summary.last_date} rows={summary.row_count}")
    elif dtype.name == "Trade_Status":
        from newbee.datasource.service.trade_status import TradeStatusService

        result = TradeStatusService(root=str(root)).daily_update(today=date.today())
        print(f"Trade_Status update: {result}")
    elif dtype.name == "Stock_Basic_Data":
        from newbee.datasource.service.stock_basic_data import StockBasicDataService

        result = StockBasicDataService(root=str(root)).daily_update(today=date.today())
        print(f"Stock_Basic_Data update: {result}")
    elif dtype.name == "Universe":
        from newbee.datasource.service.universe import UniverseService

        result = UniverseService(root=str(root)).full_init(
            index_name=args.index, backdate_to=args.backdate
        )
        print(f"Universe init: {result}")
    else:
        print(f"ERROR: unknown type {type_name!r}", file=sys.stderr)
        return 1
    return 0


def cmd_data_init_universe(args: argparse.Namespace) -> int:
    from newbee.datasource.service.universe import UniverseService

    root = Path(args.data_root) if args.data_root else Path.cwd()
    result = UniverseService(root=str(root)).full_init(
        index_name=args.index, backdate_to=args.backdate
    )
    print(f"Universe init: {result}")
    return 0


def cmd_data_codegen(args: argparse.Namespace) -> int:
    from newbee.datasource import codegen

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newbee-datasource",
        description="newbee 数据层 CLI",
    )
    parser.add_argument("--version", action="version", version="newbee-datasource 0.1.0")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # status
    p_status = sub.add_parser("status", help="打印 Data_State + 每类型 stats")
    p_status.add_argument("--data-root", type=Path, default=Path.cwd())
    p_status.set_defaults(func=cmd_data_status)

    # update
    p_update = sub.add_parser("update", help="增量拉取")
    p_update.add_argument("--type", default="KData", help="类型名 (KData / Trade_Status / Stock_Basic_Data / Universe)")
    p_update.add_argument("--source", default="sina", choices=["sina", "em", "tx"])
    p_update.add_argument("--index", default="csi1000", help="universe 指数名 (仅 Universe)")
    p_update.add_argument("--backdate", default="2020-01-01", help="backdate (仅 Universe)")
    p_update.add_argument("--data-root", type=Path, default=Path.cwd())
    p_update.set_defaults(func=cmd_data_update)

    # init-universe
    p_uni = sub.add_parser("init-universe", help="初始化 universe")
    p_uni.add_argument("--index", default="csi1000")
    p_uni.add_argument("--backdate", default="2020-01-01")
    p_uni.add_argument("--data-root", type=Path, default=Path.cwd())
    p_uni.set_defaults(func=cmd_data_init_universe)

    # codegen
    p_codegen = sub.add_parser("codegen", help="跑 codegen")
    p_codegen.set_defaults(func=cmd_data_codegen)

    # verify
    p_verify = sub.add_parser("verify", help="跑全部 datasource 测试")
    p_verify.set_defaults(func=cmd_data_verify)

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