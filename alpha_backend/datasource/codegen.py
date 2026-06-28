"""Codegen: YAML 字段字典 → Pydantic BaseModel + Markdown 数据字典.

设计:
  - YAML 是 source of truth (configs/data_dict/<Type>.yaml)
  - codegen 读取每个 YAML, 生成
      1. Pydantic BaseModel 到 alpha_backend/datasource/schemas/<type>.py
      2. Markdown 数据字典到 docs/data_dict/<Type>.md
  - tests/test_dict_sync.py 双向校验三者一致

用法:
  python -m alpha_backend.datasource.codegen
"""
from __future__ import annotations

import keyword
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ---------- 路径常量 ----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DICT_DIR = PROJECT_ROOT / "configs" / "data_dict"
SCHEMAS_DIR = PROJECT_ROOT / "alpha_backend" / "datasource" / "schemas"
DOCS_DIR = PROJECT_ROOT / "docs" / "data_dict"

# ---------- 类型映射 ----------

# pyarrow type string -> python type annotation
_PYARROW_TO_PY: dict[str, str] = {
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

# ---------- Pascal_Snake_Case 校验 ----------

_PASCAL_SNAKE_RE = re.compile(r"^([A-Z][A-Za-z0-9]*)(_[A-Z][A-Za-z0-9]*)*$")


def _assert_pascal_snake_case(name: str, where: str) -> None:
    """Pascal_Snake_Case: 每个 token 首字母大写, 由 _ 分隔. 例: KData, Trade_Status, KData_M1."""
    if not _PASCAL_SNAKE_RE.match(name):
        raise ValueError(
            f"{where} 必须是 Pascal_Snake_Case (每个 token 首字母大写, _ 分隔), 得到 {name!r}"
        )


def _to_module_name(pascal_snake: str) -> str:
    """Pascal_Snake_Case → snake_case module name. 例: KData -> kdata; Trade_Status -> trade_status."""
    return pascal_snake.lower()


def _to_class_name(pascal_snake: str) -> str:
    """Pascal_Snake_Case → Pydantic class name. 例: KData -> KData; Trade_Status -> TradeStatus.

    单 token (无 `_`) 时保留原样; 多 token 时去掉 `_` (各 token 已经是 PascalCase 内部)。
    """
    return pascal_snake.replace("_", "")


# ---------- 字段定义 ----------


@dataclass
class FieldDef:
    """单个字段的解析后定义."""

    name: str
    pyarrow: str
    nullable: bool
    unit: str
    description: str

    @classmethod
    def from_yaml(cls, raw: dict[str, Any]) -> "FieldDef":
        for required in ("name", "pyarrow", "description"):
            if required not in raw:
                raise ValueError(f"字段缺少 {required}: {raw}")
        return cls(
            name=str(raw["name"]),
            pyarrow=str(raw["pyarrow"]),
            nullable=bool(raw.get("nullable", False)),
            unit=str(raw.get("unit", "")),
            description=str(raw["description"]),
        )

    @property
    def py_type(self) -> str:
        return _PYARROW_TO_PY.get(self.pyarrow, "Any")

    @property
    def annotation(self) -> str:
        if self.nullable:
            return f"{self.py_type} | None"
        return self.py_type


@dataclass
class TypeDef:
    """单个数据类型的解析后定义."""

    name: str
    description: str
    schema_version: str
    frequency: str
    storage: str
    primary_key: tuple[str, ...]
    fields: list[FieldDef]
    npy_class: dict[str, Any] | None = None

    @classmethod
    def from_yaml_path(cls, path: Path) -> "TypeDef":
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"YAML 顶层必须是 dict, {path}")
        for required in ("name", "description", "schema_version", "frequency", "storage"):
            if required not in raw:
                raise ValueError(f"YAML {path} 缺少 {required}")
        _assert_pascal_snake_case(str(raw["name"]), f"{path.name}.name")
        npy_class = raw.get("npy_class")
        if npy_class is None:
            field_dicts = raw.get("fields", [])
            if not isinstance(field_dicts, list):
                raise ValueError(f"YAML {path} fields 必须是 list")
            fields = [FieldDef.from_yaml(fd) for fd in field_dicts]
            if "primary_key" not in raw:
                raise ValueError(f"YAML {path} 缺少 primary_key")
            primary_key = tuple(str(k) for k in raw["primary_key"])
        else:
            # npy 类型可省略 fields / primary_key
            fields = [FieldDef.from_yaml(fd) for fd in raw.get("fields", [])]
            primary_key = tuple(str(k) for k in raw.get("primary_key", ()))
        return cls(
            name=str(raw["name"]),
            description=str(raw["description"]),
            schema_version=str(raw["schema_version"]),
            frequency=str(raw["frequency"]),
            storage=str(raw["storage"]),
            primary_key=primary_key,
            fields=fields,
            npy_class=npy_class,
        )

    @property
    def module_name(self) -> str:
        return _to_module_name(self.name)

    @property
    def class_name(self) -> str:
        return _to_class_name(self.name)


# ---------- Pydantic 代码生成 ----------


def _generate_pydantic(td: TypeDef) -> str:
    """生成一个 Pydantic BaseModel 模块的源码."""
    if td.npy_class is not None:
        # npy 类型: 不生成 BaseModel, 只生成一个标记常量
        return (
            f'"""Auto-generated from configs/data_dict/{td.name}.yaml by codegen. DO NOT EDIT.\n'
            f"\n"
            f"This type uses npy cache (matrix-shaped), no Pydantic BaseModel is needed.\n"
            f'"""\n'
            f"from __future__ import annotations\n"
            f"\n"
            f'NPY_CLASS_META: dict = {repr(td.npy_class)}\n'
        )

    imports = [
        "from __future__ import annotations",
        "",
        "from pydantic import BaseModel, ConfigDict, field_validator",
    ]
    lines: list[str] = list(imports)
    lines.append("")
    lines.append(f'__all__ = ["{td.class_name}"]')
    lines.append("")
    lines.append("")
    lines.append(f"class {td.class_name}(BaseModel):")
    lines.append(f'    """{td.description}"""')
    lines.append(f"")
    lines.append(f"    model_config = ConfigDict(extra=\"forbid\", frozen=False)")
    lines.append("")
    for f in td.fields:
        if keyword.iskeyword(f.name):
            raise ValueError(f"{td.name}: 字段名 {f.name} 是 Python 关键字")
        lines.append(f"    {f.name}: {f.annotation}  # {f.unit} — {f.description}")
    # 添加 stock_code 校验器
    if any(f.name == "stock_code" for f in td.fields):
        lines.append("")
        lines.append("    @field_validator(\"stock_code\")")
        lines.append("    @classmethod")
        lines.append("    def _check_stock_code(cls, v: str) -> str:")
        lines.append('        """9 字符 .SH/.SZ 后缀校验."""')
        lines.append("        if not isinstance(v, str) or len(v) != 9 or v[6] != \".\" or v[7:] not in (\"SH\", \"SZ\"):")
        lines.append('            raise ValueError(f"stock_code 必须是 9 字符 6d.SH/SZ 格式, 得到 {v!r}")')
        lines.append("        return v")
    lines.append("")
    return "\n".join(lines)


# ---------- Markdown 生成 ----------


def _generate_markdown(td: TypeDef) -> str:
    """生成 markdown 数据字典."""
    lines: list[str] = []
    lines.append(f"# {td.name}")
    lines.append("")
    lines.append(f"> {td.description}")
    lines.append("")
    lines.append(f"- **schema_version**: `{td.schema_version}`")
    lines.append(f"- **frequency**: `{td.frequency}`")
    lines.append(f"- **storage**: `{td.storage}`")
    lines.append(f"- **primary_key**: `{', '.join(td.primary_key)}`")
    lines.append("")
    if td.npy_class is not None:
        lines.append("## npy cache metadata")
        lines.append("")
        lines.append("此类型用 npy 矩阵缓存, 不走 parquet 字段表. 元信息如下:")
        lines.append("")
        lines.append("```yaml")
        lines.append(yaml.safe_dump(td.npy_class, allow_unicode=True, sort_keys=False).rstrip())
        lines.append("```")
        lines.append("")
        return "\n".join(lines)
    lines.append("## Fields")
    lines.append("")
    lines.append("| Field | Type | Nullable | Unit | Description |")
    lines.append("|---|---|---|---|---|")
    for f in td.fields:
        nullable = "✓" if f.nullable else ""
        unit = f.unit or ""
        # escape pipe in description
        desc = f.description.replace("|", "\\|")
        lines.append(f"| `{f.name}` | `{f.pyarrow}` ({f.py_type}) | {nullable} | {unit} | {desc} |")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(f"- 此字典由 `python -m alpha_backend.datasource.codegen` 自动生成, 不要手改.")
    lines.append(f"- 字段定义变更请改 `configs/data_dict/{td.name}.yaml` 后跑 codegen + pytest.")
    lines.append("")
    return "\n".join(lines)


# ---------- 主流程 ----------


def generate_all(verbose: bool = True) -> tuple[list[Path], list[Path]]:
    """遍历 configs/data_dict/, 生成 Pydantic + markdown. 返回 (py_files, md_files)."""
    if not DATA_DICT_DIR.exists():
        raise FileNotFoundError(f"未找到 configs/data_dict 目录: {DATA_DICT_DIR}")

    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    py_files: list[Path] = []
    md_files: list[Path] = []

    for yml_path in sorted(DATA_DICT_DIR.glob("*.yaml")):
        td = TypeDef.from_yaml_path(yml_path)

        # Pydantic
        py_path = SCHEMAS_DIR / f"{td.module_name}.py"
        py_content = _generate_pydantic(td)
        py_path.write_text(py_content, encoding="utf-8")
        py_files.append(py_path)
        if verbose:
            print(f"[codegen] {yml_path.name} → {py_path.relative_to(PROJECT_ROOT)}")

        # Markdown
        md_path = DOCS_DIR / f"{td.name}.md"
        md_content = _generate_markdown(td)
        md_path.write_text(md_content, encoding="utf-8")
        md_files.append(md_path)
        if verbose:
            print(f"[codegen] {yml_path.name} → {md_path.relative_to(PROJECT_ROOT)}")

    # 生成 schemas 包 __init__.py: re-export 所有 BaseModel
    _write_schemas_init(py_files)
    return py_files, md_files


def _write_schemas_init(py_files: list[Path]) -> None:
    """生成 schemas/__init__.py, 从每个 .py 文件导入其 BaseModel."""
    imports: list[str] = ["from __future__ import annotations", ""]
    exports: list[str] = []
    for py in sorted(py_files):
        module = py.stem
        # 从模块名推回 Pascal_Snake_Case 类名
        td_name = _module_to_pascal_snake(module)
        cls = _to_class_name(td_name)
        imports.append(f"from alpha_backend.datasource.schemas import {module} as _{module}")
        exports.append(f"    \"{cls}\",")
    imports.append("")
    imports.append("__all__ = [")
    imports.extend(exports)
    imports.append("]")
    imports.append("")
    content = "\n".join(imports) + "\n"
    # 简化: 让每个模块的类直接可访问
    rewrite: list[str] = ["from __future__ import annotations", ""]
    for py in sorted(py_files):
        module = py.stem
        rewrite.append(f"from alpha_backend.datasource.schemas.{module} import *  # noqa: F401,F403")
    rewrite.append("")
    (SCHEMAS_DIR / "__init__.py").write_text("\n".join(rewrite), encoding="utf-8")


def _module_to_pascal_snake(module: str) -> str:
    """snake_case → Pascal_Snake_Case. 简单实现: 已知映射 + 启发式. 优先看是否含 '_'."""
    if "_" in module:
        return "_".join(p.capitalize() for p in module.split("_"))
    # 启发式: kdata -> KData, ad-hoc
    # 通过查找已知 YAML name 反查更稳; 此处 fallback 用 yaml 列表:
    return _resolve_from_yaml(module)


def _resolve_from_yaml(module: str) -> str:
    for yml in DATA_DICT_DIR.glob("*.yaml"):
        td = TypeDef.from_yaml_path(yml)
        if td.module_name == module:
            return td.name
    raise KeyError(f"找不到模块 {module} 对应的 YAML")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = "--quiet" not in argv
    py_files, md_files = generate_all(verbose=verbose)
    if verbose:
        print(f"\n[codegen] ✓ {len(py_files)} Pydantic + {len(md_files)} markdown generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())