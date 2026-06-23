# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 仓库关系（最重要）

本工作区包含两个**相互独立的 Git 仓库**，git 操作必须分别在各自目录执行：

| 仓库 | 路径 | 远端 | 说明 |
|------|------|------|------|
| ai-data-platform | `.`（本仓库） | `scstc/ai-data-platform` | 主项目：文档、dashboard 静态产物、frontend 前端、工作区脚手架 |
| data-juicer | `data-juicer/` | `scstc/data-juicer` | fork 自阿里 `datajuicer/data-juicer`，已被本仓库 `.gitignore` 排除 |

> `frontend/`（Ant Design Pro v6 前端）与 `backend/`（FastAPI+PostgreSQL 后端）都是本仓库的普通目录（非独立仓库）。前端内部约定见 `frontend/CLAUDE.md`；后端实现前端 `src/services/data-platform` 的接口契约，前端 `MOCK=none` 启动即切真实后端（代理见 `frontend/config/proxy.ts`）。

两仓库均为 `main` / `dev` / `prod` 三分支模型，日常开发都在 `dev`。

> ⚠️ data-juicer 的 `main` 是**阿里上游镜像**（只做 fast-forward 同步，绝不直接提交），本地稳定版在 `prod`。开发提交一律走 `dev`。

## 优先用知识图谱了解代码

了解 data-juicer 代码结构时，**优先查阅已生成的知识图谱**，而不是直接大范围读源码：

- 数据位置：`data-juicer/.understand-anything/knowledge-graph.json`（2,749 节点 / 6,551 边，覆盖 1,204 个文件，含 layers 分层与 tour 导览）
- 节点类型：file / class / function / service / pipeline / config / document，每个节点带 `summary` 摘要
- 交互问答：用 `understand-anything:understand-chat` skill 基于图谱提问
- 可视化：运行 `/adp-dashboard` 启动本地静态 dashboard（`http://127.0.0.1:8765/`）
- 图谱对应 `meta.json` 里记录的 commit；代码大幅更新后需在 `data-juicer/` 内重跑 `/understand` 刷新

具体某个文件/函数的精确实现仍以源码为准，图谱用于快速定位和把握架构。

## 常用命令（项目 slash commands）

| 命令 | 用途 |
|------|------|
| `/adp-init` | 首次初始化工作区：clone data-juicer（dev 分支）到 `data-juicer/` |
| `/adp-dashboard` | 同步最新图谱数据到 `dashboard/` 并后台启动 `python3 -m http.server 8765` |
| `/adp-start` | **一键本地启动数据平台**（后端 :18003 + 前端 :8001 真实后端）——日常"把项目跑起来"首选 |
| `/adp-web` | 启动数据平台前端（Ant Design Pro v6，`:8001`，登录 admin/ant.design） |
| `/adp-server` | 启动数据平台后端（FastAPI+PG，`:18003`，Swagger 在 `/docs`；PG 容器 `:55433`） |
| `/dj-demo` | 安装 data-juicer 环境（`uv sync --python 3.11`）并跑通最简 CLI 示例 |
| `/dj-web` | 启动 data-juicer Web UI（streamlit，`:8501`） |
| `/dj-api` | 启动 data-juicer HTTP API（FastAPI/uvicorn，`:8000`，Swagger 在 `/docs`） |

dashboard 是纯静态产物（demo 模式，无访问控制），随仓库提交。重建产物的流程见 `.claude/commands/adp-dashboard.md`。

## 本地运行（速查）

日常本地跑数据平台用 `/adp-start`（起后端 + 前端，前端连真实后端）。关键点：

| 项 | 值 |
|---|---|
| 前端 | http://127.0.0.1:8001/ （Ant Design Pro，登录 `admin / ant.design`） |
| 后端 | http://127.0.0.1:18003/docs （FastAPI Swagger） |
| 数据库 | 远程 PG `10.60.1.119:55433`（经 `backend/.env` 的 `DATABASE_URL`，无需本地 docker） |
| 前端命令 | `cd frontend && PORT=8001 MOCK=none npm run dev`（`PORT=8001` 避开 dj-api 的 8000；`MOCK=none` 走真实后端） |
| 后端命令 | `cd backend && ./.venv/Scripts/uvicorn.exe app.main:app --port 18003`（**勿用 `uv run uvicorn`，本机 `uv` exit 127**；无 `--reload`，改 `.py` 手动重启） |
| 停止 | kill 后台任务；或按端口清进程（会话退出可能留 node/uvicorn 残留致端口占用） |

> `/adp-server` 命令文件里的 `uv run uvicorn` 与本地 docker PG 是默认值；本机实际用 venv `uvicorn.exe` + 远程 PG（见上表与 `backend/.env`）。

## GitHub Pages 发布

`dashboard/`（understand-anything 知识图谱 dashboard）通过 `.github/workflows/understand-anything-pages.yml` 自动发布到 GitHub Pages（`https://scstc.github.io/ai-data-platform/`）：

- 触发条件：push 到 `dev` 且改动涉及 `dashboard/**`（也支持手动 workflow_dispatch）
- CI **不做构建**，直接把 `dashboard/` 目录作为 artifact 部署——构建在本地完成后提交
- Pages 部署在 `/ai-data-platform/` 子路径下，因此静态产物有两个硬性要求（改 dashboard 构建时勿破坏）：
  1. Vite 构建必须用**相对路径** base（否则资源 404）
  2. demo 模式加载图谱数据（knowledge-graph.json 等）必须用**相对 URL**
- workflow 用的 actions 版本（checkout@v6 / configure-pages@v6 / upload-pages-artifact@v5 / deploy-pages@v5）均原生 node24，升级时别降回 node20 的旧版

## data-juicer 架构速览

面向 LLM 的多模态数据处理系统，核心是**算子（Operator）体系**——100+ 可组合算子，按 YAML 配置编排成流水线：

- **Formatter** 格式转换 / **Mapper** 数据编辑 / **Filter** 规则过滤 / **Deduplicator** 去重（MinHash、SimHash）/ **Selector** 数据选择
- 算子代码在 `data-juicer/data_juicer/ops/` 下按类型分目录
- 支持 Ray 分布式、数据沙盒（Sandbox）、HuggingFace 数据集
- 详细介绍见本仓库 `README.md` 与 `data-juicer/README_ZH.md`

data-juicer 内部的构建/测试遵循其自身仓库的约定（pyproject.toml + tests/），在 `data-juicer/` 目录内操作。

### 启动方式（5 类）

1. **CLI**（pip 安装后的 entry points，见 `pyproject.toml [project.scripts]`）：
   - `dj-process --config xx.yaml` — 数据处理主入口（`tools/process_data.py`），示例配置 `demos/process_simple/process.yaml`
   - `dj-analyze` — 数据集质量分析 / `dj-install` — 按配置装算子依赖 / `dj-mcp` — MCP server
2. **HTTP API**：`uvicorn service:app`（FastAPI，自动把 data_juicer 公开方法注册成接口，见 `docs/DJ_service.md`）
3. **Web UI**：`streamlit run app.py`（可视化过滤/分析；`demos/` 下另有多个独立 streamlit demo）
4. **Ray 分布式**：入口仍是 `dj-process`，配置里 executor 切 `ray`；集群拓扑参考 `.github/workflows/docker/docker-compose.yml`
5. **Docker**：官方镜像 `datajuicer/data-juicer`（`Dockerfile`；具身智能版 `Dockerfile.embodied`），容器内跑上述任意方式

> Sandbox（数据沙盒）已拆分到独立仓库 `datajuicer/data-juicer-sandbox`，不是本仓库的启动方式。

## 文档约定

新增文档放 `docs/` 并在 `docs/README.md` 的索引表中登记。
