# Data Module SOP (Standard Operating Procedure)

> 适用范围: 任何对 `configs/data_dict/*.yaml`、`alpha_backend/datasource/schemas/*.py`、`docs/data_dict/*.md` 的修改.

## 字段定义 source of truth

```
configs/data_dict/<Type>.yaml   ←  唯一真源 (Pascal_Snake_Case)
        ↓ codegen (python -m alpha_backend.datasource.codegen)
        ├── alpha_backend/datasource/schemas/<type>.py   (Pydantic BaseModel, 禁止手改)
        └── docs/data_dict/<Type>.md              (人类可读字典)
        ↓ test_dict_sync.py 双向校验
```

## 修改字段的 5 步流程

1. **改 YAML** — 编辑 `configs/data_dict/<Type>.yaml`, 调整字段名 / 类型 / nullable / 描述
2. **跑 codegen** — `python -m alpha_backend.datasource.codegen` (会自动同步重写 `registry.py` 中 `# codegen: begin` / `# codegen: end` 之间的 `_register_defaults` register 块, 不用手改 registry)
3. **跑 sync 测试** — `pytest tests/test_dict_sync.py tests/test_codegen_registry_sync.py -q`
4. **写业务代码** — 在 `alpha_backend/datasource/` 下消费字段
5. **commit 三件套** — YAML + schemas/*.py + docs/data_dict/*.md + registry.py 一起提交 (codegen 跑过的产物)

## 禁止事项

| 禁止 | 原因 |
|---|---|
| 直接编辑 `alpha_backend/datasource/schemas/*.py` 添加字段 | 会与 YAML 漂移, `test_dict_sync.py` 会 fail |
| 删除 `configs/data_dict/<Type>.yaml` 中的字段但保留 Pydantic | 反向漂移, 测试 fail |
| 跳过 codegen 直接 commit YAML 改动 | schemas 与 docs/data_dict 不会同步 |
| 手改 `registry.py` `_register_defaults` 中 sentinel 之间的 register 块 | codegen 会覆盖; 用 `codegen --check-registry` 校验一致性, 或直接改 YAML 后跑 codegen |
| 在 `datas/raw/` 或 `datas/adj/` 下写新文件 | 已废弃, 改用 `datas/<Type>.parquet` long format |
| 在业务代码里 hardcode `Path("datas/raw")` 等路径 | 路径必须通过 `REGISTRY.get(<Type>).storage_path` 拿到 |

## 类型 / 命名规范

- **类型名**: Pascal_Snake_Case (`KData`, `Trade_Status`, `Adj_Factor`, `Universe`)
- **存储路径**: `datas/<Type>.parquet`, 例外只有 npy 类 (`Features_Alpha`)
- **stock_code**: 9 字符 `{6-digit}.{SH|SZ}`, 例 `600000.SH` / `000012.SZ`
- **trading_date**: 10 字符 ISO string `YYYY-MM-DD`
- **OHLCV/amount/volume**: nullable `float32` (KData)
- **adj_factor**: nullable `float64` (Adj_Factor, 防长 horizon 漂移)

## 添加新数据类型的流程

1. 在 `configs/data_dict/<NewType>.yaml` 写字段
2. 跑 codegen, 检查生成的 `schemas/<newtype>.py` 和 `docs/data_dict/<NewType>.md`
3. 在 `alpha_backend/datasource/registry.py` 调用 `REGISTRY.register(DataType(...))` 注册
4. 在 `alpha_backend/datasource/service/<newtype>.py` 实现 Service (full_init / daily_update)
5. 在 `alpha_backend/datasource/cli.py` 添加对应的 CLI 子命令 (可选)

## Codegen 命令

```bash
# 静默模式 (CI 用)
python -m alpha_backend.datasource.codegen --quiet

# 详细模式 (本地用)
python -m alpha_backend.datasource.codegen
```

## 测试命令

```bash
# 仅 dict-sync
pytest tests/test_dict_sync.py -q

# 全套 (dict-sync + storage-io + state-tracker)
pytest tests/test_dict_sync.py tests/test_storage_io.py tests/test_state_tracker.py -q
```

## PostToolUse Hook 自动校验

`~/.claude/settings.json` 中注册的 PostToolUse hook 在以下操作后自动跑 codegen + dict_sync:

- `Edit|Write|MultiEdit` 作用于 `configs/data_dict/*.yaml`
- `Edit|Write|MultiEdit` 作用于 `alpha_backend/datasource/schemas/*.py`

hook 输出 stderr 不会打断 Claude 流程, 但会以 `[codegen-hook]` 开头标记 warning / 失败.