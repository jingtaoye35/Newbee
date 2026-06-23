# Adj_Factor

> 累积复权因子 (long format, float64 精度).

- **schema_version**: `1.0`
- **frequency**: `daily`
- **storage**: `data/Adj_Factor.parquet`
- **primary_key**: `trading_date, stock_code`

## Fields

| Field | Type | Nullable | Unit | Description |
|---|---|---|---|---|
| `trading_date` | `string` (str) |  | YYYY-MM-DD | 交易日. |
| `stock_code` | `string` (str) |  | 9-char .SH/.SZ | 9 字符股票代码. |
| `adj_factor` | `double` (float) | ✓ | ratio | 累积复权因子 (float64 精度, 防长 horizon 漂移). |

## Notes

- 此字典由 `python -m newbee.datasource.codegen` 自动生成, 不要手改.
- 字段定义变更请改 `data_dict/Adj_Factor.yaml` 后跑 codegen + pytest.
