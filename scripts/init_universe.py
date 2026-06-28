"""一次性初始化股票池 (M2 datasource 路径).

用法:
    python scripts/init_universe.py [--universe csi1000] [--backdate 2020-01-01]

逻辑:
    1. 调 UniverseService.full_init (内部走 akshare 拉指数 + IPO)
    2. 写 datas/Universe.parquet + 更新 Data_State.json
    3. 打印 universe_sha / 数量
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把项目根加进 sys.path, 让脚本能 import alpha_backend
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from alpha_backend.datasource.registry import REGISTRY  # noqa: E402
from alpha_backend.datasource.service.universe import UniverseService  # noqa: E402
from alpha_backend.datasource.storage.io import DataFile  # noqa: E402

# 指数名 → UniverseService.index_name 取值 (与 fetch_index_constituents 一致)
UNIVERSE_OPTIONS = ["csi1000", "csi500", "csi300", "csi100"]


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化股票池 (datasource)")
    parser.add_argument(
        "--universe",
        default="csi1000",
        choices=UNIVERSE_OPTIONS,
        help="要初始化的 universe (默认 csi1000)",
    )
    parser.add_argument(
        "--backdate",
        default="2020-01-01",
        help="回溯 ipo_date 的默认值 (默认 1990-01-01 由 fetch_ipo_date 决定)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重跑: 先清空现有 Universe.parquet 再重新初始化",
    )
    args = parser.parse_args()

    if args.force:
        # --force: 清空 Universe.parquet (state 由 UniverseService 重建)
        from pathlib import Path as _P

        dtype = REGISTRY.get("Universe")
        path = _P("datas") / dtype.storage_path
        if path.exists():
            print(f"[reset] 删除现有 {path}")
            path.unlink()

    svc = UniverseService()
    summary = svc.full_init(index_name=args.universe, backdate_to=args.backdate)
    print(f"[done] total={summary['total']} added={summary['added']} "
          f"with_ipo={summary['with_ipo']}")

    # 验证
    file_ = DataFile(REGISTRY.get("Universe"))
    stats = file_.stats()
    print(f"[verify] row_count={stats.row_count} stock_count={stats.stock_count} "
          f"file_sha={stats.file_sha256}")


if __name__ == "__main__":
    main()
