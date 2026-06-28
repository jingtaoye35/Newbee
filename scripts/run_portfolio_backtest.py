"""组合回测入口 (M2 datasource 路径).

Thin wrapper around `alpha_backend.cli.main(['backtest', ...])`. 与
`scripts/run_portfolio_backtest.py` 的区别:
  - 不再调 `load_bars_from_parquet`, 走 `alpha_backend.datasource.storage.bars_adapter`
  - 数据来源为 `datas/KData.parquet` (long format)

用法:
  python scripts/run_portfolio_backtest.py --config configs/strategies/momentum_baseline.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.cli import main as newbee_main  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Portfolio backtest (alpha_backend CLI wrapper)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--universe",
        type=Path,
        default=PROJECT_ROOT / "datas" / "Universe.parquet",
    )
    parser.add_argument("--datas-root", type=Path, default=PROJECT_ROOT / "datas")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PROJECT_ROOT / "datas" / "portfolio" / "results",
    )
    args = parser.parse_args()

    argv = [
        "backtest",
        str(args.config),
        "--universe", str(args.universe),
        "--datas-root", str(args.data_root),
        "--out-dir", str(args.out_dir),
    ]
    return newbee_main(argv)


if __name__ == "__main__":
    sys.exit(main())