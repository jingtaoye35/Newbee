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

import importlib
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
REGISTRY_PY = PROJECT_ROOT / "alpha_backend" / "datasource" / "registry.py"
REGISTRY_SENTINEL_BEGIN = "# codegen: begin"
REGISTRY_SENTINEL_END = "# codegen: end"

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
    tds: list[TypeDef] = []

    for yml_path in sorted(DATA_DICT_DIR.glob("*.yaml")):
        td = TypeDef.from_yaml_path(yml_path)
        tds.append(td)

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
    # 同步重写 registry.py _register_defaults sentinel 之间的 register 块
    _write_registry_default_block(tds)
    if verbose:
        print(f"[codegen] {REGISTRY_PY.relative_to(PROJECT_ROOT)} ← registry register block")
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


# ---------- registry.py _register_defaults 重写 ----------


_STORAGE_FORMAT_MAP: dict[str, str] = {
    ".parquet": "parquet",
    ".csv": "csv",
}


def _format_from_storage(storage: str) -> str:
    """从 YAML storage 字段后缀推断 DataType.format.

    例: 'datas/KData.parquet' → 'parquet', 'datas/Trading_Date.csv' → 'csv'.
    未知后缀抛 ValueError, 强制开发者在 codegen 之外补 format.
    """
    suffix = Path(storage).suffix.lower()
    fmt = _STORAGE_FORMAT_MAP.get(suffix)
    if fmt is None:
        raise ValueError(
            f"无法从 storage={storage!r} 推断 format; "
            f"仅支持 {_STORAGE_FORMAT_MAP.keys()}, 请调整 YAML 或扩展 codegen"
        )
    return fmt


def _render_registry_block(tds: list[TypeDef]) -> str:
    """从 TypeDef 列表派生 _register_defaults() sentinel 之间的 register 块.

    用 importlib 反射拿 pydantic_model 类 (而不是写死 import 行), 保证 schemas
    集合变化时无需人工维护 import 列表. 反射路径假设 schemas.__init__ 已经写好.

    输出形式: `pydantic_model=_schemas.<ClassName>`, 配合 registry.py 顶部手工写的
    `import alpha_backend.datasource.schemas as _schemas`, 新增 YAML 时无需任何手工动作.
    """
    schemas_pkg = importlib.import_module("alpha_backend.datasource.schemas")
    lines: list[str] = []
    for td in tds:
        if td.npy_class is not None:
            # npy 类型不走 parquet/csv 存储, codegen 暂不为其生成 register
            continue
        cls = getattr(schemas_pkg, _to_class_name(td.name))
        fmt = _format_from_storage(td.storage)
        # 用 repr(tuple(...)) 保证单元素 primary_key 也带尾逗号,
        # 避免 `primary_key=("foo")` 被 Python 当作字符串.
        pk_repr = repr(tuple(td.primary_key))[1:-1]  # 去掉外层括号, 保留内部引号/逗号
        cls_name = _to_class_name(td.name)
        # 缩进匹配 registry.py 现有风格
        lines.append(f"    # {td.name}: {td.frequency}, primary_key {tuple(td.primary_key)}")
        lines.append(f"    REGISTRY.register(")
        lines.append(f"        DataType(")
        lines.append(f'            name="{td.name}",')
        lines.append(f'            schema_version="{td.schema_version}",')
        lines.append(f'            frequency="{td.frequency}",')
        lines.append(f"            storage_path=Path({td.storage!r}),")
        lines.append(f"            primary_key=({pk_repr}),")
        lines.append(f"            pydantic_model=_schemas.{cls_name},")
        if fmt != "parquet":
            lines.append(f'            format="{fmt}",')
        lines.append(f"        )")
        lines.append(f"    )")
        lines.append("")
    # 去掉尾部空行, 块末尾不留空白
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _write_registry_default_block(tds: list[TypeDef]) -> None:
    """重写 registry.py 中 sentinel 之间的 register 块.

    sentinel 缺失时抛 RuntimeError (含具体行号), 防止误删 sentinel 后静默整段重写.
    """
    text = REGISTRY_PY.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    begin_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        if REGISTRY_SENTINEL_BEGIN in line:
            begin_idx = i
        elif REGISTRY_SENTINEL_END in line:
            end_idx = i
    if begin_idx is None:
        raise RuntimeError(
            f"registry.py 缺少 sentinel {REGISTRY_SENTINEL_BEGIN!r}; "
            f"请先人工插入 sentinel 再跑 codegen"
        )
    if end_idx is None:
        raise RuntimeError(
            f"registry.py 缺少 sentinel {REGISTRY_SENTINEL_END!r}; "
            f"请先人工插入 sentinel 再跑 codegen"
        )
    if end_idx <= begin_idx:
        raise RuntimeError(
            f"registry.py sentinel 顺序错误: "
            f"begin@{begin_idx + 1} 必须在 end@{end_idx + 1} 之前"
        )

    new_block = _render_registry_block(tds) + "\n"
    new_lines = lines[: begin_idx + 1] + [new_block] + lines[end_idx:]
    REGISTRY_PY.write_text("".join(new_lines), encoding="utf-8")


def _check_registry() -> int:
    """--check-registry: 派生期望块, 与磁盘文件 sentinel 间内容字符串比较.

    不写盘. 一致 exit 0; 不一致 exit 1 并打 diff.
    """
    tds = [TypeDef.from_yaml_path(p) for p in sorted(DATA_DICT_DIR.glob("*.yaml"))]
    expected = _render_registry_block(tds)
    text = REGISTRY_PY.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    begin_idx = end_idx = -1
    for i, line in enumerate(lines):
        if REGISTRY_SENTINEL_BEGIN in line:
            begin_idx = i
        elif REGISTRY_SENTINEL_END in line:
            end_idx = i
    if begin_idx < 0 or end_idx < 0:
        print(f"[codegen] ✗ registry.py 缺少 sentinel", file=sys.stderr)
        return 1
    actual = "\n".join(lines[begin_idx + 1 : end_idx]).rstrip("\n")
    expected = expected.rstrip("\n")
    if actual == expected:
        print(f"[codegen] ✓ registry _register_defaults 与 YAML 一致")
        return 0
    print(f"[codegen] ✗ registry _register_defaults 与 YAML 不一致:", file=sys.stderr)
    _print_unified_diff(expected, actual)
    return 1


def _print_unified_diff(expected: str, actual: str) -> None:
    """简易 unified diff 打印, 不依赖 difflib 以减少 import surface."""
    import difflib

    diff = difflib.unified_diff(
        expected.splitlines(),
        actual.splitlines(),
        fromfile="expected (from YAML)",
        tofile="actual (registry.py)",
        lineterm="",
    )
    for line in diff:
        print(line, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check-registry" in argv:
        argv = [a for a in argv if a != "--check-registry"]
        return _check_registry()
    verbose = "--quiet" not in argv
    py_files, md_files = generate_all(verbose=verbose)
    if verbose:
        print(f"\n[codegen] ✓ {len(py_files)} Pydantic + {len(md_files)} markdown generated")
    return 0


if __name__ == "__main__":
    sys.exit(main())