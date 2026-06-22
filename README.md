# Newbee · 量化交易平台 (M1)

> 一个自建、可验证、最小可用的量化交易研究/回测平台. 目标: **不靠闭源库, 端到端可读, 数据可复现**.

## 当前进度 (M1)

- [x] M1a · 骨架 + 数据层 (StockPool, 行情, PIT)
- [x] M1b · 第一个因子 (momentum_20) + Alpha 回测
- [x] M1c · 组合回测 (mean_variance, 成本, 换手率约束)
- [x] M1d · 闭环 + 防护 (集成测试, 文档)

40 个 tasks 中 32 个已 mark, 8 个检查点 (需真实数据验证).

## 快速开始

### 1. 安装

```bash
# Python 3.11+
pip install -e .

# 或手动装核心依赖
pip install numpy pandas pyarrow scipy exchange_calendars akshare pyyaml matplotlib
```

### 2. 初始化股票池 (一次性)

```bash
python scripts/init_universe.py
# 拉中证 1000 成分股 (csi1000) 入库到 data/universe/pool.parquet
```

### 3. 拉取行情 (一次性, ~30-60 分钟)

```bash
python scripts/fetch_data.py --universe csi1000 --start 2020-01-01 --end 2024-12-31
# 数据落地: data/adj/{stock_id}.parquet (前复权)
#        + data/raw/{stock_id}.parquet (不复权)
```

### 4. 跑第一个因子 + Alpha 回测

```bash
# 计算 alpha (momentum_20) + 写 alpha_store
python docs/01_first_factor.py

# Alpha 回测 (IC, decile 收益)
python scripts/run_alpha_backtest.py --config configs/factors/momentum_20.yaml
```

### 5. 跑组合回测

```bash
python scripts/run_portfolio_backtest.py --config configs/strategies/momentum_baseline.yaml
# 输出: data/portfolio/results/momentum_baseline_1.0_nav.parquet
```

### 6. 跑测试

```bash
pytest tests/ -v
# 39 个测试, 包含 PIT / look-ahead 杀手锏 / 优化器
```

## 架构

```
newbee/
├── data/                  # 数据层
│   ├── universe.py        # StockPool (append-only, sha256 校验)
│   ├── storage.py         # 双格式 (parquet + npy 矩阵)
│   ├── calendar.py        # 交易日历 (exchange_calendars 包装)
│   ├── sources/akshare.py # 多源适配器 (sina/em/tx)
│   └── pit.py             # Point-in-Time 财务数据
├── factors/               # 因子层
│   ├── base.py            # Factor Protocol + SimpleFactor
│   ├── registry.py        # 内存注册表
│   ├── classic/momentum.py
│   └── pipeline.py        # 矩阵化计算
├── alpha_store.py         # Alpha cache (回测/实盘同源)
├── engines/               # 回测引擎
│   ├── backtest_alpha.py  # Alpha 回测 (IC, decile)
│   └── backtest_portfolio.py  # 组合回测 (状态机)
├── portfolio/             # 组合层
│   ├── state.py           # PortfolioState
│   ├── optimizers/        # mean_variance, equal_weight, inverse_vol
│   ├── constraints.py     # LongOnly, WeightSum, MaxTurnover, MaxWeight
│   └── cost.py            # CostModel (双参数化)

configs/
├── factors/
│   └── momentum_20.yaml
└── strategies/
    └── momentum_baseline.yaml

scripts/
├── init_pool.py
├── fetch_data.py
├── run_alpha_backtest.py
└── run_portfolio_backtest.py

docs/
├── 01_first_factor.py
└── 02_alpha_to_portfolio.py

tests/
├── test_pit.py             # PIT 单元 + 集成
├── test_no_lookahead.py    # look-ahead 杀手锏
└── test_optimizer.py       # 优化器
```

## 关键设计

### 1. 数据层 (Pandas 矩阵化)
- 时间轴 × 股票池 → `(T, N)` ndarray
- parquet (per-stock) + npy (per-day) 双格式
- `universe_sha` 校验 (universe 改了, 缓存自动失效)

### 2. PIT (Point-in-Time)
- 财务数据: `(stock_id, end_date, ann_date, field)` 4-key
- 严格 ann_date <= asof 才可见
- 支持 re-statement (同 end_date, 不同 ann_date 都保留)

### 3. Alpha Store
- 统一 alpha 读写, 回测 / 实盘同源
- npy at `data/alpha/{strategy_id}/{date}.npy`
- `universe_sha` 校验防 universe 漂移

### 4. 两阶段回测
- **Phase A (alpha 回测)**: 速度优先, 单因子横截面 IC + decile
- **Phase B (组合回测)**: 保真度优先, 状态机 + 成本 + 调仓约束

### 5. Look-ahead 防护 (杀手锏)
- `tests/test_no_lookahead.py`: 合成 30-100 天数据, 随机搞坏 t+1..t+20
- 验证 t 时刻因子输出**完全不变** (严格 array_equal, 不是近似)
- 一次失败 = look-ahead bug, 不能放过

### 6. 日志约定
- 使用统一入口: `from newbee.utils import logger`, 然后直接 `logger.info(...)` / `logger.warning(...)`
- proxy 自动从调用栈取 `__name__` 作为 logger 名, 无需 `logger = get_logger(__name__)` 赋值行
- 内部走 `logging` 标准库, proxy 给每个被首次访问的 logger 挂 `StreamHandler` + 统一 formatter, 并设 `propagate=False` 避免与 `logging.basicConfig` 重复输出
- 默认格式: `%(asctime)s | %(levelname)-7s | %(name)s | %(message)s`; 可用 `LOG_FORMAT` 环境变量覆盖
- **不要**在业务代码里 `print(...)` 或 `import logging; logger = logging.getLogger(__name__)`, 全走 proxy

## 配置示例

`configs/strategies/momentum_baseline.yaml`:
```yaml
name: momentum_baseline
version: "1.0"

factor:
  name: momentum_20
  window: 20
  field: adj_close

data:
  start: "2020-01-01"
  end: "2024-12-31"

portfolio:
  optimizer: mean_variance
  rebalance_freq: 20
  max_turnover: 0.3
  max_weight: 0.05

cost:
  commission_rate: 0.0005
  slippage_rate: 0.001
```

## 已知限制 (M1)

- 持仓用 weight (0-1), 不建模股数 (M2+ 扩展)
- 优化器只支持 long-only (做空需要 M2)
- 没有 T+1 / 涨跌停处理 (M2)
- 没有融资融券 (M2)
- 因子是经典动量, 没用机器学习 (M3+)

## 下一步 (M2+)

- M2: 实盘对接 (broker adapter, 订单管理, 风险监控)
- M3: 因子工厂 (ML, NLP, 另类数据)
- M4: 多策略组合 (capital allocation, meta-strategy)

---

**维护者**: 详见 CLAUDE.md (CC 配置) + MEMORY.md (CC 记忆)

**License**: Private
