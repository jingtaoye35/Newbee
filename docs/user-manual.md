# Newbee 用户手册

> **面向**: 跑平台的 operator.
> **范围(首版)**: 数据怎么写进 `datas/`. 因子、回测、组合等内容留待后续 change.
> **配套文档**:
> - 设计规范 / AI SOP:[`docs/platform-design.md`](platform-design.md) §10
> - 字段字典(每个 DataType 一份):[`docs/data_dict/`](data_dict/)

---

## 1. 数据在哪

平台所有数据落在 `datas/` 下,目录结构如下(摘自 `platform-design.md` §10.1):

```
datas/
├── KData.parquet              # 日 K 线 (后复权 close_adj 与未复权 OHLCV 并存)
├── KData_M1.parquet           # 1 分钟 K 线 (预留)
├── KData_M5.parquet           # 5 分钟 K 线 (预留)
├── Trade_Status.parquet       # 交易状态 (停牌 / ST / 活跃)
├── Stock_Basic_Data.parquet   # 基础信息 + 复权因子
├── Universe.parquet           # 股票池
├── Trading_Date.csv           # 交易日历
├── PIT.parquet                # 财务披露 (point-in-time)
│
├── Features/                  # npy 因子矩阵
│   └── <FactorName>/{YYYY-MM-DD}.npy
├── Alpha/                     # npy 组合权重
│   └── <StrategyId>/{YYYY-MM-DD}.npy
│
├── _Manifest/                 # 下划线开头,平台自管
│   ├── Data_State.json        # 每类型覆盖率 + schema_version
│   └── Data_Dict_Index.json   # 字段字典索引
│
├── models/                    # ⛔ 不归数据模块管
└── repr/                      # ⛔ 不归数据模块管
```

> ⚠️ 任何手动放进 `datas/` 的文件,只要名字不是上面列出的合法类型,就**不会被** `REGISTRY` 识别,也不会被任何 CLI 命令读到.

详见 [`docs/platform-design.md`](platform-design.md) §10.1.

---

## 2. 支持的 DataType

平台目前注册了 5 个 DataType(取自 `alpha_backend/datasource/registry.py`,用 `REGISTRY.all()` 可枚举):

| 类型 | 文件 | 格式 | 频率 | 一句话 |
|---|---|---|---|---|
| `KData` | `datas/KData.parquet` | parquet | daily | 日 K 线(OHLCV + amount + volume + close_adj),全 A 股 |
| `Trade_Status` | `datas/Trade_Status.parquet` | parquet | daily | 停牌 / ST / 活跃 三种布尔状态 |
| `Stock_Basic_Data` | `datas/Stock_Basic_Data.parquet` | parquet | daily | 复权因子 + 涨跌停价 + 总股本 + 换手率 |
| `Universe` | `datas/Universe.parquet` | parquet | static | 股票池(`stock_index` 单调递增,append-only) |
| `Trading_Date` | `datas/Trading_Date.csv` | csv | static | 交易日历,单列 `trading_date`,ISO `YYYY-MM-DD` |

每个类型的字段定义见 `docs/data_dict/<Type>.md`(由 codegen 自动生成,不要手改).

详见 [`docs/platform-design.md`](platform-design.md) §10.5.

---

## 3. 两种写入方式

### 3.1 走 CLI(推荐,日常运维)

每个交易日收盘后跑一次,把当天新增的行情增量写进 parquet. CLI 自己处理 resume 区间、原子写、`Data_State.json` 更新.

```bash
conda activate py312

# 更新交易日历 (从 2010-01-01 开始， 到执行日下一天结束)
python -m alpha_backend.datasource.cli update --type Trading_Date

# 拉日 K 线
python -m alpha_backend.datasource.cli update --type KData

# 拉分钟 K 线 (默认 source=sina; em/tx 是备选)
python -m alpha_backend.datasource.cli update --type KData --source em
```

支持的 `--type` 值:`KData` / `Trade_Status` / `Stock_Basic_Data` / `Trading_Date` / `Universe`. `Universe` 走 `init-universe` 子命令(不是 `update`),因为它是 static:

```bash
python -m alpha_backend.datasource.cli init-universe --index csi1000 --backdate 2020-01-01
```

### 3.2 直接 `DataFile.upsert`(一次性脚本 / 修补历史)

```python
import pandas as pd
from alpha_backend.datasource.registry import REGISTRY
from alpha_backend.datasource.storage.io import DataFile

dtype = REGISTRY.get("KData")
file_ = DataFile(dtype)

# 5 行示例 (trading_date / stock_code 必须齐全, OHLCV 可空)
new_rows = pd.DataFrame({
    "trading_date": ["2026-06-23", "2026-06-23", "2026-06-24", "2026-06-24", "2026-06-25"],
    "stock_code":   ["600000.SH", "000012.SZ", "600000.SH", "000012.SZ", "600000.SH"],
    "open":   [10.01, 20.02, 10.05, 20.10, 10.20],
    "high":   [10.50, 20.80, 10.60, 20.90, 10.55],
    "low":    [ 9.90, 19.80, 10.00, 20.00, 10.05],
    "close":  [10.30, 20.50, 10.40, 20.60, 10.40],
    "amount": [1.0e8, 2.0e8, 1.1e8, 2.1e8, 9.5e7],
    "volume": [1.0e7, 1.0e7, 1.05e7, 1.05e7, 9.2e6],
    "close_adj": [10.30, 20.50, 10.40, 20.60, 10.40],
})

# conflict="replace" → 主键冲突时整行覆盖; "ignore" → 跳过冲突; "error" → 抛错
n = file_.upsert(new_rows, conflict="replace")
print(f"upserted {n} rows")
```

详见 [`docs/platform-design.md`](platform-design.md) §10.7.

---

## 4. 查看状态

```bash
conda activate py312 && python -m alpha_backend.datasource.cli status
```

输出:

```
=== alpha_backend datasource status ===
data_root: /Users/yejingtao/JohnsonProject/Newbee
universe_sha: 72e26a6481dc8e50

Type             frequency  first        last         rows       stocks   updated_at
------------------------------------------------------------------------------------------
KData            daily      1990-12-21   2026-06-18   3364170    1001     2026-06-25T15:04:43
Stock_Basic_Data daily      1990-12-21   2026-06-18   3364170    1001     2026-06-25T15:04:43
Trade_Status     daily      -            -            0          0        2026-06-25T15:04:43
Trading_Date     static     2010-01-04   2026-06-25   3999       0        2026-06-25T15:04:43
Universe         static     -            -            1000       1000     2026-06-25T15:04:43
```

每列含义:

| 列 | 含义 |
|---|---|
| `Type` | DataType 名(必须出现在 `REGISTRY.all()` 里) |
| `frequency` | `daily` / `static` / `1min` / … |
| `first` | 文件里最小的 `trading_date`,ISO 字符串;空文件显示 `-` |
| `last` | 文件里最大的 `trading_date`;`-` 表示空文件 |
| `rows` | 行数;`0` 表示文件存在但是空的(可能是刚 truncate 或还没拉过) |
| `stocks` | distinct `stock_code` 数量(CSV 类如 `Trading_Date` 永远是 0) |
| `updated_at` | 文件最后写入时间(本机时区) |

`universe_sha` 是当前 `Universe` 的指纹,数据层用它判断 universe 是否被重新构建过.

详见 [`docs/platform-design.md`](platform-design.md) §10.6.

---

## 5. 常用操作

### 5.1 长周末后补行情

周末 / 节假日不开盘,周一早上 9 点跑一次 `update` 即可补齐缺失交易日. CLI 自己从 `Data_State.json` 的 `last_date` 算 resume 起点:

```bash
conda activate py312
python -m alpha_backend.datasource.cli update --type KData
python -m alpha_backend.datasource.cli update --type Trade_Status
python -m alpha_backend.datasource.cli update --type Stock_Basic_Data
```

### 5.2 给已有类型加新列(例:给 `KData` 加 `turnover`)

1. 编辑 `configs/data_dict/KData.yaml`,加 `turnover: float64, nullable, 日换手率`.
2. 跑 codegen:

    ```bash
    conda activate py312
    python -m alpha_backend.datasource.codegen
    ```

3. 跑双向一致性测试:

    ```bash
    pytest tests/test_dict_sync.py -q
    ```

4. 补历史数据(可选):用 `DataFile.upsert`,只对缺失日期写新列,已有行 `None`.

详见 [`docs/platform-design.md`](platform-design.md) §10.4 / §10.8.

### 5.3 添加一个全新类型(例:`PIT` 财务披露)

1. `configs/data_dict/PIT.yaml`:字段 + storage 路径 + primary_key.
2. `python -m alpha_backend.datasource.codegen` → 生成 `schemas/pit.py` + `docs/data_dict/PIT.md`.
3. `pytest tests/test_dict_sync.py -q` 必须过.
4. `alpha_backend/datasource/registry.py`:`REGISTRY.register(DataType(name="PIT", ...))`.
5. `alpha_backend/datasource/service/pit.py`:实现 `PITService`(`full_init` / `daily_update`).
6. `alpha_backend/datasource/cli.py`:`cmd_data_update` 加一个 `elif dtype.name == "PIT":` 分支.
7. `python -m alpha_backend.datasource.cli update --type PIT` 验证.

### 5.4 `Stock_Basic_Data.adj_factor` 是怎么来的

`Stock_Basic_Data.adj_factor` 由 `Stock_Basic_DataService` 在 `daily_update` 时根据当天 `KData.close_adj / KData.close` 计算并写入(见 `alpha_backend/datasource/service/stock_basic_data.py`).

所以**不需要额外命令**:正常跑 `update --type Stock_Basic_Data` 就会带上 `adj_factor`.

```bash
conda activate py312
python -m alpha_backend.datasource.cli update --type Stock_Basic_Data
```

如果 `Stock_Basic_Data` 已经有数据但 `adj_factor` 是 `None`,通常是 `KData.close_adj` 为 `None`(例如 `600000.SH`),属于上游数据缺失,无需修复.

详见 [`docs/platform-design.md`](platform-design.md) §10.5.3.

---

## 6. 故障排查

### 6.1 `status` 某行 `rows=0`

**症状**: `Trade_Status daily - - 0 0 ...`(空文件).

**根因**: 该类型从未被 `update` 过,或者上次 update 失败中途退出.

**修复**:

```bash
conda activate py312
python -m alpha_backend.datasource.cli update --type Trade_Status
# [需要网络] [会写文件]
```

### 6.2 `last_date` 滞后于今天

**症状**: `KData last=2026-06-18`,但今天是 `2026-06-25`.

**根因**: 已经好几天没跑 update,或上次 fetch 失败.

**修复**:

```bash
conda activate py312
python -m alpha_backend.datasource.cli update --type KData
# CLI 会从 last_date+1 一直补到今天(只补交易日)
```

### 6.3 `pytest tests/test_dict_sync.py` 失败

**症状**: 测试报 "field present in Pydantic but not in YAML" 或反之.

**根因**: YAML 改了但没跑 codegen,或 Pydantic 手改过但 YAML 没改.

**修复**: 按 YAML → codegen → test 的顺序重跑一遍:

```bash
conda activate py312
# 1. 确认 configs/data_dict/<Type>.yaml 是 source of truth
# 2. 重新生成
python -m alpha_backend.datasource.codegen
# 3. 重测
pytest tests/test_dict_sync.py -q
```

**严禁** 直接编辑 `alpha_backend/datasource/schemas/*.py`(会被 codegen 覆盖).

### 6.4 update 报 `failed=N`

**症状**: `KData update: success=N failed=K ...`.

**根因**: 行情源(sina / em / tx)对部分股票返回失败(常见于停牌股 / 新股).

**修复**:

1. 看输出里的 `failed` 列表,通常是 `stock_code` 字符串数组.
2. 多数情况是源端临时问题,过几小时重跑即可.
3. 如果反复失败,换源试试: `python -m alpha_backend.datasource.cli update --type KData --source em`.
4. 如果是数据真缺失(退市 / 长期停牌),确认 `Trade_Status.is_suspended` 是否长期为 True.

### 6.5 update 报 universe stale

**症状**: `WARN universe_sha mismatch` 类似信息.

**根因**: `Universe.parquet` 被重建过(例如 `init-universe`),数据层需要确认其他类型是否也跟着对齐.

**修复**: 重新生成所有日频数据,从 universe.ipo_date 之后开始:

```bash
conda activate py312
python -m alpha_backend.datasource.cli init-universe --index csi1000 --backdate 2020-01-01
python -m alpha_backend.datasource.cli update --type KData
python -m alpha_backend.datasource.cli update --type Trade_Status
python -m alpha_backend.datasource.cli update --type Stock_Basic_Data
```

详见 [`docs/platform-design.md`](platform-design.md) §10 / §11.

### 6.6 update 日志在哪

每次跑 `python -m alpha_backend.datasource.cli update --type KData`, CLI 会在仓库根目录 `./logs/` 下生成一个时间戳化的日志文件:

```
logs/kdata-update-20260626-153045.log
```

文件里包含这次 run 的 `[KData] daily_update: resume ...`、`failed` 列表、最终 `UpdateSummary` 等所有经 `logger` 输出的内容. 如果磁盘写不进去, helper 会退化为 stderr 警告而不阻塞业务. `Trade_Status` / `Stock_Basic_Data` / `Trading_Date` / `Universe` 的 update 当前**不**写日志文件 (留待后续 change 扩展).

`./logs/` 已在 `.gitignore` 中,不会污染仓库.

---

## 附录:常用命令速查

```bash
conda activate py312

# 状态
python -m alpha_backend.datasource.cli status

# 更新日频
python -m alpha_backend.datasource.cli update --type KData
python -m alpha_backend.datasource.cli update --type Trade_Status
python -m alpha_backend.datasource.cli update --type Stock_Basic_Data
python -m alpha_backend.datasource.cli update --type Trading_Date

# 更新静态(Universe)
python -m alpha_backend.datasource.cli init-universe --index csi1000 --backdate 2020-01-01

# 字段变更流程
python -m alpha_backend.datasource.codegen
pytest tests/test_dict_sync.py -q
```
