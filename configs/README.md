# configs/

Configuration artifacts for the Newbee platform. Each subdirectory holds one
class of configuration, consumed at different points in the pipeline.

| Subdir | Purpose | Producer / Consumer |
|---|---|---|
| `data_dict/` | **Data type field contracts** (YAML source of truth for all datas types). Consumed by `python -m alpha_backend.datasource.codegen`, which generates Pydantic models into `alpha_backend/datasource/schemas/` and markdown into `docs/data_dict/`. **Do not edit the generated artifacts directly**; see `.claude/rules/datas-sop.md`. | `alpha_backend/datasource/codegen.py` |
| `datas/` | (Reserved for runtime datas-layer configuration, e.g. fetch windows, universe index selections — not yet populated.) | TBD |
| `factors/` | Factor definitions consumed by the backtest / portfolio layer. | `alpha_backend/factors/` |
| `models/` | Trained model artifacts. | `alpha_backend/models/` |
| `strategies/` | Portfolio strategy definitions. | `alpha_backend/strategies/` |

**Note on naming**: the schema dictionaries under `configs/data_dict/` describe
the *shape* of the parquet files under `datas/`. They are not the parquet files
themselves and they are not the generated Pydantic models — they are the
human- and AI-edited source of truth that the codegen reads.
