# Newbee Platform Architecture Design

> **Scope**: any modification to `newbee/`, `configs/`, `scripts/`, `docs/*.py`.
> **Replaces** (consolidated 2026-06-24): the former `docs/data-architecture.md`, whose full content now lives in §10 of this file.

## 0. Document Metadata

- **Version**: 2.0
- **Effective date**: 2026-06-24
- **Authors**: Newbee Architecture Group
- **Change protocol**: small edits direct; large structural changes go through OpenSpec (`opsx:propose`).

## 1. Design Goals

Newbee is a **self-built, verifiable, minimal** quantitative research / backtest platform. Three core goals, ordered by priority:

1. **End-to-end readability** — no closed-source black boxes; every step on the critical path (data → factor → portfolio → backtest) is readable in code.
2. **Data reproducibility** — given the same universe + date range, results are bit-identical across runs and machines.
3. **AI-collaborative maintenance** — Claude Code modifications are bounded by strong constraints that prevent schema / contract / convention drift.

Out of scope for the current milestone: live trading (M2+), factor factory (M3+), multi-strategy portfolio (M4+).

## 2. Milestone Status

| Milestone | Scope | Status |
|---|---|---|
| **M1a** | Skeleton + data layer (legacy `StockPool`) | ✅ |
| **M1b** | First factor (`momentum_20`) + alpha backtest | ✅ |
| **M1c** | Portfolio backtest (mean-variance + cost + turnover constraints) | ✅ |
| **M1d** | Closed loop + protection (integration tests + docs) | ✅ |
| **M2** | Data module refactor (long-format parquet + YAML SoT + registry + services) | ✅ (merged 2026-06-24) |
| M2+ | Live trading (broker adapter, order management, risk monitoring) | ⏳ |
| M3+ | Factor factory (ML, NLP, alternative data) | ⏳ |
| M4+ | Multi-strategy portfolio (capital allocation, meta-strategy) | ⏳ |

## 3. Platform Layers & Call Relationships

```
                    ┌─────────────────────────────────┐
   User / Script    │  newbee CLI · scripts/*.py     │   ← Entry Layer
                    │  docs/*.py (Jupyter-style tutorials)
                    └────────────┬────────────────────┘
                                 │
                    ┌────────────▼────────────────────┐
   Config Layer     │  configs/{factors,strategies}/*.yaml
                    │  + newbee.utils.config loader
                    └────────────┬────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
   ┌────▼────────┐         ┌─────▼──────┐         ┌──────▼──────┐
   │ DataSource  │         │   Factor   │         │  Portfolio  │   ← Domain Layer
   │  (M2)       │         │            │         │             │
   │ registry    │         │ base       │         │ state       │
   │ storage     │         │ pipeline   │         │ constraints │
   │ state       │         │ registry   │         │ cost        │
   │ service     │         │ classic/*  │         │ optimizers  │
   │ codegen     │         └─────┬──────┘         └──────┬──────┘
   │ calendar    │               │                       │
   │ sources/*   │               │                       │
   └────┬────────┘               │                       │
        │                        │                       │
        │               ┌────────▼───────────────────────┘
        │               │
   ┌────▼────────┐  ┌───▼──────────┐  ┌──────────────────┐
   │  pyarrow    │  │  alpha_store │  │   engines/       │   ← Engine Layer
   │  parquet    │  │  npy cache   │  │ backtest_alpha   │
   │  json       │  │              │  │ backtest_portfolio│
   └─────────────┘  └──────────────┘  └────────┬─────────┘
                                                │
                                ┌───────────────▼──────────────┐
   Storage (data/)              │  data/KData.parquet          │   ← Persistence Layer
                                │  data/Trade_Status.parquet   │
                                │  data/Stock_Basic_Data.parquet     │
                                │  data/Universe.parquet       │
                                │  data/Trading_Date.csv       │
                                │  data/_Manifest/Data_State.json│
                                │  data/alpha/<sid>/{date}.npy │
                                │  data/portfolio/results/*.parquet│
                                └──────────────────────────────┘
```

**Call rules (hard constraints)**:

- The entry layer calls the domain layer and the engine layer only — never the persistence layer directly.
- Domain layers do **not** call each other: `factor` does not import `portfolio`; `portfolio` does not import `data` business modules.
- The data layer is infrastructure for every other layer, but the reverse is forbidden.
- The engine layer (`engines/`) orchestrates domain + data layers, produces final results, writes them back to persistence.

## 4. Module Manifest & Responsibilities

### 4.1 Data Layer — `newbee/datasource/`

> See §10 for deep details (directory layout, field contracts, API surface, error hierarchy, change flow).

| File / Directory | Responsibility |
|---|---|
| `registry.py` | `DataType` + `DataRegistry` (frozen dataclass + singleton) |
| `codegen.py` | YAML → Pydantic + Markdown |
| `calendar.py` | Trading-day calendar (wrapper around `exchange_calendars`) |
| `cli.py` | `newbee data` subcommands (status / update / init-universe / codegen / verify) |
| `schemas/*.py` | Pydantic `BaseModel` (codegen output — **must not be hand-edited**) |
| `storage/io.py` | `DataFile` physical I/O (predicate pushdown, atomic write) |
| `storage/state.py` | `StateTracker` persistence for `Data_State.json` |
| `storage/errors.py` | Error hierarchy (`SchemaVersionError`, `PrimaryKeyConflictError`, ...) |
| `sources/akshare.py` | Source adapter (`sina` / `em` / `tx`) |
| `service/<type>.py` | Per-type facade (`KDataService` / `UniverseService` / ...) |

### 4.2 Factor Layer — `newbee/factors/`

> A factor is a pure function `(prices, asof) -> ndarray(N,)`. Cross-sectional output; `NaN` means inactive.

| File | Responsibility |
|---|---|
| `base.py` | `Factor` Protocol + `FactorSpec` + `SimpleFactor` wrapper + utilities (`rank_` / `standardize`) |
| `registry.py` | `@register` decorator, in-memory factor table (`name` → `SimpleFactor`) |
| `pipeline.py` | Multi-factor matrix computation + cross-sectional standardization + neutralization |
| `classic/momentum.py` | Built-in classic factor (`momentum_20`) |

**Contracts (hard constraints)**:

1. `factor.compute(asof, ...) -> ndarray(N,)` — shape is strictly `universe.size()`.
2. `NaN` positions = inactive / missing data — **never** fill with 0.
3. **Strictly no look-ahead** — output at time `t` may only depend on data available at `≤ t`.
4. Register via `@register`; no global state. A new factor = one file under `classic/<name>.py`.

### 4.3 Portfolio Layer — `newbee/portfolio/`

| File | Responsibility |
|---|---|
| `state.py` | `PortfolioState` (positions + cash + history) + `Trade` rebalance record |
| `constraints.py` | `LongOnly` / `WeightSum` / `MaxTurnover` / `MaxWeight` projection |
| `cost.py` | `CostModel` (commission + slippage + stamp tax) |
| `optimizers/mean_variance.py` | Mean-variance optimization (Ledoit-Wolf shrinkage covariance) |
| `optimizers/equal_weight.py` | Equal-weight (baseline) |
| `optimizers/inverse_vol.py` | Inverse volatility |

**Contracts (hard constraints)**:

1. Optimizer signature: `(mu, cov, current_weights, constraints) -> target_weights (ndarray(N,))`.
2. `PortfolioState.positions` is `ndarray(N,)` — **not** share counts; M1 does not model share counts.
3. Cost is deducted on rebalance days (after NAV accumulation); no circular interaction with returns.

### 4.4 Engine Layer — `newbee/engines/`

| File | Responsibility |
|---|---|
| `backtest_alpha.py` | Alpha backtest: IC / RankIC / decile returns, speed-first |
| `backtest_portfolio.py` | Portfolio backtest: state machine + rebalance + cost, fidelity-first |

**Two-phase backtest design**:

| Phase | Goal | Data shape | Speed | Fidelity |
|---|---|---|---|---|
| Phase A: Alpha | Evaluate single-factor predictive power | `(T, N)` matrix | ⭐⭐⭐ (numpy-vectorized) | ⭐ (no cost / no turnover) |
| Phase B: Portfolio | Evaluate live-trading attainability | state machine | ⭐ (daily iteration) | ⭐⭐⭐ (cost / turnover / rebalance constraints) |

**Result file conventions**:

- `data/portfolio/results/<strategy>_<version>_nav.parquet` — Phase B NAV curve
- `data/alpha/<strategy_id>/{YYYY-MM-DD}.npy` + `manifest.json` — Phase A cache

### 4.5 Utility Layer — `newbee/utils/`

| File | Responsibility |
|---|---|
| `__init__.py` | Exports `logger` proxy (`from newbee.utils import logger`) |
| `_logger.py` | Proxy implementation (pulls `__name__` from the call stack, attaches handler on first access) |
| `config.py` | YAML loading + path constants + `strategy_id()` derivation |

**Logger convention (hard constraint)**:

```python
# ✅ Correct — go through the proxy
from newbee.utils import logger
logger.info("KData updated", extra={"rows": n})

# ❌ Wrong
import logging
logger = logging.getLogger(__name__)  # causes duplicate output
print(...)                              # not captured by tests
```

Default format: `%(asctime)s | %(levelname)-7s | %(name)s | %(message)s`; override via the `LOG_FORMAT` env var.

### 4.6 Entry Layer — `newbee/cli.py` + `scripts/*.py`

```
newbee (CLI entry, registered in pyproject.toml)
  ├── newbee backtest <config>        # Portfolio backtest
  ├── newbee alpha   <config>         # Alpha backtest
  └── newbee data    <sub>            # Data subcommands (forwards to newbee.datasource.cli)
        ├── status                    # Coverage state
        ├── update [--type]           # Incremental fetch
        ├── init-universe             # Initialize stock pool
        ├── codegen                   # Re-run codegen
        └── verify                    # Run dict_sync + storage_io + state_tracker tests
```

`scripts/*.py` (`init_universe.py`, `fetch_data.py`, `run_*_backtest.py`) are legacy entry points; their functionality is fully covered by `newbee <cmd>`. They are **retained as documentation examples only**.

### 4.7 Compatibility Layer — `newbee/data/` (DEPRECATED)

The legacy data module (`newbee/data/universe.py`, `storage.py`, `pit.py`, ...). Migration to `newbee.datasource.*` is complete; imports trigger `DeprecationWarning`, full removal at M3. See [`docs/release-notes-data-refactor.md`](release-notes-data-refactor.md) for the symbol-by-symbol mapping.

## 5. Data Flow (Typical Scenarios)

### 5.1 End-to-End Portfolio Backtest

```
config.yaml
  ↓ load_config (utils.config)
strategy_id = md5(name|version|...)
  ↓
StockPool.load() ────────────────────┐
                                     ↓
DataFile(KData).read(start, end)     │
  ↓                                  │   alpha_store read
alpha matrix (T, N)                  │   + on-the-fly fallback
  ↓                                  │
engines.backtest_alpha               │
  ↓ IC / decile                      │
  (Phase A output)                   │
                                     ↓
engines.backtest_portfolio
  ↓
optimizers.mean_variance(mu, cov, w0, constraints)
  ↓
PortfolioState update (positions + cash + history)
  ↓
NAV curve + turnover + cost
  ↓
data/portfolio/results/<sid>_nav.parquet
```

### 5.2 Daily Incremental Update

```
newbee data status         # Inspect Data_State.json
newbee data update         # Pull all daily types by default
  ├── KDataService.daily_update
  │     ├── resume_range → (start, end)
  │     ├── fetch_kdata(start, end, source)
  │     ├── DataFile.append(df)
  │     └── StateTracker.update(stats)
  ├── TradeStatusService.daily_update
  ├── StockBasicDataService.daily_update
  └── StateTracker atomic persistence (fcntl + tempfile + os.replace)
```

## 6. Configuration Conventions

### 6.1 Config File Triad

| Category | Path | Example |
|---|---|---|
| Factor | `configs/factors/<name>.yaml` | `momentum_20.yaml` |
| Strategy | `configs/strategies/<name>.yaml` | `momentum_baseline.yaml` |
| Data | `configs/data/<name>.yaml` (M3+) | — |

**Field naming**:

- Factor config top level: `factor.{name, version, type}`, `compute.{window, field, ...}`.
- Strategy config top level: `name, version`, then `factor.*, data.*, portfolio.*, cost.*, cache.*`.
- `strategy_id = md5(name|version|factor.name|portfolio.optimizer)` — derived, not hand-written.

### 6.2 Config Loading

```python
from newbee.utils.config import load_config, strategy_id, resolve_data_range

cfg = load_config("configs/strategies/momentum_baseline.yaml")
sid = strategy_id(cfg)            # → key for alpha_store
start_s, end_s = resolve_data_range(cfg)  # → (start, end)
```

## 7. Test Conventions

### 7.1 Test Directory `tests/`

| Test file | Scope | Key scenarios |
|---|---|---|
| `test_dict_sync.py` | Data layer | YAML ↔ Pydantic ↔ Markdown tri-directional consistency |
| `test_storage_io.py` | Data layer | `DataFile` read/append/upsert/stats + predicate pushdown |
| `test_state_tracker.py` | Data layer | `StateTracker` atomic write + `resume_range` |
| `test_kdata_service.py` | Data layer | `KDataService` end-to-end |
| `test_trade_status_service.py` | Data layer | `TradeStatusService` end-to-end |
| `test_universe_service.py` | Data layer | `UniverseService` end-to-end |
| `test_pit.py` | Legacy data layer (DEPRECATED) | PIT unit + integration |
| `test_no_lookahead.py` | Factor layer | Killer test: corrupt `t+1..t+20` randomly, assert t-time factor output is **bit-identical** |
| `test_optimizer.py` | Portfolio layer | Constraint projection + mean-variance + edge cases |
| `test_append_fetch.py` | Data layer | Append-only incremental + fetch continuity |
| `test_fetch_state.py` | Data layer (legacy) | `fetch_state.json` compatibility (DEPRECATED) |
| `test_infer_dates.py` | Data layer | Infer incremental interval |
| `test_dry_run.py` | Data layer | Dry-run mode does not write to disk |
| `test_latest_trading_day.py` | Data layer | `latest_trading_day` derivation |
| `test_logger.py` | Utility layer | Logger proxy + format + `propagate` |
| `test_new_stock_alignment.py` | Data layer | New-stock alignment to IPO date |
| **Total** | — | 169 / 169 passing (see release notes) |

### 7.2 Test Commands

```bash
# Full suite
pytest tests/ -q

# Data layer core triad
pytest tests/test_dict_sync.py tests/test_storage_io.py tests/test_state_tracker.py -q

# Look-ahead killer test
pytest tests/test_no_lookahead.py -q
```

### 7.3 Test Hard Constraints

- `test_no_lookahead.py` **must** pass. One failure = a look-ahead bug; never let it through (uses strict `np.array_equal`, not approximation).
- Business code must not call `print(...)`; otherwise tests cannot capture logs.
- Data layer tests isolate via `tmp_path`; do not pollute `data/`.

## 8. Design Principles (Cross-Layer)

| Principle | Concrete form |
|---|---|
| **Type-driven** | Data fields → YAML single source of truth → codegen → Pydantic; never hand-edit Pydantic. |
| **Immutability first** | `DataType` / `FactorSpec` / `Trade` are all frozen dataclasses. |
| **Append-only** | KData / Universe rows are insert-only; universe drift adds new rows, never deletes. |
| **Atomic write** | All disk writes go through `tempfile + os.replace`; concurrency is gated by `fcntl` lock. |
| **Schema guard** | `DataFile.read()` forces `state.schema_version == dtype.schema_version` on startup; mismatch raises immediately. |
| **Separation of concerns** | KData owns prices; Trade_Status owns trading state; Stock_Basic_Data owns adjustment; Universe owns the pool. |
| **Strict PIT** | Financial data keyed by `(stock_id, end_date, ann_date, field)`; visible only when `ann_date <= asof`. |
| **Look-ahead protection** | Factor computation may only use data available at `≤ asof`; verified by `test_no_lookahead`. |
| **Logger via proxy** | Business code uses `from newbee.utils import logger`; never `logging.getLogger`. |
| **Paths via registry** | No hard-coded `Path("data/...")`; use `REGISTRY.get(<Type>).storage_path`. |

## 9. Naming Conventions

| Location | Style | Example |
|---|---|---|
| `data/` filenames | Pascal_Snake_Case | `KData.parquet`, `Trade_Status.parquet` |
| `configs/data_dict/*.yaml` | Pascal_Snake_Case | `KData.yaml`, `Stock_Basic_Data.yaml` |
| `docs/data_dict/*.md` | Pascal_Snake_Case | `KData.md` |
| `data/_Manifest/` | Underscore prefix (hidden) | `Data_State.json` |
| Python modules / packages | snake_case (PEP 8) | `newbee/datasource/storage/io.py` |
| Classes | PascalCase | `DataFile`, `KDataService` |
| Functions / variables | snake_case | `read_window`, `stock_code` |
| Constants | UPPER_SNAKE_CASE | `DEFAULT_DATA_ROOT`, `ALPHA_DIR_NAME` |
| Private members | single underscore prefix | `_assert_schema_fresh` |
| 9-char stock code | `{6 digits}.{SH|SZ}` | `600000.SH`, `000012.SZ` |
| 10-char date | ISO `YYYY-MM-DD` | `2026-06-24` |

## 10. Data Layer (Detailed)

> This section is the comprehensive specification of the data layer. It absorbs the entirety of the former `docs/data-architecture.md`.

### 10.1 Directory & File Layout

#### `data/`

```
data/
├── KData.parquet              # Daily K-line (no suffix = daily; post-adjusted close_adj)
├── KData_M1.parquet           # 1-minute K-line
├── KData_M5.parquet           # 5-minute K-line (reserved)
├── Trade_Status.parquet       # Trading status (suspended / ST / activate)
├── Stock_Basic_Data.parquet         # Adjustment factor
├── Universe.parquet           # Stock pool
├── Trading_Date.csv           # Trading-day calendar (CSV; reference data)
├── PIT.parquet                # Financial disclosures
│
├── Features/                  # npy matrix, kept as-is
│   └── <FactorName>/{YYYY-MM-DD}.npy
├── Alpha/                     # npy matrix, kept as-is
│   └── <StrategyId>/{YYYY-MM-DD}.npy
│
├── _Manifest/                 # Hidden directory (underscore prefix reserved)
│   ├── Data_State.json        # Per-type coverage state
│   └── Data_Dict_Index.json   # Dictionary index
│
├── models/                    # ⛔ Outside the data module scope, stays lowercase
└── repr/                      # ⛔ Outside the data module scope, stays lowercase
```

#### `configs/data_dict/`

```
configs/data_dict/
├── KData.yaml
├── KData_M1.yaml
├── KData_M5.yaml
├── Trade_Status.yaml
├── Stock_Basic_Data.yaml
├── Universe.yaml
├── Trading_Date.yaml
├── PIT.yaml
└── Features_Alpha.yaml
```

#### `docs/data_dict/`

```
docs/data_dict/
├── KData.md
├── KData_M1.md
├── KData_M5.md
├── Trade_Status.md
├── Stock_Basic_Data.md
├── Universe.md
├── Trading_Date.md
├── PIT.md
└── Features_Alpha.md
```

#### `newbee/datasource/`

> The package is named `datasource` (singular), distinct from the physical `data/` directory.

```
newbee/datasource/
├── schemas/                   # codegen output, Pydantic BaseModel
│   ├── kdata.py
│   ├── kdata_m1.py
│   ├── kdata_m5.py
│   ├── trade_status.py
│   ├── stock_basic_data.py
│   ├── universe.py
│   ├── trading_date.py
│   ├── pit.py
│   └── features_alpha.py
├── codegen.py                 # YAML → Pydantic → Markdown
├── registry.py                # DataRegistry
├── storage/
│   ├── io.py                  # DataFile (read / upsert / append / stats; parquet + CSV)
│   └── state.py               # StateTracker
├── sources/akshare.py         # Source adapter
├── incremental.py             # Incremental update orchestrator
├── calendar.py                # Trading-day calendar (legacy, retained)
└── ...
```

### 10.2 Frequency Suffix Convention

| Filename | Frequency |
|---|---|
| `KData.parquet` | Daily (default, no suffix) |
| `KData_M1.parquet` | 1 minute |
| `KData_M5.parquet` | 5 minutes |
| `KData_M15.parquet` | 15 minutes (future) |
| `KData_H1.parquet` | 60 minutes (future) |

The `frequency` field in YAML disambiguates explicitly.

### 10.3 Stock Code Format

`stock_code` is uniformly a **9-character string**: `{6 digits}.{SH|SZ}`.

| Segment | Meaning | Example |
|---|---|---|
| First 6 chars | Securities code (zero-padded) | `600000`, `000012`, `300750` |
| Chars 7–9 | Exchange suffix (`.SH` / `.SZ`) | `.SH` (Shanghai), `.SZ` (Shenzhen) |

Decision rule: codes starting with `6` or `9` → `.SH`; codes starting with `0` or `3` → `.SZ`. Beijing Stock Exchange (`.BJ`) may be added in the future.

### 10.4 Field Dictionary Triad

Every data type's field definition is expressed by three files working in concert:

```
configs/data_dict/KData.yaml          ← source of truth (human / AI edit entry)
        │ codegen
        ▼
newbee/datasource/schemas/kdata.py    ← Pydantic BaseModel (runtime strong validation)
        │ reflect
        ▼
docs/data_dict/KData.md               ← human-readable dictionary (auto-generated)
```

**Change flow**:

1. Edit `configs/data_dict/<Type>.yaml`.
2. Run `python -m newbee.datasource.codegen` to regenerate Pydantic and the dictionary Markdown.
3. Run `pytest tests/test_dict_sync.py -q` — the bidirectional consistency check must pass.
4. Commit YAML + generated Pydantic + dictionary Markdown as a triad.

### 10.5 Core Type Field Contracts

#### 10.5.1 KData (Daily)

| Field | Type | Unit | Nullable | Meaning |
|---|---|---|---|---|
| `trading_date` | string | 10 chars | N | Actual trading date, ISO `YYYY-MM-DD`, e.g. `2026-01-06` |
| `stock_code` | string | 9 chars | N | A-share code, e.g. `600000.SH`, `000012.SZ` |
| `open` | float32 | CNY | Y | Open price (unadjusted) |
| `high` | float32 | CNY | Y | High price (unadjusted) |
| `low` | float32 | CNY | Y | Low price (unadjusted) |
| `close` | float32 | CNY | Y | Close price (unadjusted) |
| `amount` | float32 | CNY | Y | Turnover (CNY) |
| `volume` | float32 | shares | Y | Trading volume (shares) |
| `close_adj` | float32 | CNY | Y | Post-adjusted close price |

Primary key: `(trading_date, stock_code)`.

**Adjustment convention**: `close_adj = close * adj_factor` (provided directly by the source data; no separate pre-adjusted price is used). All OHLCV is stored in **unadjusted** form. To derive the pre-adjusted close, use `close_adj / adj_factor`.

#### 10.5.2 Trade_Status

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `trading_date` | string | N | 10-char ISO date, e.g. `2026-01-06` |
| `stock_code` | string | N | 9-char code, e.g. `600000.SH` |
| `is_suspended` | bool | N | Whether the stock is suspended on the date |
| `is_st` | bool | N | Whether the stock is ST / *ST on the date |
| `is_activate` | bool | N | Whether the stock is active (listed and not delisted) on the date |

Primary key: `(trading_date, stock_code)`.

#### 10.5.3 Stock_Basic_Data

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `trading_date` | string | N | 10-char ISO date |
| `stock_code` | string | N | 9-char code |
| `adj_factor` | float64 | N | Adjustment factor |

Convention: `close_adj = close * adj_factor` (documented in the YAML dictionary to avoid ambiguity). `adj_factor` is preserved as **float64** to prevent precision loss over long horizons.

Primary key: `(trading_date, stock_code)`.

#### 10.5.4 Universe

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `stock_index` | int32 | N | Monotonically increasing index, append-only, never recycled |
| `stock_code` | string | N | 9-char code, e.g. `600000.SH` |
| `ipo_date` | string | N | 10-char ISO date, e.g. `2026-01-06` |

`active_mask(asof)` derivation: a stock is considered "listed at `asof`" iff `asof >= ipo_date`. Delisted stocks remain in `Universe`; rows are append-only, never deleted.

#### 10.5.5 Trading_Date

| Field | Type | Nullable | Meaning |
|---|---|---|---|
| `trading_date` | string | N | 10-char ISO date, e.g. `2024-01-02` |

Primary key: `("trading_date",)`. `frequency = "static"` (the calendar is a reference dataset, not a per-bar observation). The on-disk file is `data/Trading_Date.csv` (CSV format — see §10.5.6). No `Service` or CLI subcommand is shipped for `Trading_Date`; the CSV is expected to be populated by ad-hoc scripts or a follow-up change.

#### 10.5.6 Format Convention

Every data type's YAML MAY declare a `format` field. The value is one of:

| `format` | On-disk file | I/O backend | Use case |
|---|---|---|---|
| `parquet` (default) | `data/<Type>.parquet` | `pyarrow.parquet` (predicate pushdown, metadata scan) | All observation-shaped data (OHLCV, status, PIT, ...) |
| `csv` | `data/<Type>.csv` | `pandas.read_csv` / `DataFrame.to_csv` (in-memory filtering) | Small reference data (calendars, lookup tables — typically ≤ 1 MB) |

The `DataFile` class branches on `dtype.format`: parquet types use `pyarrow` exclusively; CSV types use `pandas` exclusively. The two backends are not mixed within a single method. CSV-backed types are intentionally small enough to load fully on every read; the system does not enforce a size limit at runtime (the `format: csv` declaration is the contract that the file is small by construction). See `openspec/specs/data-file-io/spec.md` for the full behavioural contract.

### 10.6 State Tracking — `data/_Manifest/Data_State.json`

Replaces the legacy `fetch_state.json`, per-type granularity, carrying `schema_version`:

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
    "Stock_Basic_Data":   { "...": "..." },
    "Universe":     { "...": "..." },
    "PIT":          { "...": "..." }
  }
}
```

**Hard constraint**: `DataFile.read()` enforces `state.types[<type>].schema_version == schemas/<type>.schema_version` on startup. Any mismatch raises `SchemaVersionError` immediately, preventing silent reads of incompatible data.

### 10.7 Core API

#### 10.7.1 `DataType` and `DataRegistry` (`registry.py`)

```python
@dataclass(frozen=True)
class DataType:
    """Immutable metadata for a single data type. Frozen guarantees no post-registration mutation."""

    name: str                          # "KData" / "Stock_Basic_Data" / "Trading_Date"
    schema_version: str                # "1.0"
    frequency: str                     # "daily" | "1min" | "5min" | "static" | ...
    format: str                        # "parquet" (default) | "csv"
    storage_path: Path                 # data/KData.parquet / data/Trading_Date.csv
    primary_key: tuple[str, ...]       # ("trading_date", "stock_code") or ("trading_date",)
    pydantic_model: type[BaseModel]    # KData / Stock_Basic_Data / TradingDate / ...

    def __post_init__(self) -> None:
        """Validate name uniqueness, Pascal_Snake_Case compliance, and `format` ∈ {"parquet", "csv"}."""
        ...

class DataRegistry:
    """Process-wide singleton. One-shot registration at startup, read-only at runtime."""

    def register(self, dtype: DataType) -> None: ...
    def get(self, name: str) -> DataType: ...
    def all(self) -> list[DataType]: ...
    def by_frequency(self, frequency: str) -> list[DataType]: ...

# Module-level singleton
REGISTRY = DataRegistry()
REGISTRY.register(KDATA)
REGISTRY.register(KDATA_M1)
REGISTRY.register(TRADE_STATUS)
REGISTRY.register(STOCK_BASIC_DATA)
REGISTRY.register(UNIVERSE)
REGISTRY.register(TRADING_DATE)  # format="csv"
```

**Usage**:

```python
from newbee.datasource.registry import REGISTRY

dtype = REGISTRY.get("KData")
print(dtype.primary_key)   # ('trading_date', 'stock_code')
print(dtype.storage_path)  # PosixPath('data/KData.parquet')

for d in REGISTRY.by_frequency("daily"):
    print(d.name, d.schema_version)
```

#### 10.7.2 `DataFile` (`storage/io.py`)

> Single-file physical I/O wrapper. Writes are gated by Pydantic strong validation; reads by `schema_version` strong validation.

```python
@dataclass(frozen=True)
class CoverageStats:
    """Physical statistics of one data file."""

    type_name: str
    schema_version: str
    frequency: str
    first_date: str | None          # ISO "YYYY-MM-DD" or None
    last_date: str | None           # ISO "YYYY-MM-DD" or None
    row_count: int
    stock_count: int                # distinct stock_code
    file_size_bytes: int
    file_sha256: str                # first 16 chars
    updated_at: str                 # ISO datetime

class DataFile:
    """Physical-file wrapper for one data type."""

    def __init__(self, dtype: DataType, *, root: Path | None = None) -> None: ...
```

**Read**:

```python
def read(
    self,
    *,
    start: str | None = None,
    end: str | None = None,
    stock_codes: list[str] | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Read data filtered by predicates.

    Args:
        start: ISO `YYYY-MM-DD`, inclusive lower bound; None = no lower bound.
        end: ISO `YYYY-MM-DD`, inclusive upper bound; None = no upper bound.
        stock_codes: stock-code whitelist; None = all.
        columns: column whitelist; None = all.

    Returns:
        DataFrame sorted by (trading_date, stock_code) ascending.
        Column order matches dtype.pydantic_model field order.

    Raises:
        FileNotFoundError: parquet file is missing.
        SchemaVersionError: disk schema_version != dtype.schema_version.
        SchemaValidationError: disk data fails Pydantic validation.
        ValueError: start > end or invalid date format.

    Implementation:
        - pyarrow.dataset predicate pushdown (start/end/stock_codes → filter).
        - Column pruning (pyarrow reads only requested columns).
        - Pydantic validation on output, to detect manually corrupted disk data.
    """
```

**Other methods**:

| Method | Behavior | Raises |
|---|---|---|
| `exists() -> bool` | File exists on disk. | — |
| `stats() -> CoverageStats` | Scan file to compute all statistics. | — |
| `append(df) -> int` | Append new rows (assumes no PK conflict). Returns written row count. | `SchemaValidationError`, `PrimaryKeyConflictError`, `ValueError` |
| `upsert(df, *, conflict: Literal["replace", "ignore", "error"] = "replace") -> int` | Upsert by primary key. Returns affected row count. | `PrimaryKeyConflictError`, `SchemaValidationError` |
| `truncate() -> None` | Drop the parquet file. Caller is responsible for confirming. | — |

**Implementation details**:

- `append` / `upsert`: Pydantic validates every row first, then `tempfile + os.replace` for atomic write. The `fcntl` file lock prevents concurrent writes.
- `stats`: zero-IO via `pyarrow.parquet` metadata; first/last date and stock count are computed with single-column reads when the file is non-empty.
- `read`: predicate pushdown happens at the pyarrow filter level, not in pandas.
- **Format branch**: when `dtype.format == "csv"`, `read` calls `pd.read_csv(self.path, usecols=columns)` and applies `start`/`end`/`stock_codes` filters in pandas (no pushdown is possible or necessary for CSV because CSV-backed types are small by construction); `append` / `upsert` use `df.to_csv(self.path, index=False)` via tempfile + os.replace; `stats` loads the file once via `pd.read_csv` and computes `first_date` / `last_date` / `row_count` / `stock_count` from the resulting DataFrame; `truncate` is format-agnostic. The parquet path stays byte-identical for non-CSV types. See §10.5.6 for when to declare `format: csv`.

**Usage**:

```python
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile

dtype = REGISTRY.get("KData")
file_ = DataFile(dtype)

# 1. Read
df = file_.read(
    start="2024-01-01", end="2024-12-31",
    stock_codes=["600000.SH", "000012.SZ"],
    columns=["trading_date", "stock_code", "close", "close_adj"],
)

# 2. Incremental append (after each daily fetch)
n = file_.append(new_rows)

# 3. Upsert (repair historical data)
n = file_.upsert(corrected_df, conflict="replace")

# 4. Stats
stats = file_.stats()
print(f"rows={stats.row_count}, range={stats.first_date}~{stats.last_date}")
```

#### 10.7.3 `StateTracker` (`storage/state.py`)

> Atomic read/write wrapper for `Data_State.json`. Domain-agnostic; pure KV persistence.

```python
@dataclass(frozen=True)
class DataTypeState:
    """Single-type state entry inside Data_State.json."""

    type_name: str
    schema_version: str
    frequency: str
    first_date: str | None
    last_date: str | None
    row_count: int
    stock_count: int
    updated_at: str

class StateTracker:
    """Global read/write for Data_State.json.

    Concurrency:
        - Read: lock-free (atomic read of the post-rename file).
        - Write: fcntl file lock + tempfile + os.replace.
    """

    def __init__(self, state_path: Path = Path("data/_Manifest/Data_State.json")) -> None: ...

    def read(self) -> dict[str, DataTypeState]:
        """Read all type states. Empty dict if file is missing."""

    def update(
        self,
        type_name: str,
        stats: CoverageStats,
        *,
        universe_sha: str | None = None,
    ) -> DataTypeState:
        """Atomically update a single type's state (other types untouched).

        Raises:
            SchemaVersionError: stats.schema_version != recorded schema_version
                (prevents silent overwrite of incompatible state).
        """

    def resume_range(
        self,
        type_name: str,
        *,
        latest: str,
    ) -> tuple[str, str]:
        """Infer the (start, end) interval to fetch for a type.

        Inference:
            1. If state.types[type_name].last_date exists → start = last_date + 1.
            2. Else fall back to DataFile.stats().
            3. Else fall back to the default start (2020-01-01 or Universe.ipo_date).
        Empty interval is represented as start > end.
        """

    def is_universe_stale(self, current_universe_sha: str) -> bool:
        """Whether the Universe needs to be rebuilt (top-level universe_sha mismatch)."""
```

**Usage**:

```python
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile
from newbee.datasource.storage.state import StateTracker

dtype = REGISTRY.get("KData")
file_ = DataFile(dtype)
tracker = StateTracker()

# 1. Infer incremental interval
latest = "2026-06-23"
start, end = tracker.resume_range("KData", latest=latest)
if start > end:
    print("KData already up-to-date")
else:
    print(f"Interval to fetch: {start} ~ {end}")

# 2. Fetch + write
new_df = fetch_kdata(start, end)
file_.append(new_df)

# 3. Compute stats + persist state
stats = file_.stats()
tracker.update("KData", stats)
```

#### 10.7.4 Service Facade (`service/<type>.py`)

> When business logic needs "read + state validation + auto-update" combined, use a `Service` to simplify. Simple scenarios can call `DataFile` + `StateTracker` directly.

```python
class KDataService:
    """High-level service for the KData type."""

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
        """Read a window with automatic schema_version validation."""

    def daily_update(
        self,
        *,
        today: str | None = None,
        source: str = "sina",
    ) -> UpdateResult:
        """End-of-day incremental update.

        Steps:
            1. latest_trading_day(today) → pin `latest`.
            2. tracker.resume_range → derive `start`.
            3. fetch_kdata(start, latest, source) → fetch.
            4. file_.append(new_df) → write.
            5. tracker.update → persist state.
            6. Return UpdateResult(success_count, failed_list, elapsed_sec).
        """

    def full_init(self, start: str = "2020-01-01") -> None:
        """First-time initialization (scorched-earth scenario)."""

    def _assert_schema_fresh(self) -> None:
        """Pre-read assertion: disk schema_version == dtype.schema_version."""
```

#### 10.7.5 Error Hierarchy (`storage/errors.py`)

```
DataSourceError
├── StorageError
│   ├── SchemaVersionError           # schema_version mismatch
│   ├── SchemaValidationError        # Pydantic validation failure
│   ├── PrimaryKeyConflictError      # append() hits existing PK
│   └── ManifestMismatchError        # universe_sha mismatch
└── StateCorruptedError              # Data_State.json corruption or schema drift
```

#### 10.7.6 Concurrency & Consistency

| Scenario | Behavior |
|---|---|
| Multi-process `append` on the same file | `fcntl.flock` mutex; later callers wait. Write goes through `tempfile + rename`, so the file is never half-written. |
| Multi-process `update` on `Data_State.json` | Same: `fcntl` lock + `tempfile + rename`. |
| Concurrent `read` + `write` | Reads are lock-free. Since `rename` is atomic, readers see either the old or the new version, never a partial mix. |
| `truncate` + `read` concurrency | `truncate` is not locked; the caller is responsible. Doc explicitly states "stop all reads before truncating". |
| Cross-machine concurrency | **Not supported**. The data module is single-machine by design; cross-machine requires distributed storage (future work). |

#### 10.7.7 Typical Workflows

**Scenario A — backtest read**:

```python
from newbee.datasource.service.kdata import KDataService

service = KDataService()
df = service.read_window(
    start="2020-01-01", end="2024-12-31",
    stock_codes=["600000.SH", "000012.SZ"],
    # columns optional, omitted = all
)
# df is Pydantic + schema_version validated
```

**Scenario B — daily incremental update**:

```python
from newbee.datasource.service.kdata import KDataService

service = KDataService()
result = service.daily_update(today="2026-06-23", source="sina")
print(f"success={result.success}, failed={len(result.failed)}, elapsed={result.elapsed_sec}s")
```

**Scenario C — first-time init (scorched-earth)**:

```python
from newbee.datasource.service.kdata import KDataService

service = KDataService()
service.full_init(start="2020-01-01")  # full re-fetch + rebuild
```

**Scenario D — PIT data upsert (post-revision)**:

```python
from newbee.datasource.registry import REGISTRY
from newbee.datasource.storage.io import DataFile

dtype = REGISTRY.get("PIT")
file_ = DataFile(dtype)
n = file_.upsert(corrected_pit_df, conflict="replace")
print(f"upserted {n} rows into PIT")
```

### 10.8 End-to-End Change Flow

```
  Human / Claude Code edits YAML
        │
        ▼
  git diff YAML (reviewable)
        │
        ▼
  PostToolUse hook triggers
   ├── python -m newbee.datasource.codegen
   │     ├── generate Pydantic BaseModel
   │     └── generate dictionary Markdown
   └── pytest tests/test_dict_sync.py -q
        │
        ▼
  Pydantic strong validation on write (types / ranges / nullability)
        │
        ▼
  CI: reverse validation + schema_version consistency
```

### 10.9 Deprecation & Rollback

- Legacy per-stock directories (`data/raw/`, `data/adj/`) are retained for one iteration cycle; after the new code stabilizes, a human operator deletes them manually.
- The legacy `data/_manifest/fetch_state.json` is no longer written; new code writes only `Data_State.json`.
- The legacy `data/universe/pool.parquet` is deleted by a human operator once the new `Universe.parquet` is fully generated.
- Legacy subdirectories `data/pit/`, `data/features/`, `data/alpha/` follow the same rule.
- The legacy Python package `newbee/data/` stops being imported after `newbee/datasource/` ships; one version later a human operator runs `rm -rf`.

## 11. AI Collaboration SOP (Claude Code)

> The data-layer-specific SOP lives in `.claude/rules/data-sop.md`. This section is the platform-level companion, covering everything outside the data layer.

### 11.1 Read Before Modifying

| Change type | Must read |
|---|---|
| Modify a field | `configs/data_dict/<Type>.yaml` + this doc §10.4–10.5 + `.claude/rules/data-sop.md` |
| Add a new factor | `newbee/factors/base.py` (Factor Protocol) + `newbee/factors/registry.py` |
| Add a new optimizer | `newbee/portfolio/optimizers/` (interface) + `newbee/portfolio/state.py` (conventions) |
| Add a new strategy | `configs/strategies/<name>.yaml` + `docs/01_first_factor.py` (reference) |
| Modify the data layer | This doc §10 in full + `.claude/rules/data-sop.md` |
| Modify the CLI | `newbee/cli.py` + `newbee/datasource/cli.py` (data subcommands live there) |

### 11.2 Hard Constraints During Modification

| Forbidden | Reason |
|---|---|
| Editing `newbee/datasource/schemas/*.py` to add a field | Drifts from YAML; `test_dict_sync.py` fails. |
| Calling `print(...)` in business code | Tests cannot capture; the logger proxy is bypassed. |
| `import logging; logger = logging.getLogger(__name__)` | Causes duplicate output. |
| Hard-coding `Path("data/raw")` / `Path("data/adj")` | Legacy paths, deprecated. |
| Writing new files under `data/raw/` / `data/adj/` | Deprecated; use `data/<Type>.parquet` long format instead. |
| `factor.compute()` using data with timestamp `> asof` | Look-ahead; `test_no_lookahead` will fail. |
| Skipping `StateTracker.update()` to edit `Data_State.json` directly | Breaks atomicity + breaks the `schema_version` guard. |
| Bare `except:` | Must specify a concrete exception type. |

### 11.3 Run After Modifying

| Change type | Must run |
|---|---|
| Any data-layer field / registration / storage / state change | `pytest tests/test_dict_sync.py tests/test_storage_io.py tests/test_state_tracker.py -q` |
| Any factor change | `pytest tests/test_no_lookahead.py -q` |
| Any portfolio / optimizer change | `pytest tests/test_optimizer.py -q` |
| Any logger change | `pytest tests/test_logger.py -q` |
| Large change (>5 files or cross-layer interface) | `pytest tests/ -q` (full) + `python -m newbee.datasource.cli verify` |
| PostToolUse hook (auto) | Editing `configs/data_dict/*.yaml` or `schemas/*.py` auto-runs `codegen` + `dict_sync` |

### 11.4 Commit Message Style

`≤ 2 lines`, each line `≤ 50` Chinese characters or `≤ 80` English characters. Format:

```
<type>(<scope>): <subject>

<body> (optional)
```

`type` ∈ {`feat`, `fix`, `chore`, `refactor`, `test`, `docs`}. `scope` is typically the module name (`data` / `datasource` / `factor` / `portfolio` / `cli`).

## 12. Risks & Limitations

| Risk | Mitigation | Status |
|---|---|---|
| Single-file append performance (full rewrite) | `pyarrow.ParquetWriter` writes row groups incrementally; can switch to dataset partitioning later. | Monitoring |
| `schema_version` omitted during change | `DataFile.read()` enforces the check on startup; mismatch raises `SchemaVersionError`. | ✅ landed in M2 |
| Legacy per-stock data remnants (`data/raw/`, `data/adj/`) | Legacy directories retained for one iteration; manually `rm -rf` after stabilization. | ⏳ cleanup window |
| Positions are weights, not share counts (unusable for live trading) | M2+ broker adapter will introduce share + cash model. | ⏳ M2+ |
| Optimizer supports long-only | Shorting is on the M2+ roadmap. | ⏳ M2+ |
| No T+1 / price-limit handling | Required for paper / live trading; M2+. | ⏳ M2+ |
| Factor is classic momentum; no ML | On the M3+ roadmap. | ⏳ M3+ |
| Cross-machine concurrency **not supported** | Data module is single-machine by design; cross-machine needs distributed storage (future work). | Documented |
| `close_adj` requires source to provide both `close` and `adj_factor` | `Stock_Basic_Data` is derived as `close_adj / close`; missing source data means missing rows. | Known limit |
| `PITStore` not yet migrated to the new data layer | Will be addressed when financial data is redone in M3. | ⏳ M3+ |

## 13. Roadmap

### 13.1 M2+ — Live Trading

- Broker adapter (QMT / third-party providers / in-house).
- Order management (order state machine).
- Risk monitoring (pre-trade check + post-trade monitor).
- Introduce share + cash model (replacing the weight-only positions model).

### 13.2 M3+ — Factor Factory

- ML factors (LGB / NN).
- NLP factors (announcements / research reports).
- Alternative data ingestion.
- `PIT.parquet` schema + PIT adapter implementation.
- `Features_Alpha` dictionary completion.

### 13.3 M4+ — Multi-Strategy Portfolio

- Capital allocation (Black-Litterman / Risk Parity).
- Meta-strategy (strategy selection + weighting).
- Real-time dashboard.

### 13.4 Data Layer Evolution (No Milestone Binding)

- Parquet dataset + partitioning (if single-file performance is insufficient).
- DuckDB replacing pyarrow on the read side (if SQL interface demand grows).
- Beijing Stock Exchange (`.BJ`) extension.
- Financial statement / announcement standardization (`PIT` YAML + adapter).

---

**Maintainer**: Newbee Architecture Group
**Change protocol**: small edits direct; large structural changes go through OpenSpec (`opsx:propose`). Edit reviewer must verify the data-layer section (§10) stays consistent with `configs/data_dict/*.yaml` and the generated `schemas/*.py`.
