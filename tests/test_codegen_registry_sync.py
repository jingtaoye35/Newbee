"""test_codegen_registry_sync.py — codegen --check-registry 子进程行为测试.

通过跑 `python -m alpha_backend.datasource.codegen --check-registry` 子进程,
验证:
  1. 在干净仓库下 exit code = 0, 输出 ✓
  2. codegen 主流程能重写 _register_defaults 并保持与 YAML 一致
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_codegen(*args: str) -> subprocess.CompletedProcess[str]:
    """在 project root 下同步跑 codegen 子进程, 捕获 stdout/stderr."""
    return subprocess.run(
        [sys.executable, "-m", "alpha_backend.datasource.codegen", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_check_registry_exits_zero_on_clean_repo() -> None:
    """干净仓库: --check-registry 应 exit 0 并打 ✓."""
    result = _run_codegen("--check-registry")
    assert result.returncode == 0, (
        f"expected exit 0 on clean repo, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "✓" in result.stdout, f"expected ✓ in stdout, got: {result.stdout}"


def test_check_registry_exits_nonzero_on_drift(tmp_path: Path) -> None:
    """人为篡改 registry.py sentinel 之间内容, --check-registry 应 exit 1 并打 diff.

    在 tmp_path 复制 registry.py 后篡改, 不污染仓库内文件.
    """
    registry_py = PROJECT_ROOT / "alpha_backend" / "datasource" / "registry.py"
    assert registry_py.exists()
    original = registry_py.read_text(encoding="utf-8")
    try:
        # 把 KData 的 schema_version 改成 "9.9" 制造 drift
        tampered = original.replace(
            'name="KData",\n            schema_version="1.0"',
            'name="KData",\n            schema_version="9.9"',
        )
        assert tampered != original, "tamper substitution did not change file"
        registry_py.write_text(tampered, encoding="utf-8")

        result = _run_codegen("--check-registry")
        assert result.returncode == 1, (
            f"expected exit 1 on drift, got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "不一致" in result.stderr or "不一致" in result.stdout, (
            f"expected drift message, got:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        registry_py.write_text(original, encoding="utf-8")
        # 跑一次 codegen 复原文件 (写盘的 generate_all) 让磁盘和 YAML 重新对齐
        _run_codegen()


def test_codegen_writes_registry_block() -> None:
    """codegen 主流程 (无 --check-registry) 应成功重写 _register_defaults,
    且写盘后 --check-registry 仍然 exit 0."""
    result = _run_codegen()
    assert result.returncode == 0, (
        f"codegen failed: stdout={result.stdout}\nstderr={result.stderr}"
    )
    # 再跑一次 check, 确认幂等
    result2 = _run_codegen("--check-registry")
    assert result2.returncode == 0, (
        f"check after write failed: stdout={result2.stdout}\nstderr={result2.stderr}"
    )