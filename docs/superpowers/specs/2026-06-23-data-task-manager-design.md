# 数据任务统一控制台 — 设计稿

- 日期：2026-06-23
- 状态：已定稿，进入实现
- 范围：数据治理（蒸馏/合成/增强/加工/内容安全）+ 数据评估（质量评估）各任务的统一运维控制台

## 决策（已锁定）

1. **暂停语义 = 停止 + 可恢复**：`暂停` 杀子进程并置 `paused`（保留 `spec`）；`继续` 按 `spec` 从头重跑。复用现有 `job_runner`，重启后 paused 存活。UI 诚实告知"继续将从头重跑"。
2. **页面定位 = 运维控制台**：跨类型统一列表 + 状态 + 暂停/继续/停止/重跑/删除 + 出入参数据集多版本预览。新建任务仍走各类型 editor；类型 list 页保留不破坏。
3. **评估类改异步**：quality/review 迁到 `job_runner.spawn`，获得全状态生命周期，与治理类统一。
4. **后端方案 = 叠加门面（A）**：新增 `/data-tasks` 统一列表；pause/resume 加到通用 `/jobs`；5 个类型 router 原样保留。

## 现状关键事实

- `jobs` 表已是统一表：`Job.type`（自由字符串）+ `Job.state ∈ {pending,running,success,failed,cancelled}`。9 个类型，6 个 router 写入，大量复制粘贴，无公共基类。
- 前端 `/ops/data-tasks`（`数据任务`）已是 6 行 `PlaceholderPage` 占位，菜单已配，数据集血缘深链已指向 `/ops/data-tasks?highlight=<jobId>`。
- 数据集多版本按文件预览 + kkFileView + 安全门已实现于 `datasets/detail/index.tsx`（内联，未抽组件）。
- 运行时：治理类异步（`job_runner.spawn` + dj-process 子进程，信号量并发=3）；评估类同步阻塞 HTTP 请求。`reconcile_orphans` 重启时把 pending/running 置 failed。
- 无 Celery/APScheduler；唯一后台设施是进程内 asyncio。

## §1 架构与组件

后端：新增 `app/api/v1/data_tasks.py`（统一列表）；改 `app/api/v1/jobs.py`（pause/resume + 放宽 stop/delete）；改 `job_runner.py`+`engine.py`（pause/resume 运行时，复用 `terminate_job`）；改 `quality.py`/`content_safety.py`+`job_runner._run_job` 分发表（评估异步化）；改 `schemas/job.py`（`JobRead` 加 canPause/canResume/canStop）；`main.py` 注册新 router；可选 `alembic/0019` 复合索引。

前端：替换 `src/pages/data-tasks/index.tsx`；抽 `src/pages/data-tasks/components/DatasetVersionPreview.tsx`；`datasets/detail/index.tsx` 改用之（parity）；`api.ts`+`typings.d.ts` 加 `listDataTasks/pauseJob/resumeJob` 与 `paused`；`jobState.tsx` 加 `paused` 标签。

`paused` 为自由字符串列，无需 alembic 改列。

## §2 数据模型与状态机

`Job.state`：`pending | running | paused | success | failed | cancelled`。

转移：
- `pause`：`pending|running → paused`（running 时 `engine.terminate_job` 杀子进程）
- `resume`：`paused → pending`，`job_runner.spawn` 按 `spec` 重跑
- `stop`：`pending|running|paused → cancelled`（paused 停止仅改状态）
- `delete`：允许 `paused`；running 仍需先停止
- `reconcile_orphans`：paused 存活；pending/running → failed（不变）
- `_run_job` 取信号量后查 state，若已 paused 则放弃（处理"排队中暂停"）
- `JobRead` 加计算位：`canPause`(pending/running)、`canResume`(paused)、`canStop`(pending/running/paused)

## §3 后端 API

- 新增 `GET /api/v1/data-tasks`：query `type?`（逗号多选/缺省全）、`state?`、`keyword?`(name ilike)、`current`、`pageSize`、`sortBy`、`sortOrder`；返回 `PageResult<JobRead>`，input/output 复用共享 helper。
- `POST /jobs/{id}/pause`：守卫→杀进程(若 running)→置 paused→JobRead；不可暂停 409。
- `POST /jobs/{id}/resume`：守卫 state==paused→spawn from spec→JobRead；非 paused 409。
- 既有 `stop`/`delete` 守卫放宽（§2）。
- 评估异步化：`POST /quality/jobs`、`POST /content-safety/jobs` 改为建 `Job(pending,spec)` + spawn 立即返回；`_run_job` 分发表补 `quality→run_quality_job`、`review→run_review`；run_* 改自开会话，语义不变。

## §4 前端

统一控制台 `data-tasks/index.tsx`：ProTable + 类型/状态多选 + 关键词；列=任务名·类型·输入版本·产出版本·状态·进度·时间·操作；操作列按 can* 驱动（暂停/继续/停止/查看报告/重新运行/删除）；resume 前确认；polling=3000；详情抽屉含输入/产出两个 `DatasetVersionPreview`（评估类隐藏产出）；兑现 `?highlight=`。

抽离 `DatasetVersionPreview.tsx`：props `{versions, semanticType?, admin?}`；封装版本列表+卡片(安全门 Tag/admin 动作)+按文件 List+预览 Modal（结构化→`previewDatasetVersion` 表；否则 presigned→kkFileView iframe）。`datasets/detail` 改用之。

## §5 边界

dj-process 无原生暂停→继续=从头重跑（UI 确认）；排队中暂停（_run_job 信号量后复查 state）；评估异步回归（前端新建后改刷新列表）；run_* 自开会话；pause/finish 事务内查 state 避竞态；kkFileView base URL 硬编码保持现状仅 flag。

## §6 测试

后端：状态机转移、reconcile_orphans(paused 存活)、统一列表过滤/分页、评估立即返回 pending、resume 保 spec。
前端：DatasetVersionPreview 抽离 parity、控制台各状态按钮、polling、highlight、评估隐藏产出面板。
