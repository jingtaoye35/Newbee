from __future__ import annotations

import dataclasses
import os
import yaml

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


DEFAULT_ALPHA_RESULTS = Path("results/alpha")
DEFAULT_PORTFOLIO_RESULTS = Path("results/portfolio")
DEFAULT_DATA_ROOT = Path("datas")
DEFAULT_UNIVERSE = Path("configs/universe.csv")


# ---- Module state (process-wide singleton) ----

_config: Optional["GlobalConfig"] = None
_config_path: Optional[Path] = None


# ---- Sub-dataclasses ----


@dataclass(frozen=True)
class ProjectMeta:
    name: str  # required
    version: str = "0.1.0"  # optional


@dataclass(frozen=True)
class RuntimeSettings:
    max_core: int  # required
    log_level: str = "INFO"  # optional
    log_path: str = "logs"  # optional (relative to project root; file handler uses this)


@dataclass(frozen=True)
class PathSettings:
    datasource_dir: str  # required (absolute path, preserved verbatim)
    report_dir: str
    universe: str = ""  # optional (absolute path; "" means schema-default fallback)


@dataclass(frozen=True)
class ExternalSettings:
    akshare_endpoint: str = ""  # optional


@dataclass(frozen=True)
class GlobalConfig:
    project: ProjectMeta  # required
    runtime: RuntimeSettings  # required
    paths: PathSettings  # required
    external: ExternalSettings = field(default_factory=ExternalSettings)  # optional


# ---- Validation ----


def _required_fields(cls: type) -> list[str]:
    """
    Field names without a default (i.e. YAML must provide them).
    """
    return [
        f.name
        for f in dataclasses.fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    ]


def _resolve_type(cls: type, field_type: Any) -> Any:
    """
    Resolve a field's declared type to its concrete class.
    With `from __future__ import annotations`, dataclass field types are stored
    as strings. Map known names to their concrete types.
    """
    if isinstance(field_type, str):
        namespace: dict[str, Any] = {
            "ProjectMeta": ProjectMeta,
            "RuntimeSettings": RuntimeSettings,
            "PathSettings": PathSettings,
            "ExternalSettings": ExternalSettings,
            "int": int,
            "str": str,
            "float": float,
            "bool": bool,
        }
        if field_type in namespace:
            return namespace[field_type]
    return field_type


def _try_coerce(field_name: str, declared_type: Any, value: Any) -> Any:
    """Coerce a YAML value to the declared field type. Raise on failure.

    Special handling for `bool`: bool("anything") is True, so we require
    exact-type match (or a YAML-true/false string) for booleans.
    """
    if declared_type is bool:
        # bool() is too permissive; require real bools.
        if isinstance(value, bool):
            return value
        raise ValueError(
            f"field {field_name!r}: expected bool, got {type(value).__name__}: {value!r}"
        )

    if isinstance(value, declared_type):
        return value

    try:
        return declared_type(value)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"field {field_name!r}: cannot coerce {value!r} to {declared_type.__name__}"
        ) from e


def _validate_and_build(cls: type, data: Any) -> Any:
    """Recursively coerce a dict into a dataclass instance with strict checks.

    Raises:
        TypeError: data is not a dict at a level expecting one.
        ValueError: unknown key, missing required key, or bad leaf value.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"_validate_and_build: expected dataclass, got {cls!r}")

    if not isinstance(data, dict):
        raise TypeError(f"Expected dict for {cls.__name__}, got {type(data).__name__}: {data!r}")

    expected = {f.name for f in dataclasses.fields(cls)}
    actual = set(data.keys())

    unknown = sorted(actual - expected)
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown field(s): {unknown}")

    missing_required = [name for name in sorted(expected - actual) if name in _required_fields(cls)]
    if missing_required:
        raise ValueError(f"{cls.__name__}: missing required field(s): {missing_required}")

    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        ftype = _resolve_type(cls, f.type)
        if dataclasses.is_dataclass(ftype):
            kwargs[f.name] = _validate_and_build(ftype, value)
        else:
            kwargs[f.name] = _try_coerce(f.name, ftype, value)

    return cls(**kwargs)


# ---- Loader ----


def _resolve_load_target(path: Optional[str | Path]) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("NEWBEE_CONFIG")
    if env:
        return Path(env)
    print("config:", Path("."))
    return Path("./configs/global.yaml")


def load_config(path: str | Path | None = None) -> GlobalConfig:
    global _config
    global _config_path

    if _config is not None:
        return _config

    resolved = _resolve_load_target(path)

    if not resolved.exists():
        raise FileNotFoundError(
            f"config file not found: {resolved} "
            f"(checked explicit path, NEWBEE_CONFIG env, "
            f"and default ./configs/global.yaml)"
        )

    raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"top-level YAML in {resolved} must be a mapping, got {type(raw).__name__}")

    _config = _validate_and_build(GlobalConfig, raw)
    _config_path = resolved.resolve()
    return _config


# ---- Accessors ----


def get_config() -> GlobalConfig:
    """Return the loaded singleton. Raises if load_config() has not been called."""
    if _config is None:
        raise RuntimeError("config not initialized; call load_config() at program entry first")
    return _config


def get_config_path() -> Optional[Path]:
    """Return the absolute path the singleton was loaded from, or None."""
    return _config_path


def reset_config() -> None:
    """Clear the singleton. Test-only hook."""
    global _config, _config_path
    _config = None
    _config_path = None


__all__ = [
    # Constants
    "DEFAULT_ALPHA_RESULTS",
    "DEFAULT_DATA_ROOT",
    "DEFAULT_PORTFOLIO_RESULTS",
    "DEFAULT_UNIVERSE",
    "ExternalSettings",
    "GlobalConfig",
    "PathSettings",
    "ProjectMeta",
    "RuntimeSettings",
    # Public API
    "get_config",
    "get_config_path",
    "load_config",
    "reset_config",
]
