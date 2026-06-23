# Release Notes — Data Module Refactor (M2)

## 摘要

数据层从「按股票 per-stock parquet」迁移到「按类型 single long-format parquet」。

| 维度           | 旧 (M1)                          | 新 (M2)                                 |
| -------------- | -------------------------------- | --------------------------------------- |
| 存储粒度       | `data/adj/000001.parquet` × N    | `data/KData.parquet` (long-format)      |
| 字段定义       | 散落在 `newbee/data/sources/`    | YAML 单一 source of truth (codegen)     |
| 状态追踪       | `data/_manifest/fetch_state.json`| `data/_Manifest/Data_State.json`        |
| 股票代码       | 6 字符                           | 9 字符 + `.SH` / `.SZ` 后缀             |
| 复权处理       | `kind=raw` / `kind=adj` 双目录   | 单一目录 + `close_post_adj` 列          |
| 并发安全       | 无                               | fcntl + tempfile + os.replace           |
| schema 演进    | 无                               | Pydantic + `schema_version` 守卫         |

## 已废弃 (DEPRECATED, 过渡期保留)

- `newbee.data.universe.StockPool` → 用 `newbee.datasource.service.universe.UniverseService`
- `newbee.data.storage.load_bars_from_parquet` → 用 `newbee.datasource.service.kdata.KDataService.read_window()`
- `newbee.data.storage.write_stock_parquet` → 用 `newbee.datasource.storage.io.DataFile.upsert()`
- `newbee.data.fetch_state.{read_state,update_state,infer_resume_range}` → 用 `newbee.datasource.storage.state.StateTracker`
- `newbee.data.incremental.build_plan` → 用 `newbee.datasource.cli update`
- `newbee.data.pit.PITStore` → 暂无新实现, 保留旧版直至 M3 财务数据重做
- `newbee.data.calendar.*` → 用 `newbee.datasource.calendar.*` (同 API, 迁移即可)

每次 import 这些旧符号会触发 `DeprecationWarning`.

## 已下线的旧布局

- `data/raw/` (per-stock raw) → `data/_Deprecated_raw/`
- `data/adj/` (per-stock post-adj) → `data/_Deprecated_adj/`
- `data/_manifest/fetch_state.json` (legacy state) → 已删除
- `data/universe/pool.parquet` + `manifest.json` (legacy pool) → 已删除

新的 long-format 单文件取代之: `data/KData.parquet`, `data/Trade_Status.parquet`,
`data/Adj_Factor.parquet`, `data/Universe.parquet`.

## 迁移路径

1. 跑 `python -m newbee.datasource.cli init-universe` (一次性初始化 universe)
2. 跑 `python -m newbee.datasource.cli update --type KData` (拉历史行情)
3. 跑 `python -m newbee.datasource.cli update --type Trade_Status` (推算停牌)
4. 跑 `python -m newbee.datasource.cli update --type Adj_Factor` (推算复权因子)
5. 改业务代码 imports: `from newbee.data.*` → `from newbee.datasource.*`
6. (可选) 删 `_Deprecated_raw/` 和 `_Deprecated_adj/` 目录

## 已知限制 (M2)

- `Trade_Status.is_st` / `is_activate` 当前全部 False, 待 M3 接入东财 ST 名单
- `Adj_Factor` 由 `close_post_adj / close` 反推, 仅在源同时给两者时准确
- `PITStore` 未迁移, 仍用旧代码; M3 财务数据重做时一并处理

## 验证

- `pytest tests/ -q` — 169 / 169 通过
- `python -m newbee.datasource.cli status` — 列出每类数据的覆盖范围
- `python -m newbee.datasource.cli verify` — 跑核心三件套测试
