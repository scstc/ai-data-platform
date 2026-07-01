# 本地上传切数据湖 — 设计

- **日期**:2026-07-01
- **分支**:`feature/governance-remediation-plan`
- **状态**:待实现

## 1. 背景

现状:数据接入 → 本地上传的两个子页(`ingest/local-upload/single.tsx`
「单一数据接入」、`ingest/local-upload/ImportCard.tsx` 场景数据文件导入)
都是**直接落数据集**——用户选一个已有数据集,上传后原件存 `uploads` 桶、
按语义解析为 draft 版本的表成员或 manifest 成员。

治理文档(`docs/数据治理.md`)要求"湖集分离":所有外部数据源应先入数据湖归档
(ODS 层),再从湖抽取到数据集。数据源接入(PG、S3 等)已有 `ingest_pg_to_dataset_via_lake`
铺路,本地上传是最后一条没切过来的入湖路径。

需求:本地上传两个子页改为**只入湖**——用户选目标数据湖,上传的文件产生一批
`source_v` 快照;从湖抽到数据集由数据湖详情页现有的「抽取生成数据集」按钮
承担(已实现的 `extract_to_new_dataset`)。

**范围**:仅覆盖"上传路径切湖"。从湖抽取时的文档解析(PDF/DOCX → 文本块、
图片 → 描述、音视频 → 转写)属于第二层"数据抽取与前置解析"的工作,单独 PR
承接,不塞进本 PR。

## 2. 决策

### 2.1 一文件一快照

一批本地上传的 N 个文件 → **N 个快照**(而不是合并成 1 个)。理由:

- 每个文件的原始 filename / size 是血缘的一部分,合并成一个 parquet 会把它们塞进
  `source_metadata` 数组里,后续从湖抽取到数据集时无法按文件粒度回溯。
- 快照对应一个"原样归档单元"——不同文件的 schema / 格式可能不同(csv/jsonl/xlsx
  混传),强合并要么丢结构、要么强升类型。
- 抽取时用户可选择部分快照生成数据集(`extract_to_new_dataset` 的 `snapshot_ids`
  已支持多选),粒度天然对齐。

### 2.2 批次号自增

现有 `generate_source_version(batch=1)` 硬编码 batch=1,同湖同天同 `source_type`
第二次入湖直接撞 `uq_lake_source_version`。批量本地上传是天然的多快照场景,
必须修:

- 新增 `_next_batch_no(db, lake_id, date, source_type)`:查当天所有匹配
  `source_v{YYYYMMDD}_%_{source_type}` 的快照,取最大批次 + 1;无则从 1 开始。
- `ingest_to_lake_parquet` / `ingest_to_lake_raw` 内部调用 `_next_batch_no`
  生成 `source_version`,把 `generate_source_version(batch=)` 的默认值从
  1 改为"由调用方或自增算出"。
- `generate_source_version` 本身保持无副作用(纯字符串拼接);批次自增只在
  service 层入湖时做——纯函数用于测试与外部集成时的 spec 化命名。
- 竞态:两并发调用可能算到同一批次号→ 依赖 `uq_lake_source_version` 兜底,
  抛 `IntegrityError` 时重试一次(最多 3 次)后放弃。本地上传是用户交互场景,
  并发极低,重试足够。

### 2.3 结构化 vs 非结构化分派

按扩展名分派:

- **`LANDABLE_FORMATS` 且非二进制**(jsonl/json/csv/tsv/txt/md/xlsx/xls
  + doc/pdf/... + geojson):走 `ingest_to_lake_parquet` 前先用 landing
  的 `normalize_to_records` 把文件解析为 records。`data_category="tabular"`
  (与 PG 拉的 "database" 区分,便于抽取时统计口径)。
  - `xlsx/xls` / `docx/pdf/pptx` / `geojson` 也过 normalize_to_records,产出
    就是 records,统一走 parquet 归档——这与 landing 的 raw 落地路径一致。
  - `raw` 语义(不解析、原字节归档)本次**不做**——本地上传的语义就是"落湖以便
    后续抽成数据集",不解析等于永远抽不出来。
- **`BINARY_FORMATS`**(png/jpg/mp3/mp4 等媒体):走 `ingest_to_lake_raw`,
  按扩展名映射到 `data_category`(image/audio/video)。
- **其他扩展名**:400 拒绝,消息与现有 upload-batch 相同。

### 2.4 API 契约

`POST /api/v1/data-lakes/{lake_id}/local-upload`(需要写权;沿用湖侧现有的
`require_admin`——湖当前仅 admin 可写,与数据接入其他入湖点一致。)

- Form:`files: List[UploadFile]`(至少 1 个,最多 `MAX_MANIFEST_MEMBERS`)
- Response 200:
  ```json
  {
    "data": {
      "snapshots": [
        {
          "id": "snap-...",
          "sourceVersion": "source_v20260701_01_csv",
          "originalFilename": "a.csv",
          "dataCategory": "tabular",
          "storageFormat": "parquet",
          "rows": 1234,
          "size": 45678
        }
      ]
    },
    "success": true
  }
  ```
- 400:湖不存在、无文件、扩展名不支持、单文件超 200MB、批次总数超限
- 部分失败:任一文件失败即回滚整批,返回 400 + 失败原因(避免半个批次的
  快照留在湖里让用户困惑)。

### 2.5 前端

- `single.tsx`:标题不变("单一数据接入"),content 改为"批量上传文件:结构化文件
  解析成 parquet 入湖、媒体原格式入湖,后续到数据湖详情页抽取生成数据集"。
- 「目标数据集」下拉 → 「目标数据湖」下拉。API 换成 `listDataLakes`。
- 提交按钮"上传并生成数据集"→"上传并归档到数据湖"。
- 成功后跳 `/data-lakes/{lakeId}`(数据湖详情页),消息:「已归档 N 个快照,
  可到数据湖抽取生成数据集」。
- `VersionTargetSelect` 组件删除(不再有版本概念)。
- `ImportCard.tsx`:同样改造——目标改为数据湖。多模态卡片、场景卡片都指同一
  入湖接口,后端根据扩展名分派;不再传 `semantic_type`(语义在抽取时决定,
  不在入湖时决定)。
- **场景数据示例文件与说明保留**:虽然入湖不再需要 `semantic_type`,示例文件
  帮用户准备"cot 每行 JSON"、"gis GeoJSON"这样的**内容格式**,与湖侧无关。

### 2.6 不做的事

- 湖侧不做"版本选择"——一批文件产生一批新快照,没有"复用旧快照"的概念。
- `upload_batch_as_dataset` / `upload_media_as_dataset` 后端两个旧路由**保留**,
  供 `ingest/access/UploadModal.tsx`(数据接入)其它入口继续用。前端 `single.tsx`
  / `ImportCard.tsx` 内的调用点从两个旧接口切走。
- 抽取到数据集时的**文档解析**(markitdown/OCR/ASR)不在本 PR。本 PR 落地后,
  raw 快照(pdf/mp3/mp4 等)可以进湖但 `extract_to_new_dataset` 处理不了
  (`extract_from_lake_snapshot` 里明确 `raise` 非 parquet)。这是**已知限制**,
  写在快照详情页的提示语里,下一 PR 补。
- 前端"版本选择器"不做——那是数据集侧的概念,湖里没有版本重用。

## 3. 后端(`backend/`)

### 3.1 `app/services/data_lake.py`

- 新增 `async def _next_batch_no(db, lake_id, date, source_type) -> int`:
  用 `SELECT source_version FROM data_lake_snapshots WHERE lake_id = :lake_id
  AND source_version LIKE 'source_v{yyyymmdd}_%_{source_type}'`,正则出批次数,
  取 max + 1;无匹配 → 1。
- `ingest_to_lake_parquet` / `ingest_to_lake_raw` 内先调用 `_next_batch_no`
  拿批次,再拼 `source_version`。原 `generate_source_version(source_type=)`
  的调用点改成 `generate_source_version(batch=batch_no, source_type=)`。
- `ingest_to_lake_parquet` 新增 `data_category: str = "database"` 形参(默认
  值保兼容),本地上传路径传 `"tabular"`。
- 唯一约束冲突(极端并发)时:`try/except IntegrityError → session.rollback → 重试`,
  最多 3 次;超过则抛 `ExternalStoreError("批次号冲突,请重试")`。

### 3.2 `app/api/v1/data_lakes.py`

- 新增 `POST /data-lakes/{lake_id}/local-upload`:
  - 依赖 `require_admin`(与现有 `create_data_lake` / `extract_to_dataset` 对齐)
  - Form:`files: List[UploadFile]`
  - 检查湖存在(404)
  - 逐文件按扩展名分派:
    - 二进制媒体 → `ingest_to_lake_raw(data_category=image|audio|video, upload_channel="local")`
    - 可解析文本/文档 → `normalize_to_records(content, ext)` → `ingest_to_lake_parquet(source_type=ext, data_category="tabular", upload_channel="local", source_metadata={"original_filename": ...})`
    - 其他 → 400
  - 任一失败:整批之前已 commit 的快照通过 `_purge_lake` 反向删除(记快照 id
    列表,失败时循环 `session.delete + 物理删 MinIO`——最终交付时会补物理删),
    避免半个批次留在湖里。**MVP 简化**:失败即抛,已入库快照留着,前端提示
    "部分文件入湖失败,请到数据湖详情页手动清理"——比"复杂的补偿事务"更清晰。

### 3.3 单测(`tests/test_data_lake.py`)

- `test_next_batch_no_increments`:同湖同天同 source_type 三次入湖,批次 1/2/3
- `test_local_upload_lake_split_tabular_and_media`:一批混文件 → parquet 快照 +
  raw 快照分别落
- `test_local_upload_lake_not_found`:不存在的湖 → 404
- `test_local_upload_lake_empty_files`:空 files → 400
- `test_local_upload_lake_unsupported_ext`:.exe → 400
- 现有 `test_snapshot_unique_per_lake_version` 保留(校验 DB 层约束)

## 4. 前端(`frontend/src/`)

### 4.1 `services/data-platform/api.ts`

新增:

```ts
export async function localUploadToLake(
  lakeId: string,
  formData: FormData,
  options?: { [key: string]: any },
) {
  return request<{
    data: { snapshots: DataPlatform.DataLakeSnapshot[] };
    success: boolean;
  }>(`/api/v1/data-lakes/${lakeId}/local-upload`, {
    method: 'POST',
    data: formData,
    requestType: 'form',
    ...(options || {}),
  });
}
```

### 4.2 `pages/ingest/local-upload/single.tsx`

- `listDatasets` 调用 → `listDataLakes`,state `datasetId` → `lakeId`,标签
  "目标数据集"→"目标数据湖",placeholder 相应调整。
- `onSubmit` 内所有分支合成一个 FormData 打 `localUploadToLake(lakeId, fd)`;
  提示语改"归档到数据湖";成功跳 `/data-lakes/{lakeId}`。
- 删除 `VersionTargetSelect` 相关 import + state + 元素(不复存在)。
- 顺手把 content 文案改为准确的"归档到数据湖"描述。

### 4.3 `pages/ingest/local-upload/ImportCard.tsx`

- 同上:目标 select 换成数据湖;`onSubmit` 无论 media/非 media 都打
  `localUploadToLake`;不再传 `semantic_type` / `data_type`(后端按扩展名判)。
- 成功跳 `/data-lakes/{lakeId}`。
- 场景类型(cot/qa/preference/timeseries/gis/multimodal)在 UI 上继续用于
  **文案展示 + 示例下载 + 扩展名限制**;不再作为语义标签传后端。

## 5. 迁移风险

- 现有页面依赖 `datasetId` 的埋点(if any)会失效——检查:`data-testid="scenario-import-submit-*"`
  按语义类型区分的,保留;跳转路径变了,若有 e2e 依赖旧 URL 需同步。
- 后端旧接口保留 → 数据接入其它入口不受影响。
- 现有数据湖用户量小(feature 分支未上线);无存量数据迁移。

## 6. 验证清单

- [ ] `pytest tests/test_data_lake.py -q` 全绿
- [ ] `ruff check app/services/data_lake.py app/api/v1/data_lakes.py` 干净
- [ ] `npm run tsc` 通过
- [ ] `npx biome lint <changed files>` 通过
- [ ] 手工:选目标湖 → 传一批 csv+png → 湖详情页看到两类快照 → 抽取生成数据集
  (parquet 快照可抽,raw 快照按当前 lake_extract 会被拒;记录为"待第二层")
