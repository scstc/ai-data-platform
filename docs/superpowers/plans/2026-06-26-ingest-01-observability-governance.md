# 采集运行可观测与治理（切片 B）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 每次采集落地记录结构化质量（行数 + 各列空值率 + 列 schema 快照），任务可配阈值（空值率上限 / schema 漂移阻断），超阈值版本标 `quality_verdict=failed` 并阻断发布门，失败 run 可手动重试。

**Architecture:** 新模块 `app/services/ingest_quality.py`（纯函数：算 stats / schema 快照 / 漂移 diff / 策略评估）；`land_records` 落地时**只算并存结构化 stats**（无任务上下文，verdict 留默认 skipped）；**路由**（rerun / generate-dataset）拿 task.quality_policy 评估并写 quality_verdict。schema 漂移仅在 generate-dataset 路径触发（同数据集多版本；land_records 总建新数据集 v1 无前序版本）。

**Tech Stack:** FastAPI + SQLAlchemy + alembic + pytest；React + antd v6 + ProComponents。

## Global Constraints

- 后端 Python 3.12+；**`uv` 本机 exit 127，禁用**，测试 `cd backend && ./.venv/Scripts/python.exe -m pytest`；DB 测试须设 `TEST_DATABASE_URL` → `.60:55433/adp_test`。
- 纯单元测试不打 DB（`tests/unit/`）；模型/迁移测试用 DB fixture。
- **已有 `app/services/quality.py` 是 dj-analyze 逐条质量评估（不同关注点）—— 本切片新建 `ingest_quality.py`，绝不改动/复用 quality.py。**
- 前端 Biome only（**禁用 `npm run biome` 全仓**，scope 到文件）；vitest（非 jest）；`npx antd info <组件>` 先查；TS strict。
- 约定式提交。
- 最新迁移 `0023` → 本切片 `0024`。

**Spec：** `docs/superpowers/specs/2026-06-26-ingest-01-observability-governance-design.md`

**关键设计决策（澄清 spec）：** `land_records`（landing.py:269）总建**新数据集 v1**，无前序版本 → 漂移 diff 仅在 generate-dataset 路径（task 绑定数据集，v1/v2/...）有效。故 land_records 只算+存 stats（结构化，总是做），**策略评估在路由**（有 task 上下文）。upload 等无 task 的调用方 verdict 自然保持 skipped。

---

### Task 1: 迁移 0024 + 模型字段 + schema

**Files:**
- Create: `backend/alembic/versions/0024_ingest_quality.py`
- Modify: `backend/app/models/dataset_version.py`（+`quality_stats`/`schema_snapshot`/`quality_verdict`）
- Modify: `backend/app/models/ingest_task.py`（+`quality_policy`）
- Modify: `backend/app/schemas/ingest_task.py`（`IngestTaskCreate/Update/Read` 读写 `qualityPolicy`；`DatasetVersion` 读出 quality 字段——确认 Read schema 位置）
- Test: `backend/tests/unit/test_ingest_quality_schema.py`

**Interfaces:**
- Produces: `DatasetVersion.quality_stats: JSONB|None`、`schema_snapshot: JSONB|None`、`quality_verdict: str`（`skipped`|`passed`|`failed`，server_default `skipped`）；`IngestTask.quality_policy: JSONB|None`。

- [ ] Step 1: 迁移 + 模型字段（先写，因后续 task 依赖）。新建 `0024_ingest_quality.py`，op.add_column 三列到 `dataset_versions`（quality_stats JSONB nullable、schema_snapshot JSONB nullable、quality_verdict String server_default 'skipped'），add_column quality_policy JSONB nullable 到 ingest_tasks；downgrade 反向 drop。仿 `0018_dataset_tags.py` 风格。
- [ ] Step 2: 模型加字段（`dataset_version.py` / `ingest_task.py`，JSONB 用 `sqlalchemy.dialects.postgresql.JSONB`，quality_verdict 用 String server_default="skipped"）。
- [ ] Step 3: schema 读写 `qualityPolicy`（`IngestSchedule` 旁的 CamelModel；可选字段，校验：maxNullRate ∈ [0,1]，blockOnSchemaDrift bool）。
- [ ] Step 4: 单测 `test_ingest_quality_schema.py`：`IngestTaskCreate` 带/不带 qualityPolicy 序列化正确；非法 maxNullRate(>1) 被拒。
- [ ] Step 5: 迁移可跑验证：`cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_ingest_quality_schema.py -v`（迁移本身需 DB，单独的迁移测试可选——至少 import 模型确认字段在）。
- [ ] Step 6: 提交 `feat(ingest): 迁移0024+模型 采集质量字段(quality_stats/schema_snapshot/quality_verdict/policy)`。

---

### Task 2: ingest_quality.py 纯函数模块

**Files:**
- Create: `backend/app/services/ingest_quality.py`
- Test: `backend/tests/unit/test_ingest_quality.py`

**Interfaces:**
- Produces:
  - `compute_quality_stats(records) -> {rows:int, columns:[{name,type,nullRate}]}`
  - `schema_snapshot(stats) -> [{name,type}]`
  - `drift_diff(prev:[{name,type}], curr:[{name,type}]) -> {added,removed,typeChanged}`
  - `evaluate_policy(policy:dict|None, stats, drift) -> (verdict:str, reason:str|None)` — policy None/缺省 → ("skipped",None)；超 maxNullRate → ("failed",列名+实际+阈值)；blockOnSchemaDrift 且有漂移 → ("failed",漂移明细)；否则 ("passed",None)。

- [ ] Step 1: 写失败测试（纯函数）：空值率计算、类型推断、drift diff（增/减/类型变）、policy 评估各分支（None=skipped / 超空值率=failed / 漂移阻断=failed / 通过=passed）。
- [ ] Step 2: 实现 `ingest_quality.py`（复用 preview 的类型推断思路——但**不 import preview**避免耦合；本模块独立实现，结构化记录按 key 缺失/None 计空值）。
- [ ] Step 3: GREEN + 全 `tests/unit/` 回归。
- [ ] Step 4: 提交 `feat(ingest): ingest_quality 纯函数(stats/schema快照/漂移/策略评估)`。

---

### Task 3: land_records 落地时算并存 stats（结构化，不评估策略）

**Files:**
- Modify: `backend/app/services/landing.py:359`（DatasetVersion 创建处）
- Test: `backend/tests/unit/test_landing_quality_stats.py`（mock upload，断言 version 带 quality_stats/schema_snapshot）

**Interfaces:**
- Consumes: B2 `compute_quality_stats`/`schema_snapshot`。
- Produces: `land_records` 返回的 version 带 `quality_stats` + `schema_snapshot`（quality_verdict 保持默认 skipped——策略在路由评估）。

- [ ] Step 1: 测试：land_records 落地后 version.quality_stats.rows==len(records)、columns 含空值率、schema_snapshot 存在；quality_verdict=="skipped"（未评估）。
- [ ] Step 2: 在 land_records 的 DatasetVersion(...) 构造里加 `quality_stats=...`、`schema_snapshot=...`（用 compute_quality_stats(records) + schema_snapshot）。注意 parquet/jsonl 分支后、commit 前。records 此刻已过语义归一，用归一后 records 算。
- [ ] Step 3: GREEN + 回归（land_records 多处调用零回归）。
- [ ] Step 4: 提交 `feat(ingest): land_records 落地存 quality_stats+schema_snapshot`。

---

### Task 4: rerun 路由应用 quality_policy（空值率阈值，漂移 N/A）

**Files:**
- Modify: `backend/app/api/v1/ingest_tasks.py` rerun（:376 results 循环后）
- Test: `backend/tests/unit/test_rerun_quality_policy.py`（monkeypatch connector 返回带 stats 的 version）

**Interfaces:**
- Consumes: B2 `evaluate_policy`；task.quality_policy。
- Produces: rerun 后对每个产物 version 评估 policy；failed → version.quality_verdict="failed" + job.state="failed" + error 文案；passed/skipped 不变。

- [ ] Step 1: 测试：task 带 maxNullRate=0.2，connector 返回 version（空值率 0.3 的列）→ 断言 version.quality_verdict=="failed"、job failed、error 含列名+0.3+0.2。policy None → skipped 不阻断。
- [ ] Step 2: rerun results 循环后，读 task.quality_policy，对每个 (ds, ver) 调 evaluate_policy（drift=None，新数据集无前序）；写 ver.quality_verdict；任一 failed → task.status/job.state=failed + logs 记原因。
- [ ] Step 3: GREEN + 回归（test_ingest_tasks.py 既有 rerun 用例不破）。
- [ ] Step 4: 提交 `feat(ingest): rerun 应用 quality_policy(空值率阈值阻断发布门)`。

---

### Task 5: generate-dataset 路由应用 policy + 漂移对比

**Files:**
- Modify: `backend/app/api/v1/ingest_tasks.py` generate-dataset（:518 version 创建处）
- Test: `backend/tests/unit/test_generate_dataset_quality.py`

**Interfaces:**
- Consumes: B2 全套；task.quality_policy；上一版本 schema_snapshot。
- Produces: generate-dataset 版本带 quality_stats/schema_snapshot/quality_verdict；漂移对比前序版本；超阈值 → 400 或 version failed。

- [ ] Step 1: 测试：首版本无漂移；第二版本列增/减/类型变 → drift_diff 正确；blockOnSchemaDrift → quality_verdict=failed；maxNullRate 超阈 → failed。
- [ ] Step 2: 在 version 创建前算 stats + 取上一版 schema_snapshot + drift_diff + evaluate_policy；写 quality_verdict；failed → 返回 400/失败文案（generate-dataset 是同步返回，失败用 JSONResponse 400 + message，或 version 标 failed + 200 带告警——按 spec「阻断发布门不删数据」，选 version.quality_verdict=failed + 响应带告警，不 400，让用户看到数据但标红）。
- [ ] Step 3: GREEN + 回归。
- [ ] Step 4: 提交 `feat(ingest): generate-dataset 质量+schema漂移对比+策略评估`。

---

### Task 6: 前端详情质量呈现 + 失败重试 + 任务配 quality_policy

**Files:**
- Modify: `frontend/src/pages/ingest/tasks/index.tsx`（详情 Drawer 产物行 + 失败重试 + 建/编辑表单加 qualityPolicy 配置）
- Modify: `frontend/src/services/data-platform/typings.d.ts`（+quality 字段）
- Test: `frontend/src/pages/ingest/tasks/index.test.tsx`

- [ ] Step 1: typings 加 DatasetVersion.qualityStats/schemaSnapshot/qualityVerdict + IngestTask.qualityPolicy。
- [ ] Step 2: 详情 Drawer 产物行：展示行数 + qualityVerdict 标签（通过/不通过/未校验）+ 展开看列空值率 + schema 漂移 diff。
- [ ] Step 3: 失败 run（含质量阻断）详情/列表"重试"入口（复用 rerunIngestTask）。
- [ ] Step 4: 建/编辑任务表单加 qualityPolicy 配置（maxNullRate 数字 + blockOnSchemaDrift 开关；可空=不校验）。注意：A 切片新建已改 StepsForm——qualityPolicy 放"落地确认"步；编辑 ModalForm 也加。
- [ ] Step 5: vitest + tsc + biome(scoped) + antd lint。
- [ ] Step 6: 提交 `feat(ingest): 详情质量呈现+失败重试+任务配 quality_policy`。

---

## 完成验证（切片 B）
- 后端 `tests/unit/` 全绿；前端 `npm run lint` 全绿。
- 手测：建带 maxNullRate 的任务 → rerun 高空值率数据 → 版本标 failed + job failed；详情看到空值率；重试入口可用。

## Self-Review（写完后填）
- Spec 覆盖：质量指标(B2/B3)、漂移对比(B5)、阈值阻断(B4/B5)、手动重试(B6)。
- 命名：ingest_quality.py ≠ 已有 quality.py（不同关注点），已隔离。
- land_records 只算结构化、路由评估策略——职责分离，upload 等无 task 调用方 verdict=skipped。
