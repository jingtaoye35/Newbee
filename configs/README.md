# configs/

Configuration artifacts for the Newbee platform. Each subdirectory holds one
class of configuration, consumed at different points in the pipeline.

| Subdir | Purpose | Producer / Consumer |
|---|---|---|
| `data_dict/` | **Data type field contracts** (YAML source of truth for all data types). Consumed by `python -m newbee.datasource.codegen`, which generates Pydantic models into `newbee/datasource/schemas/` and markdown into `docs/data_dict/`. **Do not edit the generated artifacts directly**; see `.claude/rules/data-sop.md`. | `newbee/datasource/codegen.py` |
| `data/` | (Reserved for runtime data-layer configuration, e.g. fetch windows, universe index selections — not yet populated.) | TBD |
| `factors/` | Factor definitions consumed by the backtest / portfolio layer. | `newbee/factors/` |
| `models/` | Trained model artifacts. | `newbee/models/` |
| `strategies/` | Portfolio strategy definitions. | `newbee/strategies/` |

**Note on naming**: the schema dictionaries under `configs/data_dict/` describe
the *shape* of the parquet files under `data/`. They are not the parquet files
themselves and they are not the generated Pydantic models — they are the
human- and AI-edited source of truth that the codegen reads.
