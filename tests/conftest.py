"""tests/conftest.py — pytest fixtures.

The `isolated_config` fixture writes a temporary `global.yaml`,
calls `load_config(path)`, and resets the singleton on teardown so
each test starts with a clean slate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

# Ensure src/ is on sys.path so `from config import ...` works.
import sys

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import load_config, reset_config  # noqa: E402


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


MINIMAL_YAML = """\
project:
  name: test
runtime:
  max_core: 2
  log_level: DEBUG
  log_path: logs
paths:
  datasource_dir: /tmp/test-datas
  report_dir: /tmp/test-report
"""


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Provide a temp YAML file and a loaded config singleton.

    Yields the temp path so tests can inspect / mutate it. Resets the
    singleton on teardown. Clears NEWBEE_CONFIG so the fallback path is
    not used unexpectedly.
    """
    monkeypatch.delenv("NEWBEE_CONFIG", raising=False)
    yaml_path = tmp_path / "global.yaml"
    _write_yaml(yaml_path, MINIMAL_YAML)
    reset_config()
    cfg = load_config(yaml_path)
    assert cfg.runtime.max_core == 2  # sanity
    yield yaml_path
    reset_config()
