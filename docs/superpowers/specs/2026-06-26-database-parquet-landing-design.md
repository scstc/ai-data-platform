# 数据库直连数据以 Parquet 落地 — 设计

- 日期:2026-06-26
- 状态:待评审
- 范围:数据接入 → 数据集落地的存储格式(仅数据库直连)

## 背景与动机

平台现状:任何来源接入都被 `landing.py` 归一成 **jsonl**(schema-on-write)。
对**数据库直连**采集而言,这一步把库的列类型抹平——`int`/`Decimal`/`date`
在 jsonl 里都变成字符串/弱类型文本,丢掉了:

- 列类型保真(数字列做聚合/统计需要真类型);
- 列存优势(大表扫描/SQL 分析);
- 与 data-juicer 的天然契合(dj 能直接读 parquet,跑完也能输出 parquet)。

经核对:dj 的导出格式只支持 `jsonl/json/parquet`(`core/exporter.py:460`),
**不输出 CSV**。因此"保结构"唯一可行的目标格式是 **Parquet**——dj 能读能写,
列存、类型不丢。

## 目标

数据库直连(PG 族 + GoldenDB)采集的数据集,以 **Parquet** 落地与流转:
落地写 parquet、预览按真类型展示、物化直接喂 dj、dj 加工产物仍为 parquet。

## 非目标(本期不动)

- 本地上传(txt/csv/xlsx/doc/pdf)→ 维持 jsonl/原样,**不改**。
- 媒体 manifest、API 推送 → **不改**。
- CSV/Excel 上传转 parquet → 留待后续按需(类型不可靠,收益打折)。
- Lance/Iceberg 等湖格式 → 明确不做(与本平台"归一+治理"路线不符)。

## 关键设计决策

### D1. 目标格式:Parquet,版本级标记

- 数据库落地版本:`format="parquet"`,
  `storage_uri=s3://<uploads>/<dataset_id>/v<n>/data.parquet`。
- **无需数据库迁移**:`DatasetVersion.format` 本就是 free string。
- 数据集可跨版本混格式(v1 parquet 接入、v2 parquet 加工产物);每个版本
  自带 `format`,读路径按 `format`/`storage_uri` 分发,模型已支持。

### D2. 兜底:尝试 parquet,失败回退 jsonl(零回归红线)

pyarrow 从 `list[dict]` 推断 schema 可能失败的情况:空记录、嵌套 JSONB
(dict/list)、同列异构类型。**任一失败 → 捕获 → 落 jsonl 并记 `format="jsonl"`**,
采集照常成功。绝不因为换 parquet 让某种库数据采集失败(对齐 Rule 12 诚实降级)。

### D3. dj 加工 parquet 数据集 → 产物也输出 parquet

dj `Exporter` 原生支持 parquet(export 路径后缀 `.parquet`)。parquet 输入的
加工任务,产物版本 `format="parquet"`,保住"结构不丢"的初衷。

## 受影响的组件与改动

### 1. 写:`backend/app/services/landing.py`

- 新增 `records_to_parquet_bytes(records) -> bytes`:`pyarrow.Table.from_pylist`
  推断 schema → 写 parquet 字节。推断失败抛内部异常(由调用方兜底回退)。
- `land_records(...)`:新增参数 `storage_format: str = "jsonl"`。
  - `"parquet"`:调 `records_to_parquet_bytes` + `upload_parquet_to_uploads`;
    失败则回退 `records_to_jsonl_bytes` + `upload_jsonl_to_uploads`,版本 `format`
    随实际落地结果记。
  - `"jsonl"`(默认):现行为不变(上传路径零回归)。

### 2. 写:`backend/app/services/external_store.py`

- 新增 `upload_parquet_to_uploads(dataset_id, version_no, parquet_bytes) -> str`:
  key=`<dataset_id>/v<n>/data.parquet`,content_type `application/octet-stream`。

### 3. 连接器:`pg.py` / `mysql.py`

- `run_pg_ingest` / 对应 MySQL(goldendb)的 `land_records(...)` 调用传
  `storage_format="parquet"`。
- `generate-dataset` 端点(`api/v1/ingest_tasks.py`):`records_to_jsonl_bytes` +
  `upload_jsonl_to_uploads` 替换为 parquet(同样带回退);响应 `fileKey` 改
  `data.parquet`。

### 4. 读/物化:`external_store.py::materialized_version`

- parquet 版本(`format="parquet"`):下载 .parquet 到本地临时文件,**直接
  yield 该路径**喂 dj(`ParquetFormatter` 原生读),不再 normalize 成 jsonl。
- 其余分支(本地 jsonl 透传、hosted 文本归一、manifest、二进制拒绝)不变。

### 5. 预览:数据集预览端点(`api/v1/datasets.py` 中支撑 `VersionFilePreview`)

- parquet 版本:用 **DuckDB** `SELECT * ... LIMIT N` 读 parquet(本地或经
  httpfs 读 s3),返回 `{columns, data}`,按真类型展示。
- jsonl 版本:现行预览逻辑不变。
- 下载:parquet 走现有预签名直链(浏览器直接下 .parquet)。

### 6. dj 加工产物落地:`engine.py` + `external_store.py::upload_file_to_uploads`

- 输入版本为 parquet 的加工任务:dj 导出路径用 `.parquet`,产物经
  `upload_file_to_uploads` 的 parquet 变体落 `data.parquet`,产物版本
  `format="parquet"`。
- 输入为 jsonl 的任务:维持 jsonl 产物,不变。

## 数据流(改造后,数据库直连)

```
PG/GoldenDB ──fetch──> records(带真类型)
  └─ records_to_parquet_bytes ──成功──> data.parquet (format=parquet)
                            └──失败──> data.jsonl   (format=jsonl, 兜底)
预览:  DuckDB 读 parquet → {columns, data}(真类型)
物化:  下载 .parquet → 直接喂 dj ParquetFormatter
加工:  dj 跑算子 → 导出 .parquet → 产物版本 format=parquet
```

## 测试策略

- 单元:`records_to_parquet_bytes` 类型保真(int/decimal/date 往返不变);
  兜底回退(空记录 / 嵌套 JSONB / 异构列 → 落 jsonl)。
- 集成:PG 采集端到端 → 版本 `format=parquet`、预览列类型正确、物化喂 dj 跑通。
- 回归:本地上传(jsonl)、媒体 manifest、API 推送路径**不受影响**(显式断言
  这些版本仍 `format != "parquet"`)。
- dj 产物:parquet 数据集加工后产物版本 `format=parquet` 且可再预览/物化。

## 风险与权衡

- **风险**:散落的 `format == "jsonl"` 假设可能有遗漏点。缓解:D2 兜底保证最坏
  情况退回 jsonl;回归测试覆盖三类非 DB 路径。
- **权衡**:parquet 不可像 jsonl 那样逐行追加/人读;但数据库数据本就批量整表
  落地、不需要行级追加,代价可接受。
- **依赖**:pyarrow 24.0.0 / duckdb 1.5.4 已在后端 venv,无需新增依赖。
