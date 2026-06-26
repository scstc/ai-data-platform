# 采集调度与增量（切片 C）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 启用 Cron 周期调度（进程内 APScheduler，job 持久化 PG，重启对账恢复）+ 增量采集（用户指定增量列/文件 mtime-name，水位线推进）；覆盖数据库 + 文件源；解除 `_reject_cron`。

**Architecture:** APScheduler 3.x `AsyncIOScheduler` + `SQLAlchemyJobStore`（同 PG，独立表），FastAPI `_lifespan` 启动初始化 + 重启对账；任务 create/update/delete → upsert/remove scheduler job；触发函数复用从 rerun 抽出的共享 `_execute_ingest`（建 Job trigger=cron|manual、跑连接器、推进水位）。增量：`_build_queries` 整表模式按水位加 `WHERE col > watermark`；文件源按 mtime/name 过滤。

**Tech Stack:** FastAPI + APScheduler 3.x + SQLAlchemy + alembic；React + antd v6。

## Global Constraints

- 后端 Python 3.12+；**`uv` 本机 exit 127，禁用**；装包用 `./.venv/Scripts/python.exe -m pip install`，测试 `cd backend && ./.venv/Scripts/python.exe -m pytest`；DB 测试须 `TEST_DATABASE_URL` → `.60:55433/adp_test`（**当前 .60 拒连，DB-backed 测试 DEFERRED**）。
- **APScheduler 必须 3.x（`apscheduler>=3.10,<4`）**：4.x 把 `AsyncIOScheduler`/`SQLAlchemyJobStore` 改名，spec 与本计划用 3.x API。
- 前端 Biome only（禁 `npm run biome` 全仓）；vitest；`npx antd info`；TS strict。
- 约定式提交。最新迁移 `0024`（B）→ 本切片 `0025`。
- C 的 scheduler/jobstore/迁移/水位 DB-backed 测试在 .60 拒连期间 DEFERRED（同 B）；纯函数（cron 校验、增量 WHERE 拼接、文件过滤）可纯单测。

**Spec：** `docs/superpowers/specs/2026-06-26-ingest-02-schedule-incremental-design.md`

**关键设计：** 触发函数复用 rerun 的执行核 → 抽 `_execute_ingest(session, task, datasource, *, trigger) -> results`，rerun 与 scheduler 共用，避免双份逻辑。增量水位同事务推进（落地+推进一起 commit，防"落地成功水位没推进→下次重复采"）。

---

### Task 1: apscheduler 依赖 + 迁移 0025 + 模型 + schema

**Files:**
- Modify: `backend/pyproject.toml`（+`apscheduler>=3.10,<4`）；装包 `.venv/Scripts/python.exe -m pip install "apscheduler>=3.10,<4"`
- Create: `backend/alembic/versions/0025_schedule_incremental.py`
- Modify: `backend/app/models/job.py`（+`trigger`）、`backend/app/models/ingest_task.py`（+`incremental`/`watermark`）
- Modify: `backend/app/schemas/ingest_task.py`（`Incremental` CamelModel + 接入；`IngestSchedule` 仍 mode once|cron——解 cron 在 Task 3）
- Test: `backend/tests/unit/test_schedule_schema.py`

**Interfaces:** `Job.trigger: str`（`manual`|`cron`，server_default manual）；`IngestTask.incremental: JSONB|None`（`{column,type}` 库 / `{by:mtime|name}` 文件）；`IngestTask.watermark: JSONB|None`（`{value, updatedAt}`）。

- [ ] Step 1: 装 apscheduler 3.x + 加 pyproject 依赖。
- [ ] Step 2: 迁移 0025：Job +trigger(String server_default manual)；ingest_tasks +incremental JSONB nullable、+watermark JSONB nullable；downgrade 反向。
- [ ] Step 3: 模型加字段；schema 加 `Incremental`（库 `{column:str, type:Literal[timestamp,integer]}` / 文件 `{by:Literal[mtime,name]}`，model_validator 二选一）+ 接入 Create/Update/Read。
- [ ] Step 4: 单测 schema（incremental 库/文件两种形 + 非法混合拒绝）。
- [ ] Step 5: GREEN + `import apscheduler` 确认 + 回归。
- [ ] Step 6: 提交 `feat(ingest): apscheduler依赖+迁移0025+模型(trigger/incremental/watermark)`。

---

### Task 2: scheduler.py 模块 + lifespan 接入

**Files:**
- Create: `backend/app/services/scheduler.py`
- Modify: `backend/app/main.py:48` `_lifespan`（启动 init+reconcile，关闭 shutdown）
- Modify: `backend/app/core/config.py`（+`scheduler_enabled: bool = True`、复用 DATABASE_URL）
- Test: `backend/tests/unit/test_scheduler.py`（纯函数部分：cron→job-id 映射、reconcile 对账逻辑用 monkeypatched jobstore；**真实 AsyncIOScheduler 启动需 DB → DEFERRED**）

**Interfaces:**
- `init_scheduler() -> AsyncIOScheduler`（SQLAlchemyJobStore 指 PG `apscheduler_jobs` 表，AsyncIOScheduler）。
- `reconcile(session)`：扫 `IngestTask where schedule.mode=cron` ↔ jobstore，缺则 upsert、多则 remove。
- `upsert_cron_job(scheduler, task)` / `remove_cron_job(scheduler, task_id)`：job_id=`ingest:{task_id}`，CronTrigger.from_crontab(task.schedule.cron)，func=触发函数（Task 4 注入，此处先占位 `_trigger_ingest`）。
- `shutdown_scheduler(scheduler)`。

- [ ] Step 1: 写纯函数测试（job_id 映射、reconcile diff 逻辑——用 fake jobstore/session，不启真实 scheduler）。
- [ ] Step 2: 实现 scheduler.py（init/reconcile/upsert/remove/shutdown）。reconcile 用 APScheduler `scheduler.get_jobs()` + DB 扫描比对。
- [ ] Step 3: `_lifespan` 启动时 `scheduler = init_scheduler(); scheduler.start(); await reconcile(session)`，关闭 `scheduler.shutdown()`。用 try/finally 保关闭。settings.scheduler_enabled=False 时跳过（便于测试/禁用）。
- [ ] Step 4: GREEN（纯函数）+ `import` 无副作用。
- [ ] Step 5: 提交 `feat(ingest): scheduler.py(AsyncIOScheduler+PG jobstore)+lifespan 接入`。

---

### Task 3: 解除 _reject_cron + cron 校验 + 路由 upsert/remove

**Files:**
- Modify: `backend/app/schemas/ingest_task.py`（删 `_reject_cron`/两处校验器调用；加 `_validate_cron`：mode=cron 时 cron 非空且合法）
- Modify: `backend/app/api/v1/ingest_tasks.py`（create/update 成功后 upsert_cron_job；delete 前 remove_cron_job）
- Test: `backend/tests/unit/test_cron_validation.py`（纯：合法 cron 通过、非法拒绝、mode=once 不要求 cron）

**Interfaces:** create/update 任务为 cron → upsert scheduler job；改 once / 删除 → remove。cron 合法性用 `CronTrigger.from_crontab` 解析（抛则拒）。

- [ ] Step 1: 测试（合法 `0 2 * * *` 通过；非法 `99 99` 拒；once 不校验 cron）。
- [ ] Step 2: 删 `_reject_cron`；加 `_validate_cron`（mode=cron 时 from_crontab 校验）。create/update 保留校验器调用但换成 `_validate_cron`。
- [ ] Step 3: 路由 create/update 成功后（scheduler 启用时）upsert；delete 前 remove。scheduler 未启用/未初始化时安全降级（try/except 记日志，不阻断主流程——采集本身不依赖调度器在线）。
- [ ] Step 4: GREEN + 回归（既有 once 任务创建/删除不破）。
- [ ] Step 5: 提交 `feat(ingest): 解除 _reject_cron+cron 校验+路由 upsert/remove scheduler job`。

---

### Task 4: _execute_ingest 共享核 + scheduler 触发函数

**Files:**
- Modify: `backend/app/api/v1/ingest_tasks.py`（抽 `_execute_ingest(session, task, datasource, *, trigger) -> list[(ds,ver)]` from rerun；rerun 改调用它；scheduler 触发函数调它）
- Modify: `backend/app/services/scheduler.py`（`_trigger_ingest`：建 Job trigger=cron、调 `_execute_ingest`、同事务推进水位(Task 5)、重叠跳过）
- Test: `backend/tests/unit/test_execute_ingest.py`（monkeypatch connector，断言 trigger 透传到 Job；重叠时跳过——上一次 running 则不建新 Job）

**Interfaces:** `_execute_ingest` 是 rerun 与 scheduler 的共用执行核；`_trigger_ingest(task_id)` 供 APScheduler 调度调用。

- [ ] Step 1: 测试（_execute_ingest 建 Job 带 trigger；rerun trigger=manual；_trigger_ingest trigger=cron；重叠跳过）。
- [ ] Step 2: 从 rerun 抽 `_execute_ingest`（含建 Job、connector.run_ingest、B 的质量策略评估、状态机）；rerun 调用它 trigger=manual。
- [ ] Step 3: `_trigger_ingest(task_id)`：查 task；若该任务最近 Job 仍 running → 跳过+日志；否则 `_execute_ingest(trigger=cron)` + 水位推进（Task 5）。
- [ ] Step 4: GREEN + 回归（rerun 行为零变化——既有 test_ingest_tasks rerun 用例不破）。
- [ ] Step 5: 提交 `feat(ingest): 抽 _execute_ingest 共享核+scheduler 触发(重叠跳过)`。

---

### Task 5: 增量采集（DB WHERE + 文件过滤 + 水位推进）

**Files:**
- Modify: `backend/app/services/connectors/base.py` `_build_queries`（整表+incremental+watermark → `SELECT ... WHERE col > watermark`）
- Modify: `backend/app/services/connectors/objectstore.py`/`hdfs.py` run_ingest（按 incremental.by=mtime|name 过滤 keys/paths）
- Modify: `_execute_ingest`（成功后推进 task.watermark = max(增量列/文件 mtime-name)，同事务）
- Test: `backend/tests/unit/test_incremental.py`（纯：_build_queries 增量 WHERE；文件 key 过滤；首跑无水位=全量；空批不推进）

**Interfaces:** 增量仅 DB 整表模式自动（SQL 模式文档化"自行控制"）；文件按 mtime/name。水位持久化 IngestTask.watermark。

- [ ] Step 1: 纯测试（增量 WHERE 拼接正确+引用增量列；文件 mtime/name 过滤；首跑全量；空批不推进）。
- [ ] Step 2: `_build_queries` 整表模式：task.incremental + watermark.value 非空 → `SELECT cols FROM tbl WHERE "增量列" > :watermark`（参数化或字面值，注意类型）。
- [ ] Step 3: objectstore/hdfs：list 匹配后按 watermark 过滤（mtime > 水位 或 name > 水位），只采新增。
- [ ] Step 4: `_execute_ingest` 成功后：取本批 max(增量列)/max(mtime|name) 写回 task.watermark（空批不动），同事务 commit。
- [ ] Step 5: GREEN + 回归（无 incremental 的任务零变化）。
- [ ] Step 6: 提交 `feat(ingest): 增量采集(DB WHERE+文件过滤+水位推进)`。

---

### Task 6: 前端 Cron UI 启用 + 增量配置 + 调度/水位展示

**Files:**
- Modify: `frontend/src/pages/ingest/tasks/index.tsx`（Cron 选项启用 + 校验 + 下次触发预览；增量配置；列表/详情展示周期/下次触发/水位）
- Modify: `frontend/src/services/data-platform/typings.d.ts`（schedule.cron 已有；+incremental/watermark/trigger 类型）
- Test: `frontend/src/pages/ingest/tasks/index.test.tsx`

- [ ] Step 1: typings 补 incremental/watermark/trigger。
- [ ] Step 2: Cron 选项去掉 disabled；cron 输入 + 校验（前端 5 段格式）；下次触发时间预览（前端解析或省略）。
- [ ] Step 3: 增量配置 UI：库任务选增量列+类型；文件任务选 mtime/name；写入 extract... 实际 incremental 在 task 级（非 extract），放"落地确认"步 + 编辑表单。
- [ ] Step 4: 列表/详情展示调度周期、下次触发（若有）、当前水位、上次触发来源(cron|manual)。
- [ ] Step 5: vitest + tsc + biome(scoped) + antd lint。
- [ ] Step 6: 提交 `feat(ingest): 前端启用 Cron+增量配置+调度/水位展示`。

---

## 完成验证（切片 C）
- 纯单元测试全绿（cron 校验、增量 WHERE、文件过滤、_execute_ingest、reconcile 逻辑）。
- **DB-backed 测试（scheduler 启动、jobstore 持久化、迁移 0025、水位推进）DEFERRED 至 .60 恢复**——同切片 B。
- 手测（.60 恢复后）：建 cron 任务 → 到点自动触发等价 rerun；增量任务只采新增；重启后 cron 任务恢复。

## Self-Review（写完后填）
- Spec 覆盖：Cron 调度(T2/T3)、持久化+对账(T2)、增量水位(T5)、文件源(T5)、解除 _reject_cron(T3)、触发来源标注(T4)。
- APScheduler 3.x 锁定（4.x API 不兼容）。
- _execute_ingest 共享核避免 rerun/scheduler 逻辑双份。
- 增量同事务推进（不漏不重）。
