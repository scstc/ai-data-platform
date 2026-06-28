# 站内通知中心(Notification Center)设计

> 日期:2026-06-28 · 分支:dev · 状态:待实现
>
> 目标:采集任务与所有 data-juicer 任务在到达终态时,给任务创建者本人写一条站内通知;
> 前端顶部铃铛展示未读计数与通知列表。

## 1. 背景与动机

平台有两类长跑任务,跑完后用户无从感知结果,只能手动刷列表:

- **采集任务** `IngestTask`:经 `_execute_ingest`(手动或 cron 触发)拉数落地,
  终态 `success` / `failed`(见 `backend/app/api/v1/ingest_tasks.py:475-500`)。
- **所有 data-juicer 任务** `Job`:`process` / `clean` / `quality` / `distillation` /
  `synthesis` / `augmentation` / `review`,统一经 `job_runner._run_job` 执行,
  终态在 `finally` 块落定(`success` / `failed` / `cancelled` / `paused`)。

需要一个**站内通知中心**:任务成功/失败各发一条通知给创建者,前端铃铛可见。

## 2. 范围

### 触发条件
- 仅在 **`success`** 与 **`failed`** 两个终态触发。
- **不**通知 `cancelled` / `paused`——这两者是用户主动操作,无需通知自己。

### 接收者
- 任务创建者本人:`Job.created_by` / `IngestTask.creator`(均为 username 字符串)。

### 交付形态
- 站内通知中心:后端 `notifications` 表 + REST API,前端顶部铃铛 + 未读 Badge + 下拉列表。
- 未读计数采用**前端轮询** `GET /notifications/unread-count`(不引入 SSE/WebSocket)。
- 外部推送(飞书/钉钉/邮件)**不在本期范围**,但表结构与 `emit()` 留作后续扩展不阻塞。

## 3. 架构

**方案 A(选定):通用 `notifications` 表 + 单一 `emit()`,在两处终态 choke point 调用。**

否决的备选:
- 方案 B 事件总线/观察者:为未来外部推送更可扩展,但当前 YAGNI,过度设计。
- 方案 C DB 触发器 / 轮询 jobs 表:去重难、状态机漂移脆弱,否决。

```
任务终态 ──┐
           ├─ job_runner._run_job() finally        ─┐
           └─ _execute_ingest() success/failed 分支 ─┤
                                                     ▼
                              notifications.emit(session, recipient,
                                  level, source_type, source_id, title, body)
                                                     │
                                                     ▼   (与任务终态同一事务一起 commit)
                                              notifications 表
                                                     ▲
前端顶部铃铛(轮询)── GET /notifications · /unread-count
                    POST /notifications/{id}/read · /read-all
```

**关键决策:`emit` 复用 choke point 当前的 session**,通知行与任务终态在**同一事务**内落库
(避免"任务已落终态但通知丢失"的不一致)。但 `emit` 内部异常**只 loud log、不向上抛**——
通知失败绝不能让一个已成功的任务被判失败(见 §6)。

## 4. 数据模型

新表 `notifications`,配套迁移 `backend/alembic/versions/0027_notifications.py`。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | str PK | 形如 `ntf-` + 6 位 hex |
| `recipient` | str, 索引 | 接收者 username(`created_by`/`creator`) |
| `level` | str | `success` \| `error` |
| `source_type` | str | `job` \| `ingest_task` |
| `source_id` | str | 来源任务 id(`job-…` / `task-…`) |
| `title` | str | 如 "蒸馏任务 xxx 已完成" |
| `body` | str \| null | 失败时放 error 摘要(截断) |
| `read` | bool, 默认 false, 索引 | 已读标记 |
| `created_at` | datetime, server_default now() | |
| `read_at` | datetime \| null | 标记已读时间 |

无 DB 级外键(沿用本仓库 `llm_usage` / `ingest_task` 弱关联约定)。
索引:`recipient`、`(recipient, read)` 复合(未读计数与列表过滤走此索引)。

## 5. 组件(各自单一职责)

### 后端
- `app/models/notification.py` — `Notification` ORM。
- `app/schemas/notification.py` — `NotificationOut`、分页列表响应、`UnreadCount`。
- `app/services/notifications.py`:
  - `emit(session, *, recipient, level, source_type, source_id, title, body=None)`
    — 构造一行 `add` 到传入 session(不自行 commit,随 choke point 事务一起提交)。
  - `list_for(session, recipient, *, only_unread, page, page_size)`。
  - `unread_count(session, recipient)`。
  - `mark_read(session, recipient, notification_id)`(只能标自己的;幂等)。
  - `mark_all_read(session, recipient)`。
- `app/api/v1/notifications.py` — 4 个端点,RBAC 复用 `app/api/deps.py` 取当前用户:
  - `GET /api/v1/notifications?only_unread=&page=&page_size=`
  - `GET /api/v1/notifications/unread-count`
  - `POST /api/v1/notifications/{id}/read`
  - `POST /api/v1/notifications/read-all`
  - 所有端点强制 `recipient = 当前用户`,**不可见他人通知**。
- 路由注册:`app/main.py` / `app/api/v1/__init__.py`(沿用既有 include_router 约定)。

### 两处 choke point 改动(最小侵入)
- `job_runner._run_job` 的 `finally`/收尾:在落终态 commit 前,若 `job.state in {success, failed}`,
  调 `emit(session, recipient=job.created_by, level=…, source_type="job", source_id=job.id, …)`。
- `_execute_ingest` 的 success / failed 分支:同样调 `emit(... source_type="ingest_task",
  source_id=task.id, recipient=task.creator ...)`。
  > 注意:ingest 每次运行也会建一条 `Job(type=ingest)`,但该 Job **不**经 `job_runner._run_job`
  > (`_execute_ingest` 直接设 `job.state`),故必须在 `_execute_ingest` 单独埋点,
  > 二者不会重复发(job_runner 不处理 ingest 类型)。

### 前端
- `src/services/data-platform/` 新增 `notifications` service + `typings.d.ts` 类型。
- 顶部 RightContent(Ant Design Pro 既有 `src/components/RightContent` 约定)加铃铛 `Badge`:
  - 轮询 `unread-count`(约 30s 一次)。
  - 下拉 `Popover`/`Dropdown` 显示最近 N 条,点击跳对应任务页、可标记已读 / 全部已读。
- 不新增独立"通知列表页"为必须项(可选);最小闭环为铃铛下拉。

## 6. 错误处理

- `emit` 内任何异常 → `logging.exception` loud log 后**吞掉**,不向 choke point 调用者传播。
  理由:通知是旁路,任务终态是主路;通知失败不得污染任务结果(Rule 12 例外的合理化——
  这里"fail loud"体现在日志,而非让无关任务连带失败)。
- 因 `emit` 与任务终态同事务:若整个 commit 失败,任务终态本身也会回滚走既有失败路径,
  不产生"通知有、任务无"的孤儿。

## 7. 测试(验证意图,非仅行为)

- `Job` 成功 / 失败各产**恰好一条**通知,`recipient == job.created_by`,`level` 正确。
- `IngestTask` 成功 / 失败同样各产一条,`recipient == task.creator`,`source_type == ingest_task`。
- `cancelled` / `paused` 的 Job **不产**通知(防"主动操作也打扰自己"的回归)。
- ingest 运行只产**一条**通知(验证 job_runner 不重复处理 ingest 类型)。
- `mark_read` 幂等;`mark_all_read` 只影响当前用户。
- 越权:用户 A 不能 list / mark 用户 B 的通知(验证 recipient 强隔离)。
- `emit` 抛异常时任务终态不受影响(注入失败,断言 job.state 仍为 success)。

## 8. 不做(YAGNI)

- 外部推送(飞书/钉钉/邮件)、SSE/WebSocket 实时推送、通知偏好设置、按部门/管理员广播、
  通知归档/清理策略——均不在本期,表结构不阻塞后续接入。
