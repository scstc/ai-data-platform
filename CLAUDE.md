# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 仓库关系（最重要）

工作区根目录同时挂**两个互相独立的 Git 仓库**，git 操作必须分别在各自目录执行：

| 仓库 | 路径 | 远端 | 角色 |
|------|------|------|------|
| `ai-data-platform` | `.` | `scstc/ai-data-platform` | 平台源码、文档、knowledge-graph 静态 dashboard |
| `data-juicer` | `data-juicer/` | `scstc/data-juicer` | fork 自 `datajuicer/data-juicer`，构建期编进后端镜像 |

> `data-juicer/` 已被 `.gitignore` 排除（**不是本仓库子模块**）。`frontend/` 与 `backend/` 都是本仓库的普通目录（非独立仓库）。前端契约见 `frontend/src/services/data-platform/api.ts`；后端实现这些接口（`backend/app/api/v1/`），启动 `MOCK=none npm run dev` 即对接真实后端（代理见 `frontend/config/proxy.ts`）。

两仓库均使用 `main / dev / prod` 三分支，日常开发走 `dev`：

- **本仓库**：`main` 稳定 / `dev` 开发 / `prod` 生产
- **data-juicer**：`main` 是**阿里上游镜像**（仅 fast-forward 同步），本地稳定版在 `prod`

> data-juicer 的 `main` 禁止直接提交；上游同步走 fast-forward。

### 项目内文件型指令源

- `frontend/CLAUDE.md` + `frontend/AGENTS.md` — 前端细则（Umi Max v4 / antd v6 / ProComponents v3 / Biome / utoopack / Conventional Commits / Node ≥ 22）
- `.claude/commands/*.md` — `/adp-*` / `/dj-*` skill 的执行步骤（直接读对应文件即可照跑）
- `.claude/settings.json` — 注册了 `understand-anything` plugin
- `docs/README.md` — 业务/规划/研究文档索引

## 优先用知识图谱了解代码

data-juicer 子仓库代码结构量大，**先查 `data-juicer/.understand-anything/knowledge-graph.json`**（约 2,749 节点 / 6,551 边，覆盖 1,204 文件）而不是直接读源码：

- 节点类型：file / class / function / service / pipeline / config / document，每个节点带 `summary`
- 问答：用 `understand-anything:understand-chat` skill 基于图谱提问
- 可视化：`/adp-dashboard` 启 `dashboard/`（静态站点，python3 `http.server` :8765）
- 图谱对应的 commit 记录在 `meta.json`；代码大改后需在 `data-juicer/` 内重跑 `/understand` 刷新

具体实现仍以源码为准；图谱用于定位与把握架构。

## 架构速览（平台）

围绕「**数据源接入 → 数据集版本化 → 加工/质量/审核 → 发布**」闭环，分四块：

- **数据源**：S3/MinIO/OSS/OBS、HDFS、PG 系 / GoldenDB、API 推送 — `backend/app/services/connectors/`（`pg.py` / `mysql.py` / `hdfs.py` / `objectstore.py` / `push.py` / `proprietary.py` / `base.py`）
- **采集 / 数据集**：整表或自定义 SQL 拉取，落地前可跑 data-juicer 算子过滤；同任务再次执行**追加新版本**（`uploads/<dataset_id>/v<n>/data.jsonl`）— `backend/app/services/ingest_runner.py` / `landing.py`
- **文件管理**：平台内置 MinIO（`STORAGE_MINIO_*` 配置），后端启动时自建 `uploads` 桶 — `backend/app/services/external_store.py`
- **LLM 配置中心**：多供应商 + 用量统计 — `backend/app/services/llm_config.py` / `api/v1/llm_config.py`

后端 FastAPI 装配见 `backend/app/main.py`（lifespan 负责 LLM 缓存刷新、MinIO 桶、APScheduler 启停、`job_runner.reconcile_orphans`）。注意 `scheduler_enabled=False` 时跳过调度器，采集主流程**不依赖**调度器在线。

### data-juicer 子系统（fork 阿里 upstream）

100+ 算子分五类：**Formatter** 格式转换 / **Mapper** 数据编辑 / **Filter** 规则过滤 / **Deduplicator** 去重（MinHash、SimHash）/ **Selector** 数据选择。算子在 `data-juicer/data_juicer/ops/`，按类型分目录。支持 Ray 分布式、HuggingFace 数据集、data-juicer-sandbox（独立仓库，不在本仓库）。

5 种启动方式（详细见 `data-juicer/README_ZH.md` 与对应 `.claude/commands/dj-*.md`）：

1. **CLI**：`dj-process --config xx.yaml`（主入口）/ `dj-analyze` / `dj-install` / `dj-mcp`
2. **HTTP API**：`uvicorn service:app`（FastAPI 自动注册）— `/dj-api`
3. **Web UI**：`streamlit run app.py` — `/dj-web`
4. **Ray 分布式**：配置切 executor `ray`，拓扑 `.github/workflows/docker/docker-compose.yml`
5. **Docker**：`datajuicer/data-juicer` 官方镜像

## 项目 slash commands（核心工作流）

| 命令 | 用途 |
|------|------|
| `/adp-init` | 首次 clone data-juicer（`dev` 分支）到 `data-juicer/` |
| `/adp-dashboard` | 同步图谱数据到 `dashboard/` 并后台起静态站 `:8765` |
| `/adp-start` | **一键本地起平台**（后端 :18003 + 前端 :8001 真实后端） |
| `/adp-deploy` | **部署到 10.60.1.60**（`git archive` 同步 + `docker compose` 重建） |
| `/adp-web` | 起前端 :8001（Ant Design Pro v6，admin/ant.design） |
| `/adp-server` | 起后端 :18003（FastAPI+PG，Swagger 在 `/docs`） |
| `/dj-demo` | 装 DJ 环境 + 跑最简 CLI 示例 |
| `/dj-web` | 起 DJ Web UI（streamlit :8501） |
| `/dj-api` | 起 DJ HTTP API（FastAPI/uvicorn :8000） |

详细执行步骤直接读 `.claude/commands/<name>.md`。

## 本地运行（速查）

日常本地用 `/adp-start`（后端 + 前端连真实后端）。关键约束：

| 项 | 值 |
|---|---|
| 前端 | http://127.0.0.1:8001/ — `admin / ant.design` |
| 后端 | http://127.0.0.1:18003/docs（Swagger） |
| 数据库 | **远程 PG** `10.60.1.60:55433`（`backend/.env` 的 `DATABASE_URL`，无本地 docker） |
| 起后端 | `cd backend && ./.venv/Scripts/uvicorn.exe app.main:app --port 18003` |
| 起前端 | `cd frontend && PORT=8001 MOCK=none npm run dev` |
| 端口 | 前端必须 `PORT=8001`（避开 dj-api :8000）；`MOCK=none` 才走真实后端 |
| uv 坑 | **不要** `uv run uvicorn`（本机 `uv` exit 127）；无 `--reload`，改 `.py` 手动重启 |

> `/adp-server` 命令文件里的 `uv run uvicorn` 是默认值；本机实际用 venv `uvicorn.exe` + 远程 PG。

## 生产部署（速查）

`/adp-deploy` 把本地 `origin/dev` HEAD 同步到 `10.60.1.60:/opt/ai-data-platform`。关键约束（详见 `.claude/commands/adp-deploy.md`）：

- **坑 1**：Windows `core.autocrlf=true` 会把 `git archive` 输出变 CRLF → 容器 `entrypoint.sh` shebang 变 `#!/bin/sh\r` → crash-loop。**必须 `-c core.autocrlf=false`**
- **坑 2**：`.60` 上非交互 ssh 用 dash，`deploy.sh` 的 `set -o pipefail` 会报 `pipefail: invalid option` → 显式 `/usr/bin/bash` 或直接 `docker compose`
- DJ 编译慢（5–15 min），后台起别等 SSH
- **Docker Hub 封禁**：基础镜像需提前缓存到本地（`docker pull --platform linux/amd64` → `save` → `scp` → `load`），不拉新基础镜像
- 数据卷保留：`adp_pg_data`（PG）、`adp_data`（`/data/uploads` + `/data/datasets`）
- DB 迁移：`backend-entrypoint.sh` 自动 `alembic upgrade head`，**部署前确认新迁移可回退**
- `data-juicer/` 不随平台 git archive 走 → 若 DJ 也升级，需单独 rsync

### 编排 + 镜像结构（`deploy/docker-compose.yml` + `deploy/backend.Dockerfile`）

```
nginx(frontend:80)  →  /api → backend:18003  →  postgres:16(:55433→5432)
                                                  ↑
                          data-juicer 子进程  ←────┘
                          (后端镜像内置 /opt/dj/.venv/bin/dj-process)
```

- 后端 + DJ 编进**同一镜像**，共享文件系统 `/data`（卷 `adp_data`），DJ 写出 `/data/datasets` 由后端读回
- DJ 为非 editable 安装 → 构建时编译 MinHash 等 C++/Cython 扩展
- 额外 `kkfileview` 服务（`:8012`）做非结构化文件预览

部署配置 `deploy/.env`：`WEB_PORT / PG_* / CORS_ORIGINS / AUTH_SECRET / STORAGE_MINIO_* / OPENAI_*`。**生产必改**：`PG_PASSWORD`、`AUTH_SECRET`（`openssl rand -hex 32`）。

## 开发约定

### 后端（Python 3.12 + uv）

| 任务 | 命令 |
|---|---|
| 装依赖 | `cd backend && uv sync --python 3.12` |
| 跑测试 | `cd backend && uv run pytest`（单测 `tests/` + 集成 `tests/test_*.py`，async 自动） |
| 单测示例 | `uv run pytest tests/test_files.py -k "upload"` |
| Lint | `cd backend && uv run ruff check .`（`E,F,I,UP,B`，line-length 88） |
| 迁移 | `cd backend && uv run alembic upgrade head`（容器入口自动跑） |
| 新增迁移 | `uv run alembic revision --autogenerate -m "..."` |

### 前端（Umi Max v4 + antd v6 + Biome）

详细见 `frontend/CLAUDE.md`。常用：

| 任务 | 命令 |
|---|---|
| 开发（连真实后端） | `cd frontend && PORT=8001 MOCK=none npm run dev` |
| 开发（mock） | `npm run start` |
| 生产构建 | `npm run build`（max build / utoopack） |
| 类型检查 | `npm run tsc` |
| Lint | `npm run lint`（Biome + tsc 都要过） |
| 改 OpenAPI 契约 | 改后端 → `npm run openapi` 重生 `src/services/ant-design-pro/`（**勿手改**） |

### 提交规范

- Conventional Commits（commitlint 强制）
- 后端 ruff / 前端 biome + tsc 都得过
- **勿手改** `frontend/src/services/ant-design-pro/`

## 数据库操作（统一 dbx）

**所有 DB 操作走 DBX MCP**（`dbx_*` 工具集），不直接 `psql`。连接信息：

| 项 | 值 |
|---|---|
| 连接名 | `PostgreSQL_adp` |
| 库 | `adp` @ `10.60.1.60:55433`（来源：`backend/.env` 的 `DATABASE_URL`） |

常用工具：`dbx_list_connections` / `dbx_list_tables` / `dbx_describe_table` / `dbx_execute_query`（默认只读）/ `dbx_get_schema_context`。**写操作无拦截**（`DBX_MCP_ALLOW_DANGEROUS_SQL=1`），改库前先确认影响范围（核心表 `audit_logs` / `datasets` / `dataset_versions`）。

## 端到端冒烟脚本

后端关键路径（上传 / 落地 / 数据集 / RBAC 等）单测覆盖在 `backend/tests/`，命名 `test_<module>.py` + `tests/unit/` 子目录。改完 `landing.py` / 路由 / 权限后跑：

```bash
cd backend && uv run pytest tests/test_files.py tests/test_datasets_preview.py \
  tests/test_uploads.py tests/test_dataset_acl.py tests/test_rbac.py -q
```

新增端到端场景：直接 `await upload_batch_as_dataset(...)` 风格写脚本（参考 `tests/test_uploads.py` 与 `tests/test_landing_parquet.py` 的 fixture 模式），不要绕开 lifespan 直接 import 路由。

## GitHub Pages

`dashboard/`（understand-anything 知识图谱 dashboard）由 `.github/workflows/understand-anything-pages.yml` 在 push 到 `dev` 且改 `dashboard/**` 时自动发布到 `https://scstc.github.io/ai-data-platform/`。

- **CI 不构建**，直接把 `dashboard/` 作为 artifact 部署
- `/ai-data-platform/` 子路径下静态产物需满足：Vite 用相对 base、demo 模式用相对 URL 加载 `knowledge-graph.json`
- actions 版本原生 node24（`checkout@v6` / `configure-pages@v6` / `upload-pages-artifact@v5` / `deploy-pages@v5`），**勿降回 node20 旧版**

## 文档约定

新增文档放 `docs/` 并在 `docs/README.md` 索引表登记。敏感凭据文件（SSH/DB 密码等）已被 `.gitignore` 排除 `docs/数据库安装信息汇总*.md`。

## 容器内调试常见路径

```bash
# 容器内直接 import 后端模块（注意 backend-entrypoint.sh 设了 VIRTUAL_ENV=/app/.venv）
docker exec adp-backend python -c "from app.services.landing import _geojson_to_records; print(...)"

# 查 alembic 当前版本
docker exec adp-backend /app/.venv/bin/alembic current

# 用 backend .env 远程跑一次性脚本
docker run --rm --network host -v $PWD/backend:/app adp-backend:latest \
  /app/.venv/bin/python /app/scripts/xxx.py
```