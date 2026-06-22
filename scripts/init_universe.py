"""一次性初始化中证 1000 股票池.

用法:
    python scripts/init_universe.py [--universe csi1000] [--backdate 2020-01-01]

逻辑:
    1. 用 akshare 拉中证 1000 成分股
    2. 调 StockPool.add_index 一次性添加
    3. 1000 只股票 added_at 全部回溯到 --backdate (默认 2020-01-01)
    4. 验证 pool.size() == 1000
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# 把项目根加进 sys.path, 让脚本能 import newbee
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import akshare as ak  # noqa: E402

from newbee.data.universe import StockPool  # noqa: E402

# 中证指数代码映射 (M1 起步: csi1000; 未来可扩 csi2000/csi500)
UNIVERSE_CODE_MAP = {
    "csi1000": "000852",   # 中证 1000
    "csi500":  "000905",   # 中证 500
    "csi300":  "000300",   # 沪深 300
    "csi100":  "000903",   # 中证 100
}


def fetch_constituents(universe: str) -> list[str]:
    """从 akshare 拉指数成分股 stock_id 列表."""
    if universe not in UNIVERSE_CODE_MAP:
        raise ValueError(
            f"未知 universe: {universe}. 可选: {list(UNIVERSE_CODE_MAP.keys())}"
        )

    code = UNIVERSE_CODE_MAP[universe]
    print(f"[fetch] 拉取 {universe} ({code}) 成分股...")
    df = ak.index_stock_cons_csindex(symbol=code)

    # 字段: '成分券代码' (6 位字符串)
    stock_ids = df["成分券代码"].astype(str).str.zfill(6).tolist()
    print(f"[fetch] 拿到 {len(stock_ids)} 只")
    return stock_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化自建股票池")
    parser.add_argument(
        "--universe",
        default="csi1000",
        choices=list(UNIVERSE_CODE_MAP.keys()),
        help="要初始化的 universe (默认 csi1000)",
    )
    parser.add_argument(
        "--backdate",
        default="2020-01-01",
        help="回溯 added_at 的日期 (默认 2020-01-01)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重跑: 先清空现有池子再重新初始化",
    )
    args = parser.parse_args()

    backdate = date.fromisoformat(args.backdate)
    pool = StockPool.load()

    if pool.size() > 0:
        if not args.force:
            print(f"[skip] 池子已有 {pool.size()} 只股票, 用 --force 重跑")
            return
        # --force: 清空现有 pool 后重新初始化
        print(f"[reset] 现有池子 {pool.size()} 只将被清空 (--force)")
        pool_path = pool.path
        manifest_path = pool.manifest_path
        if pool_path.exists():
            pool_path.unlink()
        if manifest_path.exists():
            manifest_path.unlink()
        pool = StockPool.load()
        print("[reset] ✓ 池子已清空")

    stock_ids = fetch_constituents(args.universe)

    print(f"[init] 添加 {len(stock_ids)} 只到池子, backdate={backdate}")
    pool.add_index(
        name=args.universe,
        stock_ids=stock_ids,
        backdate_to=backdate,
    )

    # 验证
    final_size = pool.size()
    active_now = pool.active_count(date(2024, 1, 15))
    print(f"[verify] pool.size() = {final_size}")
    print(f"[verify] pool.active_count(2024-01-15) = {active_now}")
    assert final_size == len(stock_ids), f"size 不匹配: {final_size} vs {len(stock_ids)}"
    print("[done] ✓ 中证 1000 池子初始化完成")


if __name__ == "__main__":
    main()
