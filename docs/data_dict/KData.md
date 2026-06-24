# KData

> 日 K 线 (long format).

- **schema_version**: `1.0`
- **frequency**: `daily`
- **storage**: `data/KData.parquet`
- **primary_key**: `trading_date, stock_code`

## Fields

| Field | Type | Nullable | Unit | Description |
|---|---|---|---|---|
| `trading_date` | `string` (str) |  | YYYY-MM-DD | 交易日 (ISO string, 10 chars). |
| `stock_code` | `string` (str) |  | 9-char .SH/.SZ | 9 字符股票代码, 形如 "600000.SH" / "000012.SZ". |
| `open` | `float` (float) | ✓ | CNY | 开盘价 (nullable, 停牌/未上市为 NaN). |
| `high` | `float` (float) | ✓ | CNY | 最高价 (nullable). |
| `low` | `float` (float) | ✓ | CNY | 最低价 (nullable). |
| `close` | `float` (float) | ✓ | CNY | 收盘价 (nullable). |
| `amount` | `float` (float) | ✓ | CNY | 成交额 (nullable). |
| `volume` | `float` (float) | ✓ | shares | 成交量 (nullable). |
| `close_adj` | `float` (float) | ✓ | CNY | 后复权收盘价 = close * adj_factor (post-adjusted). |

## Notes

- 此字典由 `python -m newbee.datasource.codegen` 自动生成, 不要手改.
- 字段定义变更请改 `configs/data_dict/KData.yaml` 后跑 codegen + pytest.
