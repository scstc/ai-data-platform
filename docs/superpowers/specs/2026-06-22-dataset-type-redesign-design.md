# 数据集「类型」重设计 —— 三轴拆分设计

> 日期：2026-06-22 · 状态：待评审
> 背景需求：`docs/requirements/数据工程&数据集.md`（数据接入 = 接入方式 / 数据类型 / 数据格式 三项独立维度）
> 关联：`docs/plan/14-数据接入重构设计.md`（`data_type` free-string 历史包袱）

## 1. 问题

数据集列表/详情里的「类型」列取自 `Dataset.data_type`，这是个**无受控词表的自由字符串**，被各接入通道塞了不同语义的值：

- 文件格式：`csv` / `jsonl` / `image`
- 接入通道标记：`sql` / `csv-tsv`
- 语义枚举：`text` / `qa` / `cot`（仅 S3 托管表单的下拉覆盖）

前端 `<Tag>{data_type}</Tag>` 原样渲染，且 `DATA_TYPE_ENUM` 把英文 key 当显示文案，导致「类型」列**中英混排、语义含混**，与旁边规范的中文「语义类型」列并排观感混乱。

## 2. 设计原则

需求文档已把「数据接入」拆成三个**正交维度**，本设计据此把混在一格的 `data_type` 拆成三轴：

| 轴 | 字段 | 落点 | 性质 |
|---|---|---|---|
| **来源 / 接入方式** | `source_kind`（新增，受控枚举） | `Dataset` | 接入时确定，不可变 |
| **数据类型** | `semantic_type`（复用现有受控枚举） | `Dataset` / `DatasetVersion` | 接入时确定，可被加工/融合改写 |
| **格式** | `source_format`（新增，标准化字符串） | `Dataset` | 接入时捕获的**原始**格式 |

`data_type` 从**展示层退役**：列表/详情/筛选不再使用；DB 列保留仅为向后兼容与回填来源，不再作为新写入的展示依据。

## 3. 来源枚举 `source_kind`

对齐需求「接入方式」，收敛为 5 类（中文标签由前端映射）：

| 值 | 标签 | 覆盖通道 |
|---|---|---|
| `database` | 数据库 | 达梦 / GoldenDB / kingbase / gaussdb / hologres / 巨杉 / hive / doris / mysql / pg（原 `data_type="sql"`） |
| `object_store` | 对象存储 | S3 / OSS / OBS（平台采集 / 零拷贝接入） |
| `hdfs` | HDFS | hdfs 连接器 |
| `local_upload` | 本地上传 | 单文件 / 场景上传（原 `data_type="csv-tsv"`） |
| `api_push` | API 推送 | push 连接器 |

说明：
- 「采集任务」不单列——它是*拉取机制*，可作用于数据库 / 对象存储等；真正来源仍归上述 5 类，任务血缘已由 `produced_by_job_id` 承载。
- **三方 S3 托管（`origin=hosted`）本期不处理**：hosted 数据集的 `source_kind` 暂留空（`null`），列表/详情对空值回退显示 `-`。后续单独处理。

## 4. 数据类型 `semantic_type`

保持现有 10 类受控枚举不动（`semantic_registry` + `frontend/src/utils/semanticType.tsx`）：
文本 / 结构化 / 非结构化 / 多模态 / COT / QA / 偏好 / 时序 / GIS / 融合。

本设计只把它在 UI 中**提升为主「数据类型」列**，渲染沿用现有 `SemanticTypeTag`。

## 5. 格式 `source_format`

接入时捕获原始格式，标准化为小写短串：

- 文本/表格/文档：`txt` / `pdf` / `ppt` / `doc` / `docx` / `xls` / `xlsx` / `csv` / `tsv` / `html` / `json` / `jsonl`
- 二进制媒体：`image` / `audio` / `video`
- 媒体集（多文件清单）：`manifest`
- 数据库直连（无文件载体）：留空 `null`（详情显示 `-`）

> 注意与 `DatasetVersion.format` 区分：后者是**归一后的存储格式**（基本恒为 `jsonl`，媒体集 `manifest`，二进制 `binary`），是平台内部产物属性，不展示给「格式」列。`source_format` 才是用户视角的原始格式。

## 6. 各通道赋值映射（实现依据）

| 通道 / 入口 | `source_kind` | `source_format` |
|---|---|---|
| 本地上传（`land_upload`，single/scenario/...） | `local_upload` | 上传文件原始扩展名（txt/csv/docx/jsonl/image…） |
| 采集——mysql / pg / proprietary（达梦等） | `database` | `null` |
| 采集——hdfs | `hdfs` | 拉取对象原始扩展名 |
| 采集——objectstore（S3/OSS/OBS） | `object_store` | 拉取对象原始扩展名 |
| 平台对象零拷贝接入 | `object_store` | 对象原始扩展名 |
| API 推送（push） | `api_push` | `jsonl`（推送即结构化记录） |
| S3 托管（hosted，本期不处理） | `null` | `null` |

落点统一在 `land_records`（统一落地出口）新增两个入参 `source_kind` / `source_format`，由各调用方传入；连接器内已知通道类型，直接赋常量。

## 7. 存量回填（best-effort，一次性）

Alembic 迁移内做尽力回填（无法判定的留空，不臆造）：

- `source_kind`：`data_type="sql"` → `database`；`data_type="csv-tsv"` → `local_upload`；`origin=hosted` 的版本对应数据集 → 留空（本期不处理）；其余按现有信号尽力归类，判不准留 `null`。
- `source_format`：从该数据集 v1 版本的 `storage_uri` 扩展名 / `version.format` 推断；推不出留 `null`。

回填不可逆部分仅为补充展示字段，不动数据内容与既有 `data_type`。

## 8. 改动面

**后端**
- `models/dataset.py`：`Dataset` 加 `source_kind: str|None`、`source_format: str|None` 两列（均 nullable）。
- `services/landing.py`：`land_records` / `land_upload` 增 `source_kind` / `source_format` 入参并写入。
- 各连接器（mysql/pg/proprietary/hdfs/objectstore/push/本地上传入口）：落地调用处传对应常量（见 §6）。
- `schemas/dataset.py`：读模型暴露两字段；写模型不强制（接入侧内部决定）。
- `api/v1/datasets.py`：列表查询支持 `source_kind` 过滤参数；`semantic_type` 过滤已存在。
- Alembic 迁移：建列 + §7 回填。

**前端**
- `pages/datasets/list/index.tsx`：删除原「类型」列；新增「来源」列（`sourceKind` 映射，可筛选）+ 「数据类型」列（复用 `SemanticTypeTag`，已存在则保留）；「格式」**不进列表**。筛选区同步：去掉 `DATA_TYPE_ENUM`，加 `SOURCE_KIND_ENUM`。
- `pages/datasets/detail/index.tsx`：元信息区把「类型」改为「来源 / 数据类型 / 格式」三项（来源 = sourceKind 标签、数据类型 = SemanticTypeTag、格式 = source_format 原样或 `-`）。
- 新增 `utils/sourceKind.tsx`：`SOURCE_KIND_META`（值→中文标签/颜色）+ `SourceKindTag` + `SOURCE_KIND_ENUM`，与 `semanticType.tsx` 同构。
- `services/data-platform/typings.d.ts`：`Dataset` / `DatasetDetail` 加 `sourceKind?` / `sourceFormat?`。

**不改**
- `data_type` 列保留（兼容）；`semantic_type` 枚举与校验不动；分类（category）不动；托管（hosted）流程不动。

## 9. 验收标准

1. 新接入的数据集，列表「来源」列显示正确中文（数据库/对象存储/HDFS/本地上传/API 推送），「数据类型」显示语义中文标签，详情页「格式」显示原始格式。
2. 列表可按「来源」「数据类型」筛选。
3. 存量数据集回填后无英文裸值；判不准的显示 `-`，不报错、不臆造。
4. `npm run tsc`、`npx antd lint ./src`、后端 `ruff` 通过；现有测试不回归。
5. 托管数据集（hosted）来源显示 `-`，不报错（本期不处理）。

## 10. 不在本期范围

- 三方 S3 托管数据集的来源/格式归类。
- `data_type` 列的物理删除（仅退出展示）。
- 数据集「分类（category）」相关调整。
- 原始格式的反向重建（仅对新接入捕获；存量按 §7 尽力回填）。
