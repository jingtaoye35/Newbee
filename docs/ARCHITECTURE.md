# Architecture

## src/datasource 内部职责划分

`src/datasource/` 下各文件 / 目录的分工:

| 路径 | 职责 |
| --- | --- |
| `dataset.py` | 对外(例如 backend)直接提供数据访问服务,或数据工作函数 |
| `cli.py` | 更新本地数据文件 |
| `schema/*.py` | 定义本地数据文件 (A 模式的数据契约,见下文) |
| `adapter/*.py` | 从外部数据源获取数据 |
| `service/*.py` | 从 `adapter/*.py` 读取数据,转换成本地格式,落盘; 同时在 `datasource` 内部封装"读本地数据"接口,供 `dataset.py` 调用或对外暴露 |
| `storage/*.py` | 封装 io |

数据流:
- **写入**(外 → 本地):`cli.py` → `adapter/*.py` → `service/*.py` → `storage/*.py`
- **读取**(本地 → 外):`dataset.py` → `service/*.py` → `storage/*.py`

`schema/` 是 A 模式(数据契约),只描述数据本身的字段 / 主键 / 存储文件名,不写运行时路径或 IO。

## 三套配置边界与路径优先级链

### 三个模式的职责划分

| 模式 | 位置 | 负责什么 | 谁来读 |
| --- | --- | --- | --- |
| **A · 数据契约** | `src/datasource/schema/*.py` | 数据集元信息 (类型名 / 主键 / 频率 / 存储文件名) | `datasource.registry.REGISTRY.get(<name>)` |
| **B · 全局运行时** | `configs/global.yaml` + `src/config.py` | 项目元信息 / 运行时开关 / 跨业务路径 / 外部 endpoint | `from config import get_config` |
| **C · 业务参数** | 各业务模块 frozen dataclass | 单业务独占的策略门槛、打分权重、过滤规则 | 直接 import 该模块的 `DEFAULT_CONFIG` |

### 字段归类仲裁规则

```
 字段描述的是数据本身(字段名/类型/主键/存储文件名)        → A
 字段是项目运行时(路径/并发/日志/外部 endpoint),跨 ≥2 个业务用 → B
 字段是单业务独占的策略参数(门槛/权重/黑名单)             → C
```

**只看现状共用数**,不预设未来。"看着像基础设施经验值" 不构成升 B 的理由。

### 跨套访问协议(强制)

**唯一合法写法:**
```python
from config import get_config
cfg = get_config()
x = cfg.runtime.max_core
path = Path(cfg.paths.datasource_dir) / dt.storage_path
```

**禁止写法:**
- `import yaml` 在 C 模式代码里自己读
- 读 `config._config` 私有符号
- 函数级 DI (`def run(..., *, max_core: int)`)
- env 通道(B 把字段暴露成 env,C 读 `os.environ`)

### 路径优先级链(权威度从高到低)

| 优先级 | 来源 | 例子 |
| --- | --- | --- |
| 1 | CLI flag | `cli --universe=/custom/path` |
| 2 | YAML 字段 | `cfg.paths.universe` |
| 3 | schema 默认 | `REGISTRY.get("Universe").storage_path` 拼上 `cfg.paths.datasource_dir` |
| 4 | 三层都缺 | 启动期 `FileNotFoundError` / `RuntimeError` |

**调用方拼装示例:**
```python
# CLI 参数优先
explicit = args.universe
if explicit is None:
    # YAML 其次
    explicit = cfg.paths.universe
if not explicit:
    # schema 默认兜底
    dt = REGISTRY.get("Universe")
    explicit = str(Path(cfg.paths.datasource_dir) / dt.storage_path)
if not Path(explicit).exists():
    raise FileNotFoundError(explicit)
```

### YAML 路径值约定

`configs/global.yaml` 里的路径值是**字面绝对路径**,加载器:
- ❌ 不展开 `~`
- ❌ 不调用 `.resolve()`
- 不引入隐式魔法

调用方拿到的就是 YAML 里写的字符串,自己负责拼接。

### 多进程语义

| 启动方式 | `get_config()` 行为 |
| --- | --- |
| `fork` | 子进程继承父进程的 singleton,直接可用 |
| `spawn` | 子进程需要重新 `load_config()` |

`utils.parallel_run.py` 把当前 config 路径通过 `mp.Pool(initializer=...)` 传给 worker,worker 内调用 `load_config(path)` 自启。