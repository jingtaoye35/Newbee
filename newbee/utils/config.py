"""配置加载工具 (CLI / scripts 共用).

约定:
  - 策略 config: configs/strategies/*.yaml — 含 factor / data / portfolio / cost 段
  - 因子 config: configs/factors/*.yaml — 含 factor / compute / data / evaluation 段

本模块只做 YAML 解析 + 路径解析, 不做语义校验 (那是 engines 的事).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_UNIVERSE = PROJECT_ROOT / "data" / "universe" / "pool.parquet"
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "adj"
DEFAULT_ALPHA_RESULTS = PROJECT_ROOT / "data" / "alpha" / "results"
DEFAULT_PORTFOLIO_RESULTS = PROJECT_ROOT / "data" / "portfolio" / "results"


def load_config(path: str | Path) -> dict[str, Any]:
    """读 YAML 配置.

    Raises:
        FileNotFoundError: config 文件不存在
        yaml.YAMLError: YAML 语法错
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with open(p) as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"配置根节点必须是 dict, 实际是 {type(cfg).__name__}")
    return cfg


def strategy_id(cfg: dict[str, Any]) -> str:
    """从 config 推 strategy_id (name + version)."""
    name = cfg.get("name") or cfg.get("factor", {}).get("name", "unknown")
    version = str(cfg.get("version") or cfg.get("factor", {}).get("version", "1.0"))
    return f"{name}_{version}"


def resolve_data_range(cfg: dict[str, Any]) -> tuple[str, str]:
    """从 cfg.data.start/end 拿 (start, end) ISO 字符串."""
    data = cfg.get("data", {})
    if "start" not in data or "end" not in data:
        raise ValueError("config 缺少 data.start / data.end")
    return str(data["start"]), str(data["end"])