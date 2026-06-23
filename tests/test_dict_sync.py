"""test_dict_sync.py — YAML ↔ Pydantic ↔ markdown 三向同步校验.

校验:
  1. 每个 data_dict/*.yaml 都生成对应的 schemas/*.py 与 docs/data_dict/*.md
  2. YAML 中每个字段在 Pydantic BaseModel 中存在 (类型/nullable 一致)
  3. Pydantic BaseModel 中的字段在 YAML 中存在 (防 schemas 手改)
  4. Markdown 字典的字段表与 Pydantic 字段一致 (按 YAML 顺序)
  5. storage 路径只在文件不存在时报 warning (第一次跑前文件不存在是正常的)
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from newbee.datasource.codegen import (
    DATA_DICT_DIR,
    DOCS_DIR,
    PROJECT_ROOT,
    SCHEMAS_DIR,
    FieldDef,
    TypeDef,
    _to_class_name,
    _to_module_name,
)


# ---------- helpers ----------


def _field_type_compatible(yaml_pyarrow: str, py_type_str: str) -> bool:
    """粗略对齐: pyarrow type 与 Pydantic annotation 兼容."""
    pyarrow_to_py = {
        "string": "str",
        "bool": "bool",
        "int8": "int",
        "int16": "int",
        "int32": "int",
        "int64": "int",
        "uint8": "int",
        "uint16": "int",
        "uint32": "int",
        "uint64": "int",
        "float": "float",
        "double": "float",
    }
    expected = pyarrow_to_py.get(yaml_pyarrow)
    if expected is None:
        return True  # 未知类型跳过
    return expected == py_type_str


# ---------- fixtures ----------


@pytest.fixture(scope="module")
def all_type_defs() -> list[TypeDef]:
    return [TypeDef.from_yaml_path(p) for p in sorted(DATA_DICT_DIR.glob("*.yaml"))]


# ---------- 1. YAML / Pydantic / markdown 三方文件存在 ----------


def test_each_yaml_has_schema_and_markdown(all_type_defs) -> None:
    """每个 YAML 必须有对应的 schemas/<module>.py 与 docs/data_dict/<Name>.md."""
    for td in all_type_defs:
        py_path = SCHEMAS_DIR / f"{_to_module_name(td.name)}.py"
        md_path = DOCS_DIR / f"{td.name}.md"
        assert py_path.exists(), f"missing Pydantic file: {py_path}"
        assert md_path.exists(), f"missing markdown: {md_path}"


# ---------- 2. YAML fields ⊆ Pydantic fields ----------


def test_yaml_fields_in_pydantic(all_type_defs) -> None:
    """YAML 的每个字段必须在 Pydantic 中存在, 且 nullable 一致."""
    for td in all_type_defs:
        if td.npy_class is not None:
            continue  # npy 类型跳过
        mod = importlib.import_module(f"newbee.datasource.schemas.{_to_module_name(td.name)}")
        cls_name = _to_class_name(td.name)
        cls: type[BaseModel] = getattr(mod, cls_name)
        pyd_fields = cls.model_fields

        yaml_names = {f.name for f in td.fields}
        pyd_names = set(pyd_fields.keys())
        assert yaml_names == pyd_names, (
            f"{td.name}: YAML/Pydantic 字段不一致 "
            f"(YAML-only={yaml_names - pyd_names}, Pydantic-only={pyd_names - yaml_names})"
        )

        # nullable 一致
        for f in td.fields:
            pf = pyd_fields[f.name]
            ann = str(pf.annotation)
            is_nullable = "None" in ann
            assert is_nullable == f.nullable, (
                f"{td.name}.{f.name}: nullable 不一致 "
                f"(YAML nullable={f.nullable}, Pydantic ann={ann})"
            )
            # type 对齐
            py_type_raw = re.sub(r"\|.*", "", ann).strip()
            # Pydantic 把 typing 注解解析成 "<class 'str'>" 等形式; 抽取最后一词
            tokens = re.findall(r"\w+", py_type_raw)
            py_type = tokens[-1] if tokens else ""
            assert _field_type_compatible(f.pyarrow, py_type), (
                f"{td.name}.{f.name}: type 不一致 "
                f"(YAML pyarrow={f.pyarrow}, Pydantic={py_type_raw})"
            )


# ---------- 3. Pydantic 字段都在 YAML (防手改 schemas) ----------


def test_pydantic_fields_in_yaml(all_type_defs) -> None:
    """Pydantic BaseModel 字段必须是 YAML 的子集 (防止 schemas 被手改)."""
    for td in all_type_defs:
        if td.npy_class is not None:
            continue
        mod = importlib.import_module(f"newbee.datasource.schemas.{_to_module_name(td.name)}")
        cls_name = _to_class_name(td.name)
        cls: type[BaseModel] = getattr(mod, cls_name)
        pyd_names = set(cls.model_fields.keys())
        yaml_names = {f.name for f in td.fields}
        extra = pyd_names - yaml_names
        assert not extra, (
            f"{td.name}: Pydantic 中存在但 YAML 缺失的字段: {sorted(extra)}. "
            f"请先更新 data_dict/{td.name}.yaml 再跑 codegen."
        )


# ---------- 4. markdown 字段表顺序与 YAML 一致 ----------


def test_markdown_field_order_matches_yaml(all_type_defs) -> None:
    """Markdown 的字段表必须按 YAML 顺序出现."""
    for td in all_type_defs:
        if td.npy_class is not None:
            continue
        md_path = DOCS_DIR / f"{td.name}.md"
        text = md_path.read_text(encoding="utf-8")

        # 抽取 markdown 表中 `field` 列
        rows: list[str] = []
        for line in text.splitlines():
            if line.startswith("| `") and "|" in line:
                first_cell = line.split("|", 2)[1].strip()
                # 取 ```xxx```
                m = re.match(r"`([^`]+)`", first_cell)
                if m:
                    rows.append(m.group(1))
        yaml_order = [f.name for f in td.fields]
        assert rows == yaml_order, (
            f"{td.name}: markdown 字段顺序与 YAML 不一致 "
            f"\n  markdown: {rows}\n  YAML:     {yaml_order}"
        )


# ---------- 5. storage path (warning-only) ----------


def test_storage_paths_warning_only(all_type_defs, caplog) -> None:
    """YAML storage 路径若不存在, 仅打 warning (不 fail); 文件是首次写入时创建的."""
    missing: list[str] = []
    for td in all_type_defs:
        if td.npy_class is not None:
            continue
        path = PROJECT_ROOT / td.storage
        if not path.exists():
            missing.append(f"{td.name} → {path}")
    if missing:
        # warning-only, 不 fail
        import logging

        caplog.set_level(logging.WARNING)
        for m in missing:
            pytest.warns(UserWarning, match=re.escape(m))


# ---------- 6. schema_version / frequency / primary_key 一致性 ----------


def test_yaml_metadata_reflects_in_schema(all_type_defs) -> None:
    """YAML 的 schema_version / frequency 与 Pydantic docstring 不必逐字对齐, 但 primary_key 字段必须存在于 Pydantic."""
    for td in all_type_defs:
        if td.npy_class is not None:
            continue
        mod = importlib.import_module(f"newbee.datasource.schemas.{_to_module_name(td.name)}")
        cls: type[BaseModel] = getattr(mod, _to_class_name(td.name))
        pyd_names = set(cls.model_fields.keys())
        for pk in td.primary_key:
            assert pk in pyd_names, (
                f"{td.name}: primary_key {pk!r} 不在 Pydantic 字段中"
            )


# ---------- 7. FieldDef 解析 round-trip ----------


def test_field_def_round_trip() -> None:
    raw = {
        "name": "test_field",
        "pyarrow": "float",
        "nullable": True,
        "unit": "CNY",
        "description": "test desc",
    }
    f = FieldDef.from_yaml(raw)
    assert f.name == "test_field"
    assert f.pyarrow == "float"
    assert f.nullable is True
    assert f.unit == "CNY"
    assert f.py_type == "float"
    assert "None" in f.annotation


def test_yaml_loads_correctly() -> None:
    """spot check: 至少一个 YAML 能正确解析."""
    kdata_yaml = DATA_DICT_DIR / "KData.yaml"
    assert kdata_yaml.exists()
    with open(kdata_yaml, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    assert raw["name"] == "KData"
    assert raw["schema_version"] == "1.0"
    assert raw["frequency"] == "daily"
    assert "trading_date" in {f["name"] for f in raw["fields"]}