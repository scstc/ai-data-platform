# 采集运行可观测与治理（切片 B）

- 日期：2026-06-26
- 模块：数据接入 → 采集任务（`backend/app/api/v1/ingest_tasks.py`、连接器落地链路、`frontend/src/pages/ingest/tasks`）
- 状态：设计已确认，待写实现计划
- 关联：切片 A（向导+预览，`2026-06-26-ingest-wizard-preview-design.md`）、切片 C（调度+增量）

## 背景与目标

当前采集运行可观测仅限：详情 Drawer 的运行记录（jobs `type=ingest`）、产物列表、文本日志，列表 5s 轮询进度。
`DatasetVersion` 记了 `rows`/`size`，但**没有列级统计、没有 schema 快照**，无法回答"这次采的数据质量如何 / 表结构变没变"。
失败的 run 也没有便捷重试入口。

本切片给采集补"采得稳、可追溯"的治理能力：**采集质量校验**（行数 + 空值率 + schema 快照与漂移对比）、
**可配阈值阻断**、**失败手动重试**。属"丰富采集流程"三方向第二片（B）。

### 成功标准

1. 每次采集落地的版本记录质量指标：总行数、各列空值率、列名+类型 schema 快照。
2. 与该数据集上一版本对比，识别 schema 漂移（列增/减/类型变）并在前端高亮。
3. 任务可配阈值（空值率上限 / 是否允许 schema 漂移）；超阈值时该版本标记不通过并阻断发布，run 记 failed-quality。
4. 失败（含质量阻断）的 run 在详情/列表有"重试"入口（复用 rerun）。
5. 质量指标、漂移结论、阈值策略均有测试覆盖意图。

### 非目标

- 不做向导/预览（切片 A）、不做调度/增量（切片 C）。
- 质量指标 v1 只做行数/空值率/schema；不做主键重复率、数值分布、采样分位数（后续迭代）。
- 不做自动重试（仅手动按钮）；不做告警外发（邮件/IM）。

## 设计

### ① 质量指标的计算与存储

**计算时机**：落地时。在 `land_records`（受管落地公共入口）与 `generate-dataset` 的 parquet 落地处，
对最终记录集算一次质量指标——单点接入，rerun 与生成数据集两条路径都覆盖。

**指标**（对一个版本的记录集）：

```jsonc
{
  "rows": 12345,
  "columns": [
    { "name": "id", "type": "int8", "nullRate": 0.0 },
    { "name": "email", "type": "text", "nullRate": 0.03 }
  ]
}
```

- `nullRate` = 该列 null/空串占比（结构化记录按 key 缺失或值为 null 计）。
- `type`：数据库走驱动字段类型；文件/半结构化走值类型推断（多类型取出现最多者，标 `mixed` 兜底）。

**存储**：在 `DatasetVersion` 新增两列（版本不可变，质量随版本固化）：

- `quality_stats: JSON | null` —— 上面的指标对象。
- `schema_snapshot: JSON | null` —— `[{name, type}]`，便于跨版本对比（与 `quality_stats.columns` 冗余一份精简结构，
  对比只读它，避免拉全量统计）。

迁移：新增 alembic 版本加这两列（nullable，存量版本为空，前端按空降级显示）。

### ② schema 漂移对比

落地新版本时，取同 `dataset_id` 的上一版本 `schema_snapshot` 做 diff：

```jsonc
{
  "added":   [{ "name": "phone", "type": "text" }],
  "removed": [{ "name": "fax", "type": "text" }],
  "typeChanged": [{ "name": "age", "from": "text", "to": "int8" }]
}
```

- 首版本（无上一版）→ 无漂移。
- diff 结论随 run 写入 job 日志，并可由前端按 `schema_snapshot` 现算展示（不单独建表）。

### ③ 阈值策略与阻断

阈值挂在任务上，新增 `IngestTask.quality_policy: JSON | null`（建/编辑任务时可配，缺省 = 不阻断，纯记录）：

```jsonc
{
  "maxNullRate": 0.2,        // 任一列空值率超此值 → 不通过(null=不校验)
  "blockOnSchemaDrift": true // 出现 added/removed/typeChanged → 不通过(默认 false)
}
```

落地后评估：

- 通过 → 版本照常 `publish_status=draft`（沿用现状），run `success`。
- 不通过 → 版本写入但标记 `publish_status` 维持 draft 且 `quality_verdict=failed`（**新增版本字段
  `quality_verdict: passed|failed|skipped`**，默认 skipped=未配策略）；run 记 `failed`，
  error 写明原因（如"email 空值率 0.31 超阈值 0.20"/"检测到 schema 漂移：+phone -fax"）。**阻断指阻断发布**
  （`publish_status` 不得进 published），不删除已落地数据——数据保留供排查。

> 设计权衡：阻断只作用于"发布门"，不回滚已落地版本。理由——采集数据已是事实，删除会丢证据；
> 治理目标是拦住坏数据进入下游（algo 只消费 published），而非阻止落地。

### ④ 失败重试（前端）

- 列表/详情 Drawer 对 `status=failed` 的任务/run 显示"重试"入口，点击复用现有 `rerunIngestTask`。
- 当前列表已有"运行"按钮（非 running 时显示）；本切片把失败态文案/位置明确为"重试"，并在详情 Drawer
  的运行记录行也加"重试"动作。无新后端接口。

### ⑤ 运行记录与详情强化（前端）

详情 Drawer 运行记录表与产物区增强（数据已有，主要是呈现）：

- 每个版本产物行展示：行数、`quality_verdict` 标签（通过/不通过/未校验）、点开看列级空值率 + schema 漂移 diff。
- run 失败时直接展示 error（质量阻断原因）。

## 数据流

```
运行/生成数据集 → 拉取记录 → (A 的列裁剪) → land_records / parquet 落地
                                   │
                                   ├─ 算 quality_stats + schema_snapshot
                                   ├─ 取上一版 schema_snapshot → diff 漂移
                                   ├─ 按 task.quality_policy 评估 → quality_verdict
                                   │     └ 不通过: version.quality_verdict=failed, job=failed(+原因)
                                   └─ 写 DatasetVersion(quality_stats/schema_snapshot/quality_verdict)
前端详情 Drawer ← 读版本质量字段 + 现算 schema diff 呈现
```

## 错误处理

| 场景 | 行为 |
|---|---|
| 未配 quality_policy | quality_verdict=skipped，纯记录指标，不阻断 |
| 空值率超阈值 | version.quality_verdict=failed，job failed + 列名/实际值/阈值文案 |
| schema 漂移且 blockOnSchemaDrift | 同上，文案列出 added/removed/typeChanged |
| 首版本无对比基线 | 无漂移，正常评估空值率 |
| 指标计算自身异常 | 不阻断落地；quality_stats 留空 + 日志告警（治理失败不该连累采集主流程） |

## 测试（验证 intent）

后端：

- 落地后 `quality_stats` 行数/空值率计算正确（构造含 null 的记录集）。
- schema diff 正确识别列增/减/类型变；首版本无漂移。
- `quality_policy` 阈值评估：超空值率 / 命中漂移 → quality_verdict=failed + job failed（**编码"超阈值即拦发布门"意图**）；
  未配策略 → skipped 且不阻断（**编码"治理可选、缺省不打断采集"意图**）。
- 指标计算异常不影响版本落地（容错隔离）。

前端：

- 详情 Drawer 渲染质量标签 / 空值率 / schema diff；失败 run 显示重试并触发 rerun。
- 建/编辑任务可配 quality_policy。

## 影响面（改动清单）

- 后端：`DatasetVersion` 加 `quality_stats`/`schema_snapshot`/`quality_verdict` + alembic 迁移；
  `IngestTask` 加 `quality_policy` + 迁移；质量计算辅助（`services/quality.py` 新增）接入 `land_records` 与 generate-dataset；
  schemas（version 读出质量、task 读写 policy）。
- 前端：详情 Drawer 呈现强化、失败"重试"、建/编辑任务表单加 quality_policy 配置项、`api.ts`/`typings` 补字段。
- 不动：连接器拉取逻辑、A 的向导/预览。
