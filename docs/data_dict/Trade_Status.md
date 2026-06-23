# Trade_Status

> 交易状态 (停牌/ST/活跃), long format.

- **schema_version**: `1.0`
- **frequency**: `daily`
- **storage**: `data/Trade_Status.parquet`
- **primary_key**: `trading_date, stock_code`

## Fields

| Field | Type | Nullable | Unit | Description |
|---|---|---|---|---|
| `trading_date` | `string` (str) |  | YYYY-MM-DD | 交易日. |
| `stock_code` | `string` (str) |  | 9-char .SH/.SZ | 9 字符股票代码. |
| `is_suspended` | `bool` (bool) |  | bool | True 当日停牌. |
| `is_st` | `bool` (bool) |  | bool | True 当日被 ST 标记. |
| `is_activate` | `bool` (bool) |  | bool | True 当日正常交易 (非停牌非 ST). |

## Notes

- 此字典由 `python -m newbee.datasource.codegen` 自动生成, 不要手改.
- 字段定义变更请改 `data_dict/Trade_Status.yaml` 后跑 codegen + pytest.
