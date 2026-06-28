# Features_Alpha

> 因子 / Alpha 矩阵 (npy cache, 非 parquet). 字段定义不适用, 仅 metadata.

- **schema_version**: `1.0`
- **frequency**: `daily`
- **storage**: `datas/_npy/`
- **primary_key**: ``

## npy cache metadata

此类型用 npy 矩阵缓存, 不走 parquet 字段表. 元信息如下:

```yaml
layout: matrix
shape:
- N
dir_template: datas/{category}/{name}/{YYYY-MM-DD}.npy
manifest: datas/{category}/{name}/manifest.json
manifest_fields:
- universe_sha
- dates
- count
- version
- last_asof
- last_updated
fields: []
```
