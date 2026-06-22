"""tests/test_config.py — coverage for src/config.py (Pattern B runtime)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from config import (
    DEFAULT_DATA_ROOT,
    DEFAULT_UNIVERSE,
    GlobalConfig,
    get_config,
    get_config_path,
    load_config,
    reset_config,
)


# ---- Resolution order ----


def test_load_from_explicit_path(tmp_path: Path) -> None:
    yaml = tmp_path / "cfg.yaml"
    yaml.write_text(
        "project: {name: explicit}\n"
        "runtime: {max_core: 8}\n"
        "paths: {datasource_dir: /tmp/explicit-datas, report_dir: /tmp/explicit-report}\n",
        encoding="utf-8",
    )
    reset_config()
    cfg = load_config(yaml)
    assert cfg.runtime.max_core == 8
    assert get_config_path() == yaml.resolve()


def test_load_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWBEE_CONFIG", raising=False)
    yaml = tmp_path / "env.yaml"
    yaml.write_text(
        "project: {name: env}\n"
        "runtime: {max_core: 16}\n"
        "paths: {datasource_dir: /tmp/env-datas, report_dir: /tmp/env-report}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWBEE_CONFIG", str(yaml))
    reset_config()
    cfg = load_config()
    assert cfg.runtime.max_core == 16


def test_load_default_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When NEWBEE_CONFIG is unset and no path arg, the loader uses
    ./configs/global.yaml. To exercise this, monkeypatch CWD to a temp
    dir containing a fresh global.yaml."""
    monkeypatch.delenv("NEWBEE_CONFIG", raising=False)
    cwd = Path.cwd()
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "configs").mkdir()
            (Path(td) / "configs" / "global.yaml").write_text(
                "project: {name: default}\n"
                "runtime: {max_core: 3}\n"
                "paths: {datasource_dir: /tmp/default-datas, report_dir: /tmp/default-report}\n",
                encoding="utf-8",
            )
            os.chdir(td)
            reset_config()
            cfg = load_config()
            assert cfg.runtime.max_core == 3
            assert cfg.project.name == "default"
    finally:
        os.chdir(cwd)


def test_missing_file_raises(tmp_path: Path) -> None:
    reset_config()
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_missing_default_file_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NEWBEE_CONFIG", raising=False)
    cwd = Path.cwd()
    try:
        os.chdir(tmp_path)  # no configs/global.yaml here
        reset_config()
        with pytest.raises(FileNotFoundError):
            load_config()
    finally:
        os.chdir(cwd)


# ---- Strict validation ----


def test_unknown_top_level_field(tmp_path: Path) -> None:
    yaml = tmp_path / "bad.yaml"
    yaml.write_text(
        "project: {name: t}\nruntime: {max_core: 1}\npaths: {datasource_dir: /x}\nbanana: 1\n",
        encoding="utf-8",
    )
    reset_config()
    with pytest.raises(ValueError, match="banana"):
        load_config(yaml)


def test_unknown_nested_field(tmp_path: Path) -> None:
    yaml = tmp_path / "bad.yaml"
    yaml.write_text(
        "project: {name: t}\nruntime: {max_core: 1, BAD_FIELD: x}\npaths: {datasource_dir: /x}\n",
        encoding="utf-8",
    )
    reset_config()
    with pytest.raises(ValueError, match="BAD_FIELD"):
        load_config(yaml)


def test_missing_required_field(tmp_path: Path) -> None:
    yaml = tmp_path / "bad.yaml"
    # missing `runtime`
    yaml.write_text(
        "project: {name: t}\npaths: {datasource_dir: /x}\n",
        encoding="utf-8",
    )
    reset_config()
    with pytest.raises(ValueError, match="runtime"):
        load_config(yaml)


def test_type_coercion_failure(tmp_path: Path) -> None:
    yaml = tmp_path / "bad.yaml"
    yaml.write_text(
        "project: {name: t}\nruntime: {max_core: not_an_int}\npaths: {datasource_dir: /x}\n",
        encoding="utf-8",
    )
    reset_config()
    with pytest.raises((TypeError, ValueError)):
        load_config(yaml)


# ---- Accessor semantics ----


def test_get_before_load_raises() -> None:
    reset_config()
    with pytest.raises(RuntimeError, match="load_config"):
        get_config()


def test_get_config_path_before_load_is_none() -> None:
    reset_config()
    assert get_config_path() is None


def test_get_config_path_after_reset_is_none(isolated_config: Path) -> None:
    # isolated_config fixture loaded us; now reset and check.
    reset_config()
    assert get_config_path() is None
    assert pytest.raises(RuntimeError, get_config)


# ---- Idempotency ----


def test_load_is_idempotent(isolated_config: Path) -> None:
    a = get_config()
    b = load_config()
    assert a is b


# ---- Optional / default values ----


def test_optional_field_defaults(tmp_path: Path) -> None:
    yaml = tmp_path / "min.yaml"
    yaml.write_text(
        "project: {name: t}\nruntime: {max_core: 1}\npaths: {datasource_dir: /x, report_dir: /r}\n",
        encoding="utf-8",
    )
    reset_config()
    cfg = load_config(yaml)
    assert cfg.runtime.log_level == "INFO"
    assert cfg.runtime.log_path == "logs"
    assert cfg.paths.universe == ""
    assert cfg.external.akshare_endpoint == ""


def test_custom_log_path(tmp_path: Path) -> None:
    yaml = tmp_path / "custom_log.yaml"
    yaml.write_text(
        "project: {name: t}\n"
        "runtime: {max_core: 1, log_path: /var/log/newbee}\n"
        "paths: {datasource_dir: /x, report_dir: /r}\n",
        encoding="utf-8",
    )
    reset_config()
    cfg = load_config(yaml)
    assert cfg.runtime.log_path == "/var/log/newbee"


def test_yaml_path_values_preserved_verbatim(tmp_path: Path) -> None:
    """Spec: datasource_dir in YAML must NOT be ~-expanded or resolved()."""
    yaml = tmp_path / "verbatim.yaml"
    yaml.write_text(
        "project: {name: t}\n"
        "runtime: {max_core: 1}\n"
        "paths: {datasource_dir: '~/weird/path/../datas', report_dir: '~/r'}\n",
        encoding="utf-8",
    )
    reset_config()
    cfg = load_config(yaml)
    assert cfg.paths.datasource_dir == "~/weird/path/../datas"


# ---- Default path constants exported ----


def test_default_path_constants_exist() -> None:
    """cli.py imports these as argparse defaults."""
    assert DEFAULT_DATA_ROOT == Path("datas")
    assert DEFAULT_UNIVERSE == Path("configs/universe.csv")
