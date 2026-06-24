# Trading_Date

> A 股交易日历 (single-column reference data, CSV-backed).

- **schema_version**: `1.0`
- **frequency**: `static`
- **storage**: `data/Trading_Date.csv`
- **primary_key**: `trading_date`

## Fields

| Field | Type | Nullable | Unit | Description |
|---|---|---|---|---|
| `trading_date` | `string` (str) |  | YYYY-MM-DD | 实际交易日 (ISO YYYY-MM-DD), 例如 2024-01-02. |

## Notes

- 此字典由 `python -m newbee.datasource.codegen` 自动生成, 不要手改.
- 字段定义变更请改 `configs/data_dict/Trading_Date.yaml` 后跑 codegen + pytest.
