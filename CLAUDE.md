# 环境要求
- 每次运行我的代码或执行 Python 相关命令前，必须先激活 conda 环境：
  conda activate py312

# 数据模块
- 数据模块架构、字段字典、命名规范、变更 SOP 详见 [docs/platform-design.md §10](docs/platform-design.md#10-data-layer-detailed)
- 字段字典 source of truth 在 `data_dict/*.yaml`,禁止直接改 `newbee/datasource/schemas/*.py`
- 改字段时需改 YAML 后跑 `python -m newbee.datasource.codegen` + `pytest tests/test_dict_sync.py -q`
