# 采集调度与增量（切片 C）

- 日期：2026-06-26
- 模块：数据接入 → 采集任务（`backend/app/api/v1/ingest_tasks.py`、连接器、新增调度器、`frontend/src/pages/ingest/tasks`）
- 状态：设计已确认，待写实现计划
- 关联：切片 A（向导+预览）、切片 B（可观测与治理）

## 背景与目标

当前调度只能"单次"：`schedule.mode='cron'` 在 schema 层被 `_reject_cron`（`app/schemas/ingest_task.py`）显式拒绝，
前端 Cron 选项 `disabled`，以杜绝"建了永不触发的任务"。后端**无任何调度运行时**。每次采集都是全量重拉，大表浪费。

本切片引入**定时调度**与**增量采集**：进程内 APScheduler 按 cron 周期触发采集，job 持久化到 PG（进程重启不丢）；
任务可配增量列（时间戳/自增主键），每次只采水位线之后的新增。范围覆盖**数据库源 + 文件源**。
属"丰富采集流程"三方向第三片（C），最重，依赖 A/B 已落地的采集主链路。

### 成功标准

1. 任务可选 Cron 周期调度；到点由 APScheduler 触发，等价于自动 rerun。
2. 调度状态持久化到 PG：后端重启后已有 cron 任务自动恢复继续触发。
3. 数据库任务可配增量列，每次只拉 `增量列 > 上次水位`；水位每次采集后推进并持久化。
4. 文件源任务支持按文件名/mtime 水位，只采上次之后新增的文件。
5. 解除 `_reject_cron`，前端 Cron 选项启用并可填 cron 表达式（已有 UI 雏形）。
6. 调度触发、水位推进、增量查询有测试覆盖意图。

### 非目标

- 不做分布式多副本调度选主（v1 假设单 backend 实例；多 worker 选主留待后续）。
- 不做秒级/事件驱动的实时采集（仅 cron 周期）。
- 不做 CDC（binlog/逻辑复制）增量；增量仅基于用户指定的水位列。
- 不改质量校验（B）、向导（A）。

## 设计

### ① 调度运行时（进程内 APScheduler + PG 持久化）

- FastAPI lifespan 启动时初始化 `AsyncIOScheduler`，jobstore 用 `SQLAlchemyJobStore` 指向同一 PG（独立表，
  与业务表共库不共表），实现"重启恢复"。
- 调度动作 = 调用现有采集执行逻辑（等价 rerun 的内部函数），复用连接器派发、落地、（B 的）质量校验。
- 任务生命周期与调度同步：
  - 创建/更新任务为 `mode='cron'` → upsert 一个 APScheduler job（id = `ingest:{task_id}`，CronTrigger 解析 `schedule.cron`）。
  - 改回 `once` / 删除任务 → remove job。
  - 启动时对账：扫 PG 里所有 `mode='cron'` 任务，确保各有对应 scheduler job（防 jobstore 与业务表漂移）。
- 解除 `app/schemas/ingest_task.py` 的 `_reject_cron`（创建/更新放行 cron，但校验 cron 表达式合法性）。

> 设计权衡：选进程内 APScheduler 而非 K8s CronJob——当前后端是单 uvicorn、docker compose 部署，
> 进程内零额外基建、改动最小；jobstore 持久化解决重启丢失。代价是多副本需选主，列入非目标。

### ② 增量水位（数据库源）

任务新增增量配置 `IngestTask.incremental: JSON | null`（缺省 = 全量，保持现状）：

```jsonc
{ "column": "updated_at", "type": "timestamp" }  // 或 { "column": "id", "type": "integer" }
```

- 水位持久化：新增 `IngestTask.watermark: JSON | null`，形如 `{ "value": "2026-06-26T03:00:00", "updatedAt": ... }`。
  按任务存一份（多表任务 v1 要求增量列在所选表/SQL 语义一致；多表各自水位留待后续，v1 文档化此约束）。
- 增量查询：`_build_queries` 在配了 `incremental` 且 `watermark.value` 非空时，对整表模式追加
  `WHERE <column> > <watermark>`（SQL 模式由用户在 SQL 里自行用占位或不支持，v1 仅整表模式自动增量；
  SQL 模式增量文档化为"自行在 SQL 控制"）。
- 水位推进：本次采集成功后，取本批 `max(增量列)` 写回 `watermark.value`（空批不推进）。
  事务内与落地一起提交，避免"落地成功但水位没推进 → 下次重复采"。

### ③ 增量水位（文件源 S3/HDFS）

- 增量配置复用 `incremental`，但语义为文件级：`{ "by": "mtime" }` 或 `{ "by": "name" }`。
- 水位 `watermark.value` 存"上次已采的最大 mtime 或最大文件名"。
- list 匹配后，过滤掉 `mtime <= 水位`（或 `name <= 水位`）的文件，只采新增；采完推进水位到本批最大值。

### ④ 调度执行与运行记录

- 每次 cron 触发 = 建一条 `Job(type=ingest)`（与手动 rerun 同构），进运行记录，标注触发来源
  （新增 job 字段或 note：`trigger=cron|manual`）。
- 触发时若上一次该任务的 job 仍 running → 跳过本次（防重叠），日志记"上次未完成，跳过本次调度"。

### ⑤ 前端

- 启用 Cron 选项（去掉 `disabled`），cron 表达式输入已有雏形（`schedule.cron`）；加表达式校验与"下次触发时间"预览（前端解析或后端返回）。
- 增量配置 UI：建/编辑任务时，数据库任务可选增量列（从预览/表结构取列名）+ 类型；文件任务选 mtime/name。
- 列表/详情展示：调度周期、下次触发时间、当前水位值、上次触发来源（cron/manual）。

## 数据流

```
建/改 cron 任务 ──► upsert APScheduler job(CronTrigger) ──► jobstore(PG) 持久化
                                                              │ 到点触发
                                                              ▼
                          建 Job(trigger=cron) ──► _build_queries(+WHERE 增量列 > 水位)
                                                              │
                                          连接器拉取(只新增) ──► 落地 + (B)质量校验
                                                              │ 同事务
                                                              ▼
                                          推进 task.watermark = max(增量列/mtime)
后端重启 ──► lifespan 对账: 扫 mode=cron 任务 ↔ jobstore，缺则补 upsert
```

## 错误处理

| 场景 | 行为 |
|---|---|
| cron 表达式非法 | 创建/更新任务 400 + message；不 upsert job |
| 到点触发但上次 run 未完成 | 跳过本次，日志记"跳过本次调度"，不建重复 job |
| 增量列不存在/类型不符 | 采集失败 IngestError + 文案；水位不推进 |
| 空批（无新增） | run success，行数 0，水位不变 |
| 后端重启 | lifespan 对账恢复所有 cron job；运行中断的 job 标记 failed（不自动续跑） |
| watermark 与落地不一致 | 落地+推进同事务提交，保证一致 |

## 测试（验证 intent）

后端：

- CronTrigger 解析 `schedule.cron` 正确；建/改/删任务正确 upsert/remove scheduler job。
- 启动对账：PG 有 cron 任务但 jobstore 缺 job → 补齐（**编码"重启不丢调度"意图**）。
- 增量查询：配 `incremental` 且有水位 → SQL 含 `WHERE col > 水位`；无水位（首跑）→ 全量；
  采集后水位推进到 `max(col)`，空批不推进（**编码"只采新增且不漏不重"意图**）。
- 文件源按 mtime/name 过滤只采新增。
- 重叠触发被跳过。

前端：

- Cron 选项启用、表达式校验、下次触发预览。
- 增量配置写入 `incremental`；列表展示周期/下次触发/水位。

## 影响面（改动清单）

- 后端：新增 `services/scheduler.py`（APScheduler 生命周期 + upsert/remove/对账）、FastAPI lifespan 接入；
  `IngestTask` 加 `incremental`/`watermark`（+ 迁移）；`_build_queries` 支持增量 WHERE；
  连接器落地后推进水位；解除 `_reject_cron` + cron 表达式校验；Job 加 `trigger` 标注（+ 迁移）；
  依赖新增 `apscheduler`。
- 前端：启用 Cron UI + 校验 + 下次触发预览、增量配置项、列表/详情展示水位与触发来源、`api.ts`/`typings` 补字段。
- 不动：A 的预览接口、B 的质量计算（仅被调度路径复用）。
