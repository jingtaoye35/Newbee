# 数据模块架构设计

> 本文件是 Newbee 数据模块开发与调用的**最高纲领**。所有数据相关代码、字段定义、状态记录、AI 协同规范均以此为准。修改本文件需经审阅。

## 1. 设计目标

- **存储独立**:每种数据类型独立 parquet/npy 文件,便于追溯与单点维护
- **状态可追踪**:每个数据文件有"最近成功更新"记录,支持增量更新与断点续传
- **字段有字典**:每个 parquet/npy 的字段含义、类型、单位有单一事实源
- **AI 可协同**:Claude Code 修改数据模块时,字典与状态自动同步,不脱节

## 2. 核心原则

1. **关注点分离**:`KData` 只管价格;`Trade_Status` 只管交易状态;`Adj_Factor` 只管复权;`Universe` 只管股票池
2. **long-format 优先**:K 线、复权因子、交易状态、PIT 均用 long format(一行 = 一个 stock 在一个时间点的全部字段)
3. **单一事实源**:字段定义从 `data_dict/*.yaml` 出发,codegen 出 Pydantic 与字典 markdown
4. **append-only**:`Universe` 与 `KData` 的行只增不删,universe 漂移不删旧行,只新增

## 3. 目录与文件布局

### 3.1 数据文件 `data/`

```
data/
├── KData.parquet              # 日频 K 线(无后缀 = 日频,后复权 close_post_adj)
├── KData_M1.parquet           # 1 分钟 K 线
├── KData_M5.parquet           # 5 分钟 K 线(预留)
├── Trade_Status.parquet       # 交易状态(suspended / ST / activate)
├── Adj_Factor.parquet         # 复权因子
├── Universe.parquet           # 股票池
├── PIT.parquet                # 财务披露
│
├── Features/                  # npy 矩阵,保持现状
│   └── <FactorName>/{YYYY-MM-DD}.npy
├── Alpha/                     # npy 矩阵,保持现状
│   └── <StrategyId>/{YYYY-MM-DD}.npy
│
├── _Manifest/                 # 隐藏目录(下划线前缀保留)
│   ├── Data_State.json        # per-type 覆盖状态
│   └── Data_Dict_Index.json   # 字典索引
│
├── models/                    # ⛔ 不在数据模块范围,保持小写
└── repr/                      # ⛔ 不在数据模块范围,保持小写
```

### 3.2 字段字典 `data_dict/`

```
data_dict/
├── KData.yaml
├── KData_M1.yaml
├── KData_M5.yaml
├── Trade_Status.yaml
├── Adj_Factor.yaml
├── Universe.yaml
├── PIT.yaml
└── Features_Alpha.yaml
```

### 3.3 字典 markdown 文档 `docs/data_dict/`

```
docs/data_dict/
├── KData.md
├── KData_M1.md
├── KData_M5.md
├── Trade_Status.md
├── Adj_Factor.md
├── Universe.md
├── PIT.md
└── Features_Alpha.md
```

### 3.4 Python 模块 `newbee/datasource/`

> 命名采用 `datasource`(单数),与 `data/` 物理目录区分。

```
newbee/datasource/
├── schemas/                   # codegen 产物,Pydantic BaseModel
│   ├── kdata.py
│   ├── kdata_m1.py
│   ├── kdata_m5.py
│   ├── trade_status.py
│   ├── adj_factor.py
│   ├── universe.py
│   ├── pit.py
│   └── features_alpha.py
├── codegen.py                 # YAML → Pydantic → markdown
├── registry.py                # DataRegistry
├── storage/
│   ├── io.py                  # DataFile(读 / upsert / append / stats)
│   └── state.py               # StateTracker
├── sources/akshare.py         # 数据源适配器
├── incremental.py             # 增量更新编排
├── calendar.py                # 交易日历(沿用)
└── ...
```

## 4. 命名规范

### 4.1 强制 Pascal_Snake_Case 的位置

- `data/` 及其所有子层:目录名、文件名
- `data_dict/` repo 根:所有 `.yaml`
- `docs/data_dict/`:所有 `.md`

### 4.2 规则细节

- 单单词:直接大写(`Universe`、`PIT`、`Features`、`Alpha`)
- 多单词:每个单词首字母大写,下划线连接(`KData`、`Trade_Status`、`Adj_Factor`)
- 例外 1:`_Manifest/` 保留下划线前缀(隐藏目录语义)
- 例外 2:日期文件 `{YYYY-MM-DD}.npy` 保留 ISO 格式(数据值,非名字)
- 例外 3:Python 代码 `newbee/datasource/**/*.py` 保持 **snake_case**(PEP 8)

### 4.3 频率后缀约定

| 文件名 | 频率 |
|---|---|
| `KData.parquet` | 日频(默认,无后缀) |
| `KData_M1.parquet` | 1 分钟 |
| `KData_M5.parquet` | 5 分钟 |
| `KData_M15.parquet` | 15 分钟(未来) |
| `KData_H1.parquet` | 60 分钟(未来) |

YAML 中以 `frequency` 字段显式标注,避免歧义。

### 4.4 股票代码格式

`stock_code` 统一为 **9 位字符串**:`{6 位数字}.{SH|SZ}`。

| 段位 | 含义 | 示例 |
|---|---|---|
| 前 6 位 | 证券代码(左补 0) | `600000`、`000012`、`300750` |
| 7-9 位 | 交易所后缀(`.SH` / `.SZ`) | `.SH`(沪市)、`.SZ`(深市) |

判定规则:代码以 `6` 或 `9` 开头 → `.SH`;以 `0` / `3` 开头 → `.SZ`。未来如需支持北交所(`.BJ`)再扩展。

## 5. 字段字典三件套

每个数据类型的字段定义由三个文件协同表达:

```
data_dict/KData.yaml                  ← source of truth(人/AI 编辑入口)
        │ codegen
        ▼
newbee/datasource/schemas/kdata.py    ← Pydantic BaseModel(运行时强校验)
        │ reflect
        ▼
docs/data_dict/KData.md               ← 人类可读文档(自动生成)
```

**变更流程**:

1. 改 `data_dict/<Type>.yaml`
2. 跑 `python -m newbee.datasource.codegen` 自动重生 Pydantic 与字典 markdown
3. 跑 `pytest tests/test_dict_sync.py -q` 双向校验必须通过
4. 提交时 YAML + 生成的 Pydantic + 字典 markdown 三件套一起入

## 6. 核心类型字段契约

### 6.1 KData(日频)

| 字段 | 类型 | 单位 | nullable | 含义 |
|---|---|---|---|---|
| `trading_date` | string | 10 位 | N | 实际发生交易的日期, 格式为`YYYY-MM-DD`,如 `2026-01-06` |
| `stock_code` | string | 9 位 | N | A 股证券代码,如 `600000.SH`、`000012.SZ` |
| `open` | float32 | 元 | Y | 开盘价(未复权) |
| `high` | float32 | 元 | Y | 最高价(未复权) |
| `low` | float32 | 元 | Y | 最低价(未复权) |
| `close` | float32 | 元 | Y | 收盘价(未复权) |
| `amount` | float32 | 元 | Y | 成交额 |
| `volume` | float32 | 股 | Y | 成交量 |
| `close_post_adj` | float32 | 元 | Y | 后复权收盘价 |

主键:`(trading_date, stock_code)`

**复权约定**:`close_post_adj = close * adj_factor`(由源数据直接给出,不再用前复权价格)。OHLCV 全部以**未复权**形态存储,前复权价格如需,自行由 `close_post_adj / adj_factor` 推导。

### 6.2 Trade_Status

| 字段 | 类型 | nullable | 含义 |
|---|---|---|---|
| `trading_date` | string | N | 10 位代码, 交易日, 格式为`YYYY-MM-DD`,如 `2026-01-06` |
| `stock_code` | string | N | 9 位代码,如 `600000.SH` |
| `is_suspended` | bool | N | 当日是否停牌 |
| `is_st` | bool | N | 当日是否为 ST/*ST |
| `is_activate` | bool | N | 当日是否为活跃(上市未退市) |

主键:`(trading_date, stock_code)`

### 6.3 Adj_Factor

| 字段 | 类型 | nullable | 含义 |
|---|---|---|---|
| `trading_date` | string | N | 10 位代码, 交易日, 格式为`YYYY-MM-DD`,如 `2026-01-06` |
| `stock_code` | string | N | 9 位代码 |
| `adj_factor` | float64 | N | 复权因子 |

约定:`close_post_adj = close * adj_factor`(在 YAML 字典里写明,避免歧义)。`adj_factor` 保留 **float64** 以防长周期复权因子累加精度损失。

主键:`(trading_date, stock_code)`

### 6.4 Universe

| 字段 | 类型 | nullable | 含义 |
|---|---|---|---|
| `stock_index` | int32 | N | 单调递增索引,append-only,永不回收 |
| `stock_code` | string | N | 9 位代码,如 `600000.SH` |
| `ipo_date` | string | N | 10 位代码, 交易日, 格式为`YYYY-MM-DD`,如 `2026-01-06` |

`active_mask(asof)` 推算:`trading_date >= ipo_date` 即为当时已上市。退市股仍保留在 Universe 中,只追加、不删除。

## 7. 状态追踪 `data/_Manifest/Data_State.json`

替代旧 `fetch_state.json`,per-type 粒度,带 `schema_version`:

```json
{
  "version": "1.0",
  "universe_sha": "<16-char hex>",
  "types": {
    "KData": {
      "schema_version": "1.0",
      "frequency": "daily",
      "first_date": "2020-01-02",
      "last_date": "2026-06-19",
      "row_count": 6000000,
      "stock_count": 1000,
      "updated_at": "2026-06-19T16:00:00+08:00"
    },
    "Trade_Status": { "...": "..." },
    "Adj_Factor":   { "...": "..." },
    "Universe":     { "...": "..." },
    "PIT":          { "...": "..." }
  }
}
```

**强约束**:`DataFile.read()` 启动时强制 `state.types[<type>].schema_version == schemas/<type>.schema_version`,不一致直接抛 `SchemaVersionError`,避免悄悄读到不兼容的旧数据。

## 8. 核心 API

> 本节定义业务代码与数据模块交互的全部接口。所有方法必须经过 codegen 测试 + dict_sync 测试 + 单元测试三道关卡。

### 8.1 模块职责划分

```
newbee/datasource/
├── registry.py          # DataType + DataRegistry(类型元数据)
├── schemas/*.py         # Pydantic BaseModel(codegen 产物)
├── storage/
│   ├── io.py            # DataFile(物理 parquet 读写)
│   ├── state.py         # StateTracker(Data_State.json)
│   └── errors.py        # 异常体系
├── service/             # 高层 facade(可选,每 type 一个文件)
│   ├── kdata.py         # KDataService:DataFile + StateTracker + 业务编排
│   ├── trade_status.py
│   ├── adj_factor.py
│   └── ...
├── codegen.py           # YAML → Pydantic + markdown
├── sources/akshare.py   # 数据源适配器
├── incremental.py       # 增量更新编排
└── calendar.py          # 交易日历
```

调用层级(自上而下):

```
业务代码 → Service(facade) → DataFile / StateTracker → pyarrow / json
                       ↓
                 DataRegistry(查 DataType 元数据)
```

### 8.2 DataType 与 DataRegistry(`registry.py`)

```python
@dataclass(frozen=True)
class DataType:
    """一个数据类型的元数据. frozen 保证注册后不可改."""

    name: str                          # "KData" / "Adj_Factor"
    schema_version: str                # "1.0"
    frequency: str                     # "daily" | "1min" | "5min" | ...
    storage_path: Path                 # data/KData.parquet
    primary_key: tuple[str, ...]       # ("trading_date", "stock_code")
    pydantic_model: type[BaseModel]    # KData / Adj_Factor / ...

    def __post_init__(self) -> None:
        """校验 name 唯一性、storage 路径符合 Pascal_Snake_Case."""
        ...

class DataRegistry:
    """全局单例. 启动时一次性 register, 运行时只读."""

    def __init__(self) -> None:
        self._types: dict[str, DataType] = {}

    def register(self, dtype: DataType) -> None:
        """注册一个 DataType. 同名重复抛 ValueError."""
        if dtype.name in self._types:
            raise ValueError(f"DataType {dtype.name!r} 已注册")
        self._types[dtype.name] = dtype

    def get(self, name: str) -> DataType:
        """取 DataType. 不存在抛 KeyError."""
        if name not in self._types:
            raise KeyError(f"DataType {name!r} 未注册. 已注册: {list(self._types)}")
        return self._types[name]

    def all(self) -> list[DataType]:
        """返回全部已注册类型, 按 name 排序."""
        return sorted(self._types.values(), key=lambda d: d.name)

    def by_frequency(self, frequency: str) -> list[DataType]:
        """按频率筛. 例: by_frequency('daily') → [KData, Trade_Status, ...]"""

# 启动时一次性 register, 文件末尾追加即可
REGISTRY = DataRegistry()
REGISTRY.register(KDATA)         # 1.0
REGISTRY.register(KDATA_M1)      # 1.0
REGISTRY.register(TRADE_STATUS)
REGISTRY.register(ADJ_FACTOR)
REGISTRY.register(UNIVERSE)
```

**调用示例**:

```python
from newbee.datasource.registry import REGISTRY

# 业务代码取元数据
dtype = REGISTRY.get("KData")
print(dtype.primary_key)        # ('trading_date', 'stock_code')
print(dtype.storage_path)       # PosixPath('data/KData.parquet')

# 列出所有 daily 类型
for d in REGISTRY.by_frequency("daily"):
    print(d.name, d.schema_version)
```

### 8.3 DataFile(`storage/io.py`)

> 单一文件级别的物理读写封装. 所有写入走 Pydantic 强校验,所有读取走 schema_version 强校验。

```python
@dataclass(frozen=True)
class CoverageStats:
    """一个数据文件的物理统计."""

    type_name: str
    schema_version: str
    frequency: str
    first_date: str | None          # ISO "YYYY-MM-DD" 或 None
    last_date: str | None           # ISO "YYYY-MM-DD" 或 None
    row_count: int
    stock_count: int                # distinct stock_code
    file_size_bytes: int
    file_sha256: str                # 16 位
    updated_at: str                 # ISO datetime

class DataFile:
    """一种数据类型的物理文件封装."""

    def __init__(self, dtype: DataType) -> None:
        self.dtype = dtype
        self.path = dtype.storage_path
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    # ---------- 读 ----------

    def read(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        stock_codes: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """按条件读数据.

        Args:
            start: 起始交易日(含),ISO 格式 "YYYY-MM-DD". None 表示不限下界.
            end: 截止交易日(含),ISO 格式 "YYYY-MM-DD". None 表示不限上界.
            stock_codes: 股票代码白名单. None 表示全部.
            columns: 列名白名单. None 表示全部.

        Returns:
            DataFrame, 严格按 (trading_date, stock_code) 升序.
            列顺序与 dtype.pydantic_model 字段顺序一致.

        Raises:
            FileNotFoundError: parquet 文件不存在.
            SchemaVersionError: 磁盘文件的 schema_version 与 dtype.schema_version 不一致.
            ValueError: start > end 或日期格式非法.

        实现要点:
            - pyarrow.dataset 谓词下推(start/end/stock_codes 转 filter)
            - 列裁剪(pyarrow 读取时只 IO 所需列)
            - 返回前用 Pydantic 校验一次, 防止磁盘数据被人手改损坏
        """
        ...

    def exists(self) -> bool:
        """文件存在且 schema_version 匹配 dtype.schema_version."""
        ...

    def stats(self) -> CoverageStats:
        """扫一次文件算出全部统计.

        实现要点:
            - file_size_bytes: os.path.getsize
            - file_sha256: hashlib.sha256 分块读, 取前 16 位
            - first_date / last_date: pyarrow 读 date 列做 min/max
            - stock_count: distinct stock_code
            - row_count: pyarrow.parquet metadata
        """
        ...

    # ---------- 写 ----------

    def append(self, df: pd.DataFrame) -> int:
        """追加新行(假设无主键冲突).

        Args:
            df: 必须包含 dtype.primary_key 全部列, 列类型必须与 Pydantic 匹配.

        Returns:
            实际写入行数(去重前).

        Raises:
            PrimaryKeyConflictError: 磁盘已存在 df 中某 (key) 组合 → 改用 upsert.
            SchemaValidationError: df 列与 Pydantic 不一致.
            ValueError: df 为空.

        实现要点:
            - Pydantic 校验全部行
            - 用 pyarrow.ParquetWriter 追加 row group(避免整文件重读)
            - 文件不存在时先写表头再追加
            - 文件锁(fcntl)防并发写
        """
        ...

    def upsert(
        self,
        df: pd.DataFrame,
        *,
        conflict: Literal["replace", "ignore", "error"] = "replace",
    ) -> int:
        """按主键 upsert.

        Args:
            df: 同 append.
            conflict:
                - "replace"(默认): 新行覆盖磁盘旧行
                - "ignore": 冲突时跳过(返回 0)
                - "error": 冲突时抛 PrimaryKeyConflictError

        Returns:
            受影响行数(新插入 + 被覆盖的合计).

        Raises:
            PrimaryKeyConflictError: conflict='error' 且有冲突.
            SchemaValidationError: df 列与 Pydantic 不一致.

        实现要点:
            - 读磁盘全表 → 与 df concat → 按 primary_key drop_duplicates(keep='last')
            - 重写整文件(行数小时可接受; 后续可优化为 in-place 替换)
        """
        ...

    def truncate(self) -> None:
        """清空文件(用于推倒重来 / 重建). 调用方负责确认."""
        ...
```

**调用示例**:

```python
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile

dtype = REGISTRY.get("KData")
file_ = DataFile(dtype)

# 1. 读
df = file_.read(
    start="2024-01-01", end="2024-12-31",
    stock_codes=["600000.SH", "000012.SZ"],
    columns=["trading_date", "stock_code", "close", "close_post_adj"],
)

# 2. 增量 append(每日 fetch 后)
new_rows = fetch_today_kdata()  # 返回 Pydantic 校验过的 DataFrame
n = file_.append(new_rows)

# 3. upsert(修复历史数据)
n = file_.upsert(corrected_df, conflict="replace")

# 4. stats
stats = file_.stats()
print(f"rows={stats.row_count}, range={stats.first_date}~{stats.last_date}")
```

### 8.4 StateTracker(`storage/state.py`)

> `Data_State.json` 的原子读写封装. 不感知具体业务, 只做 KV 持久化。

```python
@dataclass(frozen=True)
class DataTypeState:
    """Data_State.json 里单个 type 的状态条目."""

    type_name: str
    schema_version: str
    frequency: str
    first_date: str | None
    last_date: str | None
    row_count: int
    stock_count: int
    updated_at: str

class StateTracker:
    """全局 Data_State.json 读写.

    线程/进程安全:
        - 读: 无锁(原子读 atomic rename 后的文件)
        - 写: fcntl 文件锁 + tempfile + os.replace
    """

    def __init__(self, state_path: Path = Path("data/_Manifest/Data_State.json")) -> None:
        self.path = state_path
        self._lock_path = state_path.with_suffix(state_path.suffix + ".lock")

    def read(self) -> dict[str, DataTypeState]:
        """读全部 type 状态. 文件不存在返回空 dict."""
        if not self.path.exists():
            return {}
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            name: DataTypeState(
                type_name=name,
                schema_version=entry["schema_version"],
                frequency=entry.get("frequency", "daily"),
                first_date=entry.get("first_date"),
                last_date=entry.get("last_date"),
                row_count=int(entry.get("row_count", 0)),
                stock_count=int(entry.get("stock_count", 0)),
                updated_at=entry.get("updated_at", ""),
            )
            for name, entry in payload.get("types", {}).items()
        }

    def update(
        self,
        type_name: str,
        stats: CoverageStats,
        *,
        universe_sha: str | None = None,
    ) -> DataTypeState:
        """原子更新单个 type 的状态(其他 type 不动).

        Args:
            type_name: 例如 "KData"
            stats: 由 DataFile.stats() 算出的最新统计
            universe_sha: 可选, 若传入则更新顶层 universe_sha

        Returns:
            更新后的 DataTypeState.

        Raises:
            SchemaVersionError: stats.schema_version 与已记录的不一致(防止悄悄覆盖).

        实现要点:
            - 读旧 state → 校验 type_name 的 schema_version 与 stats 一致(或旧值缺失)
            - 写新 state 到 tempfile → os.replace
            - 整体 fcntl 锁保护
        """
        ...

    def resume_range(
        self,
        type_name: str,
        *,
        latest: str,
    ) -> tuple[str, str]:
        """推断 type 的待补区间.

        Args:
            type_name: 例如 "KData"
            latest: 本次允许拉到的最大交易日, ISO "YYYY-MM-DD"

        Returns:
            (start, end). 区间为空时 start > end(返回 latest+1, latest).

        推断逻辑:
            1. state.types[type_name].last_date 存在 → start = last_date + 1
            2. 否则扫 DataFile.stats() 兜底
            3. 都没有 → start = 默认起点(2020-01-01 或 Universe.ipo_date)
        """
        ...

    def is_universe_stale(self, current_universe_sha: str) -> bool:
        """判断 Universe 是否需要重建(顶层 universe_sha 与当前不一致)."""
        ...
```

**调用示例**:

```python
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile
from newbee.datasource.storage.state import StateTracker

dtype = REGISTRY.get("KData")
file_ = DataFile(dtype)
tracker = StateTracker()

# 1. 推断增量区间
latest = "2026-06-23"
start, end = tracker.resume_range("KData", latest=latest)
if start > end:
    print("KData 已 up-to-date")
else:
    print(f"待补区间: {start} ~ {end}")

# 2. 拉数据 + 写盘
new_df = fetch_kdata(start, end)
file_.append(new_df)

# 3. 算 stats + 更新 state
stats = file_.stats()
tracker.update("KData", stats)
```

### 8.5 Service(facade,可选) — `service/<type>.py`

> 当业务逻辑需要"读 + state 校验 + 自动更新"组合时,用 Service 简化。简单场景可直接用 DataFile + StateTracker。

```python
class KDataService:
    """KData 类型的高层服务.

    典型用法:
        service = KDataService()
        df = service.read_window("2024-01-01", "2024-12-31")
        result = service.daily_update(today="2026-06-23")
    """

    def __init__(self) -> None:
        self.dtype = REGISTRY.get("KData")
        self.file = DataFile(self.dtype)
        self.tracker = StateTracker()

    def read_window(
        self,
        start: str,
        end: str,
        *,
        stock_codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """读窗口数据, 自动校验 schema_version."""
        self._assert_schema_fresh()
        return self.file.read(start=start, end=end, stock_codes=stock_codes)

    def daily_update(
        self,
        *,
        today: str | None = None,
        source: str = "sina",
    ) -> UpdateResult:
        """执行日终增量更新.

        步骤:
            1. latest_trading_day(today) → 锁定 latest
            2. tracker.resume_range → 推断 start
            3. fetch_kdata(start, latest, source) 拉数据
            4. file_.append(new_df) 写盘
            5. tracker.update 持久化状态
            6. 返回 UpdateResult(success_count, failed_list, elapsed_sec)
        """
        ...

    def full_init(self, start: str = "2020-01-01") -> None:
        """首次初始化(推倒重来场景):全量重拉 + 重建."""
        self.file.truncate()
        # ... 全量拉, 周期性 batch append
        self.tracker.update("KData", self.file.stats())

    def _assert_schema_fresh(self) -> None:
        """读盘前断言:磁盘文件 schema_version == dtype.schema_version."""
        state = self.tracker.read().get("KData")
        if state is None:
            raise SchemaVersionError("KData state 未初始化, 请先跑 daily_update")
        if state.schema_version != self.dtype.schema_version:
            raise SchemaVersionError(
                f"KData state.schema_version={state.schema_version} "
                f"!= code={self.dtype.schema_version}. 请 bump schema_version 并迁移"
            )
```

### 8.6 异常体系(`storage/errors.py`)

```python
class DataSourceError(Exception):
    """数据层错误的基类."""

class StorageError(DataSourceError):
    """存储层 IO 错误基类."""

class SchemaVersionError(StorageError):
    """schema_version 不匹配."""

class SchemaValidationError(StorageError):
    """Pydantic 校验失败(列缺失/类型错/null 违反 nullable)."""

class PrimaryKeyConflictError(StorageError):
    """append 时主键冲突(应改用 upsert)."""

class ManifestMismatchError(StorageError):
    """universe_sha 校验失败."""

class StateCorruptedError(DataSourceError):
    """Data_State.json 文件损坏或 schema 漂移."""
```

### 8.7 并发与一致性

| 场景 | 行为 |
|---|---|
| 多进程同时 `append` 同一文件 | `fcntl.flock` 互斥, 后到者等待; 写入走 tempfile + rename, 不会半写 |
| 多进程同时 `update` Data_State.json | 同上, fcntl 锁 + tempfile + rename |
| `read` 与 `write` 并发 | 读侧无锁; 写侧 rename 是原子的, 读侧要么读到旧版本要么新版本 |
| `truncate` 与 `read` 并发 | truncate 不加锁(由调用方保证); 文档明示"truncate 需停止所有 read" |
| 跨机器并发 | **不支持**, 数据模块设计为单机; 跨机需引入分布式存储(后续路线) |

### 8.8 完整工作流示例

**场景 A:回测读取**

```python
from newbee.datasource.service.kdata import KDataService

service = KDataService()
df = service.read_window(
    start="2020-01-01", end="2024-12-31",
    stock_codes=["600000.SH", "000012.SZ"],
    # columns 可选, 不传返回全部
)
# df 自动校验过 Pydantic + schema_version
```

**场景 B:每日增量更新**

```python
from newbee.datasource.service.kdata import KDataService

service = KDataService()
result = service.daily_update(today="2026-06-23", source="sina")
print(f"success={result.success}, failed={result.failed}, elapsed={result.elapsed_sec}s")
```

**场景 C:首次初始化(推倒重来)**

```python
from newbee.datasource.service.kdata import KDataService

service = KDataService()
service.full_init(start="2020-01-01")  # 全量重拉 + 重建
```

**场景 D:PIT 数据 upsert(财报披露后修订)**

```python
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile

dtype = REGISTRY.get("PIT")
file_ = DataFile(dtype)
n = file_.upsert(corrected_pit_df, conflict="replace")
print(f"upsert {n} rows into PIT")
```

## 9. AI 协同机制

### 9.1 变更 SOP(`.claude/rules/data-sop.md`)

| 场景 | 步骤 |
|---|---|
| **改字段** | 改 `data_dict/<Type>.yaml` → 跑 codegen → 跑 dict_sync 测试 |
| **新增 type** | 新建 YAML → 跑 codegen → 在 `registry.py` 末尾追加 register 行 |
| **schema 不兼容变更** | YAML 里 bump `schema_version` → 写迁移脚本(若需) |
| **改 `Data_State.json`** | 仅由 `StateTracker.update()` 写;禁止手改 |

严禁:直接改 Pydantic 字段、在代码里硬编码字段名字符串、跳过 PostToolUse hook、改字段语义不 bump `schema_version`。

### 9.2 PostToolUse Hook(`.claude/settings.json`)

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "bash .claude/hooks/post_edit_data_dict.sh"
          }
        ]
      }
    ]
  }
}
```

`.claude/hooks/post_edit_data_dict.sh` 触发条件:改 `data_dict/*.yaml` 或 `newbee/datasource/schemas/*.py` 时跑 codegen + 字典校验。漂移在 hook 阶段被 CI/本地拦截。

### 9.3 字典双向校验 `tests/test_dict_sync.py`

- YAML 字段 ⊆ Pydantic 字段
- Pydantic 字段 ⊆ YAML 字段
- YAML `storage` 路径与磁盘实际文件存在性一致
- 字典 markdown 字段与 Pydantic 一致

## 10. 变更流程(端到端)

```
  人 / Claude Code 改 YAML
        │
        ▼
  git diff YAML(易审)
        │
        ▼
  PostToolUse hook 触发
   ├── python -m newbee.datasource.codegen
   │     ├── 生成 Pydantic BaseModel
   │     └── 生成字典 markdown
   └── pytest tests/test_dict_sync.py -q
        │
        ▼
  写盘时 Pydantic 强校验(类型 / 范围 / nullable)
        │
        ▼
  CI: 反向校验 + schema_version 一致性
```

## 11. 风险与限制

| 风险 | 缓解 |
|---|---|
| 单文件 append 性能(整文件重写) | `pyarrow.ParquetWriter` 增量写 row group,避免全量重读 |
| `schema_version` 漏改 | `DataFile.read()` 启动时强制校验,不匹配直接抛错 |
| features / alpha npy 与新字典脱节 | YAML 里加 `npy_class` 轻量定义,字段含义从 docstring 同步 |
| PyArrow ↔ Pandas 类型漂移 | codegen 内置 `pyarrow.type` → `pd.dtype` 单一映射表 |
| 旧 per-stock 数据残留 | 旧 `data/raw/ data/adj/` 保留不读;新代码不引用;必要时手动 `rm -rf` |
| 全量重拉耗时 | 1000 只 × 2000 交易日,sina 源约 30-60 分钟,首次跑接受 |
| float32 精度 | OHLCV 用 float32,单只股票价格精度 ~1e-7,够用;`adj_factor` 保留 float64 防累加漂移 |

## 12. 退役与回退

- 旧 per-stock 目录(`data/raw/`、`data/adj/`)保留 1 个迭代周期,确认新代码稳定后由人手动删除
- 旧 `data/_manifest/fetch_state.json` 不再写入,新代码只写 `Data_State.json`
- 旧 `data/universe/pool.parquet` 在新 `Universe.parquet` 完整生成后由人手动删除
- 旧 `data/pit/`、`data/features/`、`data/alpha/` 子目录同理
- 旧 Python 包 `newbee/data/` 在 `newbee/datasource/` 上线后停止 import,迭代 1 个版本后人手动 `rm -rf`

## 13. 后续路线(M2+ 暂列)

- `PIT.parquet` 字段与 PIT 适配器实现
- `Features_Alpha` 字典补全(因子 metadata 标准化)
- Parquet dataset + 分区(若 single file 性能不达标)
- DuckDB 替代 pyarrow 读侧(若 SQL 接口需求增长)
- 北交所(`.BJ`)扩展

---

**版本**:1.2
**生效日期**:2026-06-24
**本次变更**:§8 核心 API 详细化(DataType/DataRegistry/DataFile/StateTracker/Service/异常/并发/示例)
**生效日期**:2026-06-24
**维护者**:Newbee 数据架构组
**变更方式**:修改本文件需经审阅,变更 SOP 同 §9
