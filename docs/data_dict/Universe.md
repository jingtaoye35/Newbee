# Universe

> 自建股票池 (append-only, stock_index 单调递增).

- **schema_version**: `1.0`
- **frequency**: `static`
- **storage**: `data/Universe.parquet`
- **primary_key**: `stock_index`

## Fields

| Field | Type | Nullable | Unit | Description |
|---|---|---|---|---|
| `stock_index` | `int32` (int) |  | int | 单调递增 idx, 一旦分配永不回收 (即使股票退市). |
| `stock_code` | `string` (str) |  | 9-char .SH/.SZ | 9 字符股票代码. |
| `ipo_date` | `string` (str) |  | YYYY-MM-DD | IPO 日期, 用于 active_mask(asof) 计算. |

## Notes

- 此字典由 `python -m newbee.datasource.codegen` 自动生成, 不要手改.
- 字段定义变更请改 `data_dict/Universe.yaml` 后跑 codegen + pytest.
