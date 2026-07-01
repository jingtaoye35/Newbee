"""Alpha 回测入口 (M2 datasource 路径).

Thin wrapper around `alpha_backend.cli.main(['alpha', ...])`. 与 `scripts/run_alpha_backtest.py`
的区别:
  - 不再调 `load_bars_from_parquet`, 走 `alpha_backend.datasource.storage.bars_adapter`
  - 数据来源为 `datas/KData.parquet` (long format), 默认 root=datas

用法:
  python scripts/run_alpha_backtest.py --config configs/factors/momentum_20.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.cli import main as newbee_main  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpha backtest (alpha_backend CLI wrapper)")
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
        default=PROJECT_ROOT / "datas" / "alpha" / "results",
    )
    args = parser.parse_args()

    argv = [
        "alpha",
        str(args.config),
        "--universe", str(args.universe),
        "--datas-root", str(args.datas_root),
        "--out-dir", str(args.out_dir),
    ]
    return newbee_main(argv)


if __name__ == "__main__":
    sys.exit(main())