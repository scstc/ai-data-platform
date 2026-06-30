# 数据集优先流程改造 — 设计

- **日期**:2026-06-30
- **分支**:`feature/governance-remediation-plan`
- **目标库**:`adp_gov`(治理整改克隆库;新迁移接在 0031–0033 之后,**不动 dev adp**)
- **状态**:已批准,待写实现计划

## 1. 背景与动机

平台当前是 **landing-first / 数据集即副产品** 的模型:不存在「先建空数据集」这一步,数据集总是作为上传/采集动作的副作用,与首版本(`version_no=1`)在同一事务里被铸造出来。中央落地函数 `land_records`(`backend/app/services/landing.py:544`)无条件执行 `Dataset(id=_new_dataset_id())` + `DatasetVersion(version_no=1)`,没有「追加到已有数据集」的分支。

后果:

- 一个**多表采集任务会扇出成 N 个独立数据集**(每张表调用一次 `land_records`),与「一个数据集承载多张表」的预期相反。
- 重跑同一任务每次都铸造新的 `dset-…`(都是 v1),不是追加。
- `ingest_tasks.dataset_id` 列虽在(迁移 0015),但**休眠**,从不被写。

本设计将流程倒置为 **数据集优先**:用户先创建数据集,上传与采集再选择已有数据集落入;多表数据集在一个版本内承载多个 parquet 成员。

## 2. 锁定的决策(已与用户确认)

| 维度 | 决策 |
|---|---|
| 多表结构 | **一个版本内含多个表成员**(一组 parquet,每文件一张表);「新建 parquet」= 给数据集加一个表成员 |
| 流程倒置力度 | **完全倒置**,废除「上传/采集自动建数据集」 |
| 选集约束适用入口 | **全部四个**:本地上传、采集任务、托管 S3/平台对象、API 推送 |
| 再次执行语义 | **原版本内覆盖/新增表**(不涨版本)——经下述 draft/published 生命周期调和 |
| 目标库 | **adp_gov** 整改克隆库,迁移 0034+ |

## 3. 核心语义:版本生命周期 draft → published

「原版本内更新表」与现有的**版本不可变**不变量(`dataset_versions` 只有 `created_at` 无 `updated_at`;血缘与 G18 复现性 `dj_version`/`image_tag`/`executor_type` 都依赖「一版本 = 一固定快照」)直接冲突。调和方案:

- 数据集**最新版本处于 `draft`** 时:上传/采集执行就在这个 draft 版本里**新增或覆盖表成员**——即用户要的「数据集像一个活的表集合」。
- **发布**(复用已有 `publish_status`)即冻结该版本,成员集合定格,成为不可变快照。
- 发布之后,下一次针对该数据集的上传/采集**自动开新 draft 版本**(v+1,默认克隆上一版成员做续接)。

净效果:常态(在 draft 上攒表)= 「原版本内覆盖/新增表」;只在「发布」边界落不可变快照,保住血缘/复现/发布语义。

## 4. 数据模型(adp_gov,迁移 0034+)

沿用本仓库**无 DB 级 FK / 弱关联**约定(所有跨表链接为普通 String 列)。

### 4.1 新增子表 `dataset_version_tables`(仿 `job_inputs`)

一行 = 一张表 = 一个 parquet 成员:

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | String PK | `"dvt-" + 6hex` |
| `dataset_version_id` | String NOT NULL | 弱引用 `dataset_versions.id`,无 FK |
| `table_name` | String NOT NULL | 表名/成员名,版本内唯一(走唯一约束 `uq_dvt_version_table`) |
| `storage_uri` | String NOT NULL | 单个成员文件位置 `s3://<bucket>/<dataset_id>/v<n>/<table>.parquet` |
| `format` | String NOT NULL default `parquet` | 成员级格式 `jsonl\|csv\|parquet` |
| `rows` | BigInteger | 该表行数 |
| `size` | BigInteger | 该表字节数 |
| `schema_snapshot` | JSONB | 该表 `[{name,type}]` |
| `schema_variant` | String nullable | 成员级覆盖(默认继承版本级) |
| `created_at` | timestamptz | |

约束:`UniqueConstraint(dataset_version_id, table_name)` + `Index(ix_dvt_version, dataset_version_id)`,与现有 `uq_dataset_version_no`、`ix_job_inputs_version` 风格一致。

### 4.2 `dataset_versions` 字段降级为 rollup

原本单值的 `storage_uri / format / rows / size / schema_snapshot` 转为**跨成员汇总/可空 rollup**(`rows`/`size` = 各成员之和;`storage_uri` 指向版本前缀或首成员;`format` 在混合时为 `multi`)。单表数据集 = 恰好一个成员,旧字段语义对单成员仍成立,读路径向后兼容。

### 4.3 元数据归属

- `train_type` 留**版本级**(一快照一训练用途,符合 0031 设计)。
- `schema_variant`:版本级作默认,**成员级可覆盖**(表形态按表而定)。
- `semantic_type` / `data_type` 留**数据集级**,作模板/默认。
- 不把 `train_type`/`schema_variant` 上移到 `datasets`(会破坏 0031 的版本级不变量)。

### 4.4 其他表

- `ingest_tasks.dataset_id`:从休眠转**权威**,迁移改为 NOT NULL(adp_gov 克隆库,回填后安全)。仅约束**拉取型采集任务**;API 推送不经 `ingest_tasks`,其选集约束落在 `datasource.config.boundDatasetId`(见 §7),不受此 NOT NULL 影响。
- `upload_records`:增可空 `dataset_id`,仅作可追溯。

### 4.5 向后兼容回填

迁移给每个现存 `dataset_versions` 回填一行 `dataset_version_tables`(`table_name` 取默认如 `data`,`storage_uri/format/rows/size/schema_snapshot` 复制自版本现值),使所有读路径统一走成员模型。adp_gov 为克隆库,回填可重跑、可回退。

## 5. 存储布局

```
s3://<bucket>/<dataset_id>/v<n>/<table>.parquet   # 每个表成员一个 key
```

沿用现有 `<dataset_id>/v<n>/` 前缀,`files.py` 的软保护(`_count_hosted_refs`、storage_uri LIKE)与前缀 GC 逻辑对「一版本多 key」天然兼容(前缀不变)。

## 6. 后端服务层

### 6.1 拆分 `land_records`(landing.py:544)

拆成两个职责单一的函数:

- `create_dataset(meta) -> Dataset`:只建空数据集行,不建任何版本。
- `add_table_member(dataset_id, *, records, table_name, fmt, train_type, schema_variant, produced_by_job_id, …) -> DatasetVersionTable`:定位数据集的**当前 draft 版本**(无则开 v1;若最新版已 published 则开 v+1 并克隆上一版成员),按 `table_name` 新增或覆盖成员,写 parquet,刷新版本 rollup。

复用已有的 `select(func.max(DatasetVersion.version_no))+1` 模式(push.py:180、engine.py:474 已有先例);并发安全依赖 `uq_dataset_version_no` 与 `uq_dvt_version_table`。`land_upload`/`land_upload_raw`/`land_eval_dataset` 改为经此路径。fail-loud 校验门(eval ≥300 校验、construct 全坏、parquet→jsonl 回退、扫描版 PDF ParseError)在**落成员时**触发,不在建空集时触发。

### 6.2 连接器收口

`pg/mysql/hdfs/objectstore/proprietary` 现状「每表 → 一数据集」,改为「所选多表 → 落进同一选定数据集的 draft 版本,每表一个成员」。`_build_queries`(base.py)已产出 `[(table, query)]`,仅替换落地那一步为 `add_table_member(task.dataset_id, table_name=suffix, …)`。host-s3/host-platform 多 key 从「每 key 一数据集」改为「多 key = 一数据集多成员」。

### 6.3 读路径多表化

`materialized_version`(external_store.py:501)、preview、DuckDB query、download、engine/construct、`_members_of`(datasets.py:881)需支持**表选择器**(缺省取第一个成员)。`_members_of` 现有枚举成员机制可复用扩展。`external_store.upload_jsonl/parquet_to_uploads`(698/716)增 `table_name` 参数,key 变 `<id>/v<n>/<table>.parquet`。

### 6.4 血缘

追加成员/版本路径仍写 `produced_by_job_id` + `JobInput`,保证 `export_delivery.collect_lineage` BFS 不断。

## 7. 后端 API(backend/app/api/v1)

| 端点 | 变更 |
|---|---|
| `POST /api/v1/datasets` | **新增**(今天不存在)。Body `DatasetCreate { name, categoryId, dataType, semanticType, trainType?, schemaVariant?, tags }`,建空数据集 |
| `POST /datasets/upload`、`/upload-batch`、`/upload-media` | 去掉建集逻辑;**新增必填 `datasetId`** form 字段;落进其 draft 版本作成员;加「数据集可写」ACL 校验 |
| `POST /ingest-tasks` | **必填 `datasetId`**,写 `IngestTask.dataset_id`,校验存在/可写 |
| `POST /datasets/host-s3`、`/host-platform` | 新增 `datasetId`;返回从 `Dataset[]` 变为「一数据集的成员集」 |
| API push (`/ingest/push/{token}`) | 沿用 `datasource.boundDatasetId` 绑定(已有追加逻辑),纳入统一约束 |
| 读 schema | `DatasetVersionRead`/`DatasetDetailRead`/`DatasetMemberRead` 补**成员数组**(table_name/rows/schema/format/storageUri) |

`list_datasets` 的 `latest_version_label`/`_showcase_version`/`_showcase_modalities` 聚合改为可处理多 schema 版本。

## 8. 前端(frontend/src)

- `api.ts`:新增 `createDataset(DatasetCreate)`;`typings.d.ts` 的 `Dataset.sourceFormat` 容纳多格式/多表。
- 数据集列表页(`pages/datasets/list`)加「新建数据集」ModalForm;详情页(`datasets/detail`)成为「建集后往里加表/上传」中枢。
- **所有上传表单从「填名字」改成「选数据集」**:`ingest/local-upload/single.tsx`、`ingest/access/UploadModal.tsx`、files 页「接入数据集」。FormData 键 `name/data_type/categoryId` → `datasetId`。
- 采集向导 `ingest/tasks/index.tsx` StepsForm 增「目标数据集」选择步;`IngestTaskCreate`/`IngestExtract` 加 `datasetId`;改掉「每张表各产一个数据集」「每个对象各产一个数据集」文案。
- 多表 UI 复用已建好的 `DatasetMember`(`listDatasetMembers` GET `/dataset-versions/{id}/members`、`addDatasetMembers`、`deleteDatasetMember`,原为媒体清单)扩到表成员;列表/详情显示成员数与各表 schema;`previewDatasetVersion`(已接受 key 参数)切换成员文件。
- 后端契约定后跑 `npm run openapi` 重生 `src/services/ant-design-pro/`(**不手改**)。

## 9. 错误处理与不变量

- 建空数据集**不触发** fail-loud 校验门;成员落地时才触发。
- 已 published 版本**只读**:任何往其追加/覆盖成员的尝试 → 自动开新 draft 版本(不报错,符合 §3)。
- 并发落同名表:靠 `uq_dvt_version_table` 兜底,后到者覆盖语义需在 `add_table_member` 内显式 upsert。
- ACL:落入数据集前校验调用者对该数据集可写(复用 `dataset_acl`)。

## 10. 测试(Rule 9:测意图而非行为)

复用 `tests/test_uploads.py`、`tests/test_landing_parquet.py` 的 fixture 模式:

- `create_dataset` → 多次 `add_table_member` → 断言**一版本多成员**、各成员 schema 独立、rollup(rows/size)正确。
- **draft 可变 / published 冻结后开新版本**:发布前覆盖同名表 = 原版本内变化;发布后再落 = v+1 且克隆上一版成员。
- 多表采集任务 → **一个数据集多成员**(回归原「扇出 N 数据集」缺陷)。
- 四入口选集约束:缺 `datasetId` → 422;选只读/无权数据集 → 403。
- 改完 landing/路由/权限跑 CLAUDE.md 冒烟集(`test_files / test_datasets_preview / test_uploads / test_dataset_acl / test_rbac`)。

## 11. 分期(单 spec,分阶段实现)

1. **数据模型 + 迁移 0034+**(新子表、rollup 降级、`ingest_tasks.dataset_id` NOT NULL、回填)
2. **landing 拆分 + 读路径多表化**(`create_dataset`/`add_table_member`、成员化读路径)
3. **API**(建集端点 + 各入口选集 + 读 schema 成员数组)
4. **连接器 / host 收口**(多表落同一数据集)
5. **前端**(建集、选集、多表 UI)+ openapi 重生

## 12. 暂不做(YAGNI)

- 跨成员联表查询
- 成员级独立发布/血缘
- 自动 schema 合并/演进
- dev adp 库的同步(本轮只面向 adp_gov)

