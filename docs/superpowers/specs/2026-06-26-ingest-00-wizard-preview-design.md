# 采集配置向导 + 源数据预览 + 字段选择（切片 A）

- 日期：2026-06-26
- 模块：数据接入 → 采集任务（`frontend/src/pages/ingest/tasks`、`backend/app/api/v1/ingest_tasks.py`）
- 状态：设计已确认，待写实现计划

## 背景与目标

当前"新建采集任务"是单个 `ModalForm`：任务名/分类 → 数据源 → 调度（Cron 已禁用，仅单次）→ 采集对象
（库:整表多选/SQL + 可选过滤算子；S3/HDFS:路径+Glob）。**没有数据预览、没有字段级选择、没有试跑采样**，
用户配完直接运行才知道采到什么。

本切片把"建采集任务"升级为**多步向导**，中间插入**源数据采样预览**，并在数据库整表模式支持**字段勾选**。
属于"丰富采集流程"三方向中的第一片（A），先落地；运行可观测与治理（B）、调度与增量（C）各自后续独立成 spec。

### 成功标准

1. 新建采集任务走四步向导，第 3 步能看到源数据样本行 + 列（名 + 推断类型）。
2. 数据库整表模式可勾选列，落地（运行 / 生成数据集）只采选中列。
3. 预览为只读、无副作用；失败时诚实报错并允许重试或跳过。
4. 编辑采集任务仍走原有轻量弹窗，行为不变。
5. 后端 `preview` 接口与 `_build_queries` 列裁剪有测试覆盖意图。

### 非目标（留给 B / C 或后续迭代）

- 不做 Cron 调度、增量采集（水位线）——切片 C。
- 不做采集质量校验 / schema 漂移 / 运行记录强化——切片 B。
- 文件（S3/HDFS）类型**不做字段裁剪**，本切片只读预览。
- 不改"运行(rerun) vs 生成数据集"两个按钮的去留；字段选择会让两者产出一致，按钮取舍并入 B 或单独处理。

## 设计

### ① 向导结构（前端）

`frontend/src/pages/ingest/tasks/index.tsx` 的"新建任务"由 `ModalForm` 改为 **`StepsForm`**（ProComponents），
四步。**编辑**采集任务仍用现有 `ModalForm`（`editRow`），不动。

| 步 | 内容 | 进入下一步的校验 |
|---|---|---|
| 1 基本信息 | 任务名、分类（treeSelect）、数据源（select） | 必填项 |
| 2 采集对象 | 按数据源类型条件渲染（沿用现有 `taskFormFields` 逻辑）：库=整表多选/SQL；S3/HDFS=路径列表/Glob | 库:至少 1 表或非空 SQL；文件:至少 1 路径或 Glob |
| 3 预览与字段 | 进入即调采样接口 → ProTable/Table 展示样本行 + 列（名+推断类型）。**数据库整表模式**：列带 checkbox 勾选要采的列（默认全选）；**SQL / 文件模式**：只读预览 | 预览成功即可；失败可"重试"或"跳过" |
| 4 落地确认 | 过滤算子（库，沿用 `FilterOperatorPicker`）+ 调度（单次）+ 配置概要 → 提交 `createIngestTask` | — |

- 样本行数 N=50（前端不暴露，后端常量 `PREVIEW_SAMPLE_ROWS`）。
- 字段勾选状态收集进表单字段 `extract.columns: string[]`，随 `createIngestTask` 一并提交。
- 预览步组件建议拆为独立文件 `tasks/components/SourcePreview.tsx`，输入 `{ datasourceId, extract }`，
  内部调接口 + 渲染 + 勾列，向外回传选中列。保持 `index.tsx` 不继续膨胀。

### ② 采样接口（后端，新增）

`POST /api/v1/ingest-tasks/preview`，**无副作用**（不建任务、不落地、不建 job/version）。

请求体（复用任务创建的形状子集）：

```jsonc
{
  "datasourceId": "ds_xxx",
  "extract": { "mode": "table", "tables": ["public.users"] }
  //        | { "mode": "sql", "sql": "SELECT ..." }
  //        | { "mode": "path", "paths": ["raw/a.jsonl"], "glob": "raw/**/*.jsonl" }
}
```

响应：

```jsonc
{
  "data": {
    "columns": [{ "name": "id", "type": "int8" }, { "name": "name", "type": "text" }],
    "rows": [{ "id": 1, "name": "a" }, ...],   // ≤ 50
    "truncated": true,                          // 源数据多于样本行数
    "sampledFrom": "public.users"               // 多表/多文件时实际取样对象
  },
  "success": true
}
```

实现：

- **数据库**（`type=database`，`db_kind ∈ PG 系 / GoldenDB / MySQL`）：
  复用 `pg._connect` / `mysql._connect` 建连；用 `_build_queries(extract)` 得到查询，对每条包一层
  `SELECT * FROM (<query>) AS _preview LIMIT 50`。多表/多查询时**只取第 1 条**预览，`sampledFrom` 标注。
  列类型由 asyncpg/驱动的字段类型推断（取游标列描述；MySQL 同理）。
- **文件**（`type=s3` / `type=hdfs`）：
  复用 objectstore/hdfs 的 list 逻辑匹配 `paths`/`glob` → 取**首个**文件 → 解析前 50 行，
  `columns` = 样本行 key 的并集（保持出现顺序）。按格式区分取样方式：
  - **jsonl / csv**（按行）：流式只读头部（≤ `MAX_MATERIALIZE_BYTES` 护栏），读够 50 行即停，`truncated=true`。
  - **parquet**（需 footer，无法读字节前缀）：文件 ≤ `MAX_MATERIALIZE_BYTES` 时整文件下载后用 DuckDB
    `read_parquet ... LIMIT 50`（与数据集版本预览同套）；超护栏则诚实返回"文件过大暂不支持预览"，允许跳过。
- **诚实失败**：无数据源 / 不支持类型 / 未配采集对象 / 未装驱动 / 连接失败 → 4xx + message，
  沿用现有 `ConnectorNotReady` / `IngestError` 文案风格。前端预览步据此提示并允许重试或跳过。

接口放在 `ingest_tasks.py`（与 rerun / generate-dataset 同文件），采样逻辑下沉到连接器或一个
`preview` 辅助函数，避免在路由里堆格式分支。

### ③ 字段选择落地语义

仅**数据库整表模式**生效。勾选列存进 `extract.columns: string[]`：

- 空数组或未提供 = 不裁列，等价 `SELECT *`（保持现状，向后兼容旧任务）。
- 落地链路单点改动：`backend/app/services/connectors/base.py` 的 `_build_queries`，整表模式由
  `SELECT * FROM <table>` 改为 `SELECT <选中列> FROM <table>`（列名做标识符引用，无选中则维持 `*`）。
- 该改动同时被 `运行`(rerun → `run_pg_ingest`) 与 `生成数据集`(generate-dataset → `_fetch_db_records`) 复用，
  两条路径产出自动一致，无需各改一处。
- SQL 模式 / 文件模式不写 `extract.columns`，行为不变。

## 数据流

```
[向导 step1-2] 填 datasourceId + extract
      │
      ▼  step3 进入即调
POST /ingest-tasks/preview ──► _connect/list → LIMIT/head 取样 → {columns, rows}
      │（只读，无落地）
      ▼  整表模式勾列
extract.columns = 选中列
      │
      ▼  step4 提交
POST /ingest-tasks (createIngestTask) ──► 持久化 task.extract（含 columns）
      │
      ▼  后续 运行 / 生成数据集
_build_queries(extract) ──► SELECT <columns> FROM table LIMIT? → land_records / parquet
```

## 错误处理

| 场景 | 行为 |
|---|---|
| 数据源不存在 / 不支持预览的类型 | preview 返回 4xx + message；向导预览步显示错误 + "重试 / 跳过" |
| 未配采集对象就进入 step3 | step2 校验拦截；理论不可达，preview 兜底返回 400 |
| 数据库连接 / 查询失败 | 4xx + `连接失败:...` / `采集失败:...`；允许重试或跳过 |
| 文件不存在 / 格式无法解析 | 4xx + message；允许跳过（仍可建任务，运行时再暴露） |
| jsonl/csv 超 `MAX_MATERIALIZE_BYTES` | 流式只读头部够 50 行即停，`truncated=true` |
| parquet 超 `MAX_MATERIALIZE_BYTES` | 4xx "文件过大暂不支持预览"；允许跳过 |

## 测试（验证 intent，非仅行为）

后端（`backend/tests/`）：

- `preview` 对 PG 整表 / SQL 返回正确样本行与列；多表只取第 1 张并标 `sampledFrom`。
- `preview` 对缺采集对象 / 不支持类型 / 连接失败分别返回诚实 4xx（不伪造成功）。
- `_build_queries`：给定 `extract.columns=[a,b]` 整表模式生成 `SELECT a, b FROM ...`；
  空/缺省时维持 `SELECT *` —— **编码"勾列即只采选中列、未勾即全采"这一业务意图**。

前端（`tasks/index.test.tsx` 及预览组件测试）：

- 向导四步流转与每步校验。
- step3 渲染样本行 + 列；整表模式勾列写入 `extract.columns`，SQL/文件模式无勾选 UI。
- 预览失败可重试 / 跳过仍能完成建任务。
- 编辑入口仍走原 `ModalForm`，不触发向导。

## 影响面（改动清单）

- 前端：`tasks/index.tsx`（新建改 StepsForm，编辑不动）、新增 `tasks/components/SourcePreview.tsx`、
  `services/data-platform/api.ts`（新增 `previewIngestSource`）、类型 `typings`（`extract.columns`、preview 形状）。
- 后端：`api/v1/ingest_tasks.py`（新增 `preview` 路由）、连接器内采样辅助、
  `services/connectors/base.py`（`_build_queries` 支持 `columns`）、schemas（preview 请求/响应、`extract.columns`）。
- 不动：编辑弹窗、rerun / generate-dataset 路由主体（仅经 `_build_queries` 间接受益）。
