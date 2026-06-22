# Newbee · 量化交易平台
> 一个自建、可验证、最小可用的量化交易研究/回测平台. 目标: **不靠闭源库, 端到端可读, 数据可复现**.

主要划分几个目录
- src 代码库。src/datasource 用于获取外部数据；其中 schema/ 目录维护存储数据的元信息 (Pydantic BaseModel + ClassVar: type_name / schema_version / frequency / storage_path / primary_key / format), 完全单源, 不再依赖 yaml / configs/
- scripts bash 运维脚本
- docs 用于维护设计文档等内容

## src/datasource 内部职责划分

`src/datasource/` 下各文件 / 目录的分工:

| 路径 | 职责 |
| --- | --- |
| `dataset.py` | 对外(例如 backend)直接提供数据访问服务,或数据工作函数 |
| `cli.py` | 更新本地数据文件 |
| `schema/*.py` | 定义本地数据文件 (A 模式的数据契约) |
| `adapter/*.py` | 从外部数据源获取数据 |
| `service/*.py` | 从 `adapter/*.py` 读取数据,转换成本地格式,落盘; 同时在 `datasource` 内部封装"读本地数据"接口,供 `dataset.py` 调用或对外暴露 |
| `storage/*.py` | 封装 io |

数据流:
- **写入**(外 → 本地):`cli.py` → `adapter/*.py` → `service/*.py` → `storage/*.py`
- **读取**(本地 → 外):`dataset.py` → `service/*.py` → `storage/*.py`

## 配置架构(三套并存)

项目里**三套配置模式并存**,各自负责一片,**不要互相越界**:

| 模式 | 位置 | 负责什么 |
| --- | --- | --- |
| **A · 数据契约** | `src/datasource/schema/*.py` | 数据集元信息 (`type_name` / `storage_path` / `primary_key` / `frequency` / `format`),通过 `ClassVar` 挂在每个 `BaseModel` 上 |
| **B · 全局运行时** | `configs/global.yaml` + `src/config.py` | 项目元信息、运行时开关 (并发上限、日志级别)、跨业务模块共享的路径、外部 endpoint |
| **C · 业务参数** | 各业务模块自带的 frozen dataclass (例:`backend/strategy/watchlist/config.py`) | 单业务独占的策略门槛、打分权重、过滤规则等 |

**新增字段时**,按"现状共用数"判定归类:
- 跨 ≥ 2 个业务模块共用 → **B** (`configs/global.yaml` + `src/config.py` 加字段)
- 仅当前业务模块用 → **C** (加到该模块的 dataclass)

**B 模式的访问协议(强制)**:
```python
from config import get_config
cfg = get_config()
n_workers = cfg.runtime.max_core
path = Path(cfg.paths.datasource_dir) / dt.storage_path
```
其它写法(`import yaml` 自己读 / 直接读 `_config` / DI 注入 / env 通道)都不允许。

**B 模式的优先级链(决定路径怎么解析)**:
1. CLI 参数(如 `cli --universe`)
2. YAML 字段(`cfg.paths.universe`)
3. schema 默认值(`REGISTRY.get(<Name>).storage_path` + 拼 `cfg.paths.datasource_dir`)
4. 三层都缺 → 启动报错

**YAML 路径策略**:`paths.datasource_dir` 是绝对路径,加载时**不做 `~` 展开或 `resolve()`**,调用方自己用 `Path(...) / dt.storage_path` 拼装。