"""newbee 顶层 CLI.

用法:
  newbee --help
  newbee backtest <config.yaml>              # 组合回测
  newbee alpha   <config.yaml>              # Alpha 回测 (IC, decile)
  newbee data status                         # 查看数据覆盖范围
  newbee data update [--dry-run]             # 增量拉取数据
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from newbee.utils.config import (
    DEFAULT_ALPHA_RESULTS,
    DEFAULT_DATA_ROOT,
    DEFAULT_PORTFOLIO_RESULTS,
    DEFAULT_UNIVERSE,
    load_config,
    resolve_data_range,
    strategy_id,
)
from newbee.utils import logger as nb_logger

logger = logging.getLogger(__name__)


# ---------- 子命令实现 ----------


def cmd_alpha(args: argparse.Namespace) -> int:
    """Alpha 回测 (IC / RankIC / decile)."""
    from newbee import alpha_store
    from newbee.datasource.storage.bars_adapter import load_bars
    from newbee.datasource.storage.pool_adapter import StockPool
    from newbee.engines.backtest_alpha import (
        forward_returns_from_prices,
        run_alpha_backtest_from_store,
    )

    cfg = load_config(args.config)
    sid = strategy_id(cfg)
    print(f"Config: {args.config}")
    print(f"Strategy id: {sid}")

    # 数据
    pool = StockPool.load(args.universe)
    stock_ids = pool.export()["stock_code"].tolist()
    print(f"Pool: {pool.size()} stocks")

    start_s, end_s = resolve_data_range(cfg)
    start = date.fromisoformat(start_s)
    end = date.fromisoformat(end_s)
    # KData 在 args.data_root 之下 (data/KData.parquet)
    bars = load_bars(
        stock_codes=stock_ids,
        start=start, end=end,
        kind="adj",
        root=args.data_root.parent,
    )
    if not bars.dates:
        print(f"ERROR: 没有可用行情 (root={args.data_root}, "
              f"range={start}~{end})", file=sys.stderr)
        return 1
    print(f"Bars: T={len(bars.dates)}, N={bars.N}, "
          f"range={bars.dates[0]} ~ {bars.dates[-1]}")

    # alpha store 校验
    root = args.data_root.parent  # data/ 目录 (alpha_store 默认放这)
    existing_dates = alpha_store.list_dates(strategy_id=sid, root=root)
    if not existing_dates:
        print(f"WARNING: alpha_store 为空: data/alpha/{sid}/")
        print("         先跑: python docs/01_first_factor.py")
        return 1

    # forward return
    prices = bars.adj_close
    horizon = cfg.get("evaluation", {}).get("horizon", 20)

    result = run_alpha_backtest_from_store(
        strategy_id=sid,
        forward_returns=forward_returns_from_prices(prices, horizon=horizon),
        dates=list(bars.dates),
        n_groups=cfg.get("evaluation", {}).get("n_groups", 10),
        min_valid=cfg.get("evaluation", {}).get("min_valid", 30),
        root=str(root),
    )

    # 写结果
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{sid}_summary.json"
    import json
    with open(out_path, "w") as f:
        json.dump({
            "strategy_id": sid,
            "config": str(args.config),
            "summary": result.summary(),
        }, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n=== Alpha Backtest Result ===")
    print(result.summary())
    print(f"\nSaved: {out_path}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """组合回测 (Phase B)."""
    from newbee import alpha_store
    from newbee.datasource.storage.bars_adapter import load_bars
    from newbee.datasource.storage.pool_adapter import StockPool
    from newbee.engines.backtest_portfolio import run_portfolio_backtest
    from newbee.factors.classic.momentum import momentum_20, momentum_60, rev_5
    from newbee.factors.pipeline import compute_factor_panel
    from newbee.portfolio import CostModel

    FACTOR_REGISTRY = {
        "momentum_20": momentum_20,
        "momentum_60": momentum_60,
        "rev_5": rev_5,
    }

    cfg = load_config(args.config)
    sid = strategy_id(cfg)
    print(f"Config: {args.config}")
    print(f"Strategy id: {sid}")

    pool = StockPool.load(args.universe)
    stock_ids = pool.export()["stock_code"].tolist()
    start_s, end_s = resolve_data_range(cfg)
    start = date.fromisoformat(start_s)
    end = date.fromisoformat(end_s)
    bars = load_bars(
        stock_codes=stock_ids,
        start=start, end=end,
        kind="adj",
        root=args.data_root.parent,
    )
    if not bars.dates:
        print(f"ERROR: 没有可用行情 (root={args.data_root}, "
              f"range={start}~{end})", file=sys.stderr)
        return 1
    print(f"Bars: T={len(bars.dates)}, N={bars.N}")

    # 因子
    factor_name = cfg["factor"]["name"]
    factor_func = FACTOR_REGISTRY.get(factor_name)
    if factor_func is None:
        print(f"ERROR: 未知因子 {factor_name} (registry: "
              f"{sorted(FACTOR_REGISTRY)})", file=sys.stderr)
        return 1
    prices = bars.adj_close
    print(f"计算因子: {factor_name} ...")
    import numpy as np
    # compute_factor_panel 返回 DataFrame (T, N), 取 values
    alpha_df = compute_factor_panel(factor_func, prices, list(bars.dates))
    alpha_panel = alpha_df.to_numpy()
    print(f"Alpha shape: {alpha_panel.shape}, non-NaN: {(~np.isnan(alpha_panel)).sum()}")

    # 写 alpha_store (cache)
    root = args.data_root.parent  # data/ 目录
    for t, d in enumerate(bars.dates):
        if not np.isnan(alpha_panel[t]).all():
            alpha_store.write(
                strategy_id=sid, asof=d, scores=alpha_panel[t],
                universe_size=pool.size(), root=root,
            )
    print(f"Alpha store: {len(alpha_store.list_dates(strategy_id=sid, root=root))} dates")

    # 成本
    cost_cfg = cfg.get("cost", {})
    cost_model = CostModel(
        commission_rate=cost_cfg.get("commission_rate", 0.0005),
        slippage_rate=cost_cfg.get("slippage_rate", 0.001),
    )

    # 组合
    portfolio_cfg = cfg.get("portfolio", {})
    result = run_portfolio_backtest(
        prices=prices, dates=list(bars.dates), pool=pool,
        alpha_scores=alpha_panel,
        rebalance_freq=portfolio_cfg.get("rebalance_freq", 20),
        lookback_cov=portfolio_cfg.get("lookback_cov", 60),
        risk_aversion=portfolio_cfg.get("risk_aversion", 1.0),
        max_turnover=portfolio_cfg.get("max_turnover", 0.3),
        max_weight=portfolio_cfg.get("max_weight", 0.05),
        cost_model=cost_model,
        optimizer=portfolio_cfg.get("optimizer", "mean_variance"),
    )

    # 输出
    args.out_dir.mkdir(parents=True, exist_ok=True)
    nav_path = args.out_dir / f"{sid}_nav.parquet"
    result.nav.to_frame("nav").to_parquet(nav_path)

    summary = result.summary()
    summary_path = args.out_dir / f"{sid}_summary.json"
    import json
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print(f"\n=== Portfolio Backtest Result ===")
    print(f"Strategy: {sid}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nSaved: {nav_path}")
    print(f"Summary: {summary_path}")
    return 0


# ---------- 子命令: data ----------


def cmd_data_status(args: argparse.Namespace) -> int:
    """`newbee data status` — 转发到 newbee.datasource.cli status."""
    from newbee.datasource import cli as ds_cli

    argv = ["status"]
    if args.data_root:
        argv += ["--data-root", str(args.data_root)]
    return ds_cli.main(argv)


def cmd_data_update(args: argparse.Namespace) -> int:
    """`newbee data update` — 转发到 newbee.datasource.cli update."""
    from newbee.datasource import cli as ds_cli

    argv = ["update"]
    if args.type:
        argv += ["--type", args.type]
    if args.source:
        argv += ["--source", args.source]
    if args.data_root:
        argv += ["--data-root", str(args.data_root)]
    return ds_cli.main(argv)


# ---------- argparse ----------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="newbee",
        description="newbee 量化交易平台 CLI (M1 骨架)",
    )
    parser.add_argument("--version", action="version", version="newbee 0.1.0")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # shared args via parents=[...] — 但 parents 在 --help 时会重复, 故用显式 add
    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE,
                        help=f"股票池 parquet (默认 {DEFAULT_UNIVERSE})")
        sp.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                        help=f"行情数据根目录 (默认 {DEFAULT_DATA_ROOT})")
        sp.add_argument("-v", "--verbose", action="store_true",
                        help="DEBUG 日志")

    # alpha
    p_alpha = sub.add_parser("alpha", help="Alpha 回测 (IC / RankIC / decile)",
                             description="从 alpha_store 读 alpha 矩阵 + 价格算 forward return, "
                                         "输出 IC / RankIC / decile 收益汇总.")
    p_alpha.add_argument("config", type=Path, nargs="?",
                         help="YAML 配置路径 (e.g. configs/factors/momentum_20.yaml)")
    p_alpha.add_argument("--out-dir", type=Path, default=DEFAULT_ALPHA_RESULTS,
                         help=f"结果输出目录 (默认 {DEFAULT_ALPHA_RESULTS})")
    add_common(p_alpha)
    p_alpha.set_defaults(func=cmd_alpha)

    # backtest
    p_bt = sub.add_parser("backtest", help="组合回测 (Phase B 状态机)",
                          description="读 YAML → 算因子 → 写 alpha_store → 跑组合回测 "
                                      "(调仓 + 成本 + 约束).")
    p_bt.add_argument("config", type=Path, nargs="?",
                      help="YAML 配置路径 (e.g. configs/strategies/momentum_baseline.yaml)")
    p_bt.add_argument("--out-dir", type=Path, default=DEFAULT_PORTFOLIO_RESULTS,
                      help=f"结果输出目录 (默认 {DEFAULT_PORTFOLIO_RESULTS})")
    add_common(p_bt)
    p_bt.set_defaults(func=cmd_backtest)

    # data (status / update)
    p_data = sub.add_parser("data", help="数据层管理 (status / update)",
                            description="查询每类数据的覆盖范围, 或执行增量拉取.")
    p_data_sub = p_data.add_subparsers(dest="data_command", metavar="<data_command>")

    # data status
    p_ds = p_data_sub.add_parser("status", help="打印每类数据的 first/last/days/rows",
                                 description="读取 fetch_state.json, 打印覆盖范围表.")
    p_ds.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                      help=f"data 根目录 (默认 {DEFAULT_DATA_ROOT})")
    p_ds.set_defaults(func=cmd_data_status)

    # data update
    p_du = p_data_sub.add_parser("update", help="增量拉取缺失数据",
                                 description="对每类数据计算 resume 区间, "
                                             "追加最新缺失日期.")
    p_du.add_argument("--type", default="KData",
                      choices=["KData", "Trade_Status", "Stock_Basic_Data", "Universe"],
                      help="要更新的类型 (默认 KData)")
    p_du.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT,
                      help=f"data 根目录 (默认 {DEFAULT_DATA_ROOT})")
    p_du.add_argument("--source", default="sina",
                      choices=["sina", "em", "tx"],
                      help="数据源 (默认 sina)")
    p_du.add_argument("--dry-run", action="store_true",
                      help="只打印计划, 不实际下载, 不写 fetch_state")
    p_du.add_argument("--no-progress", action="store_true",
                      help="不打 tqdm 进度条")
    p_du.set_defaults(func=cmd_data_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 日志
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not getattr(args, "func", None):
        # 没指定子命令 → 打顶层 help
        parser.print_help()
        return 0

    # data 子命令未指定 status/update → 打 data help
    if getattr(args, "command", None) == "data" and not getattr(args, "data_command", None):
        sub_action = next(
            (a for a in parser._actions
             if isinstance(a, argparse._SubParsersAction) and a.dest == "data_command"),
            None,
        )
        if sub_action:
            sub_action.choices["status"].print_help()
        print("\nERROR: newbee data 需要 status / update 子命令.", file=sys.stderr)
        return 2

    # alpha / backtest 子命令缺 config → 友好错误
    if getattr(args, "command", None) in ("alpha", "backtest") and not getattr(args, "config", None):
        sub_action = next(
            (a for a in parser._actions
             if isinstance(a, argparse._SubParsersAction)), None
        )
        if sub_action and args.command in sub_action.choices:
            sub_action.choices[args.command].print_help()
        else:
            parser.print_help()
        print("\nERROR: 缺少 config 路径参数.", file=sys.stderr)
        return 2

    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyError as e:
        print(f"ERROR: 配置缺少键 {e}", file=sys.stderr)
        return 1
    except Exception as e:
        logger.exception("回测失败")
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())