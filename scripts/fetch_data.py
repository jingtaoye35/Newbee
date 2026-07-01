"""批量拉取全市场数据 (M2 datasource 路径).

Thin wrapper around `alpha_backend.datasource.cli update` — 拉指定类型 (KData /
Stock_Basic_Data / Trade_Status) 到最新.

用法:
    python scripts/fetch_data.py [--type KData] [--source sina]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.datasource import cli as ds_cli  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="批量拉取数据 (datasource.update)")
    parser.add_argument(
        "--type",
        default="KData",
        choices=["KData", "Stock_Basic_Data", "Trade_Status", "Universe"],
        help="要更新的类型 (默认 KData)",
    )
    parser.add_argument(
        "--source",
        default="sina",
        choices=["sina", "em", "tx"],
        help="数据源 (默认 sina)",
    )
    parser.add_argument(
        "--datas-root",
        type=Path,
        default=PROJECT_ROOT / "datas",
        help=f"datas 根目录 (默认 {PROJECT_ROOT / 'datas'})",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划")
    args = parser.parse_args()

    argv = ["update", "--type", args.type, "--source", args.source,
            "--datas-root", str(args.datas_root)]
    if args.dry_run:
        argv.append("--dry-run")
    return ds_cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())