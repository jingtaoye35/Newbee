# Stock_Basic_Data

> 股票基础数据 (累积复权因子 + 涨跌停价 + 申万行业, long format, float64 精度).

- **schema_version**: `1.0`
- **frequency**: `daily`
- **storage**: `data/Stock_Basic_Data.parquet`
- **primary_key**: `trading_date, stock_code`

## Fields

| Field | Type | Nullable | Unit | Description |
|---|---|---|---|---|
| `trading_date` | `string` (str) |  | YYYY-MM-DD | 交易日. |
| `stock_code` | `string` (str) |  | 9-char .SH/.SZ | 9 字符股票代码. |
| `adj_factor` | `double` (float) | ✓ | ratio | 累积复权因子 (float64 精度, 防长 horizon 漂移). |
| `limit_upper_price` | `float` (float) | ✓ | CNY | 涨停价 (nullable). |
| `limit_lower_price` | `float` (float) | ✓ | CNY | 跌停价 (nullable). |
| `sw_industry` | `string` (str) | ✓ | None | 申万一级行业. |

## Notes

- 此字典由 `python -m newbee.datasource.codegen` 自动生成, 不要手改.
- 字段定义变更请改 `data_dict/Stock_Basic_Data.yaml` 后跑 codegen + pytest.
