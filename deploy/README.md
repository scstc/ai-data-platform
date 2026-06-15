# 部署(Docker Compose)

整套数据平台容器化部署:**PostgreSQL + 后端(FastAPI,内置 data-juicer 引擎)+ 前端(nginx)**。

## 架构

```
                         ┌─────────────────────────────────────┐
  浏览器 ──:80──▶  frontend (nginx)                            │
                         │   /        → SPA 静态产物            │
                         │   /api/    → 反代 backend:18003      │
                         └───────────────┬─────────────────────┘
                                         │
                              backend (FastAPI :18003)
                              ├─ /app/.venv     应用(py3.12)
                              └─ /opt/dj/.venv  data-juicer 引擎
                                 (子进程 dj-process 跑算子流水线)
                                         │
                              postgres:16  (卷 adp_pg_data)
   共享卷 adp_data:/data  ← 上传文件 + 受管数据集产物(backend 与 DJ 同读写)
```

- 后端与 DJ 引擎打进**同一镜像、共享文件系统**:后端以子进程方式调 `dj-process`,
  并读取其写到 `/data/datasets` 的产物,故二者必须同机同盘。
- DJ 为**非 editable 安装**,构建时编译 C++/Cython 去重扩展(MinHash 等)。

## 一键部署

需要目标机:Docker + compose,且仓库根目录下存在 `data-juicer/` 子仓库。

```bash
# 仓库根目录
cp deploy/.env.example deploy/.env   # 按需改 PG 密码 / WEB_PORT / LLM
bash deploy/deploy.sh
```

`deploy.sh` 等价于:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

启动后:

- Web UI:`http://<host>:<WEB_PORT>`(默认 80),登录 `admin / ant.design`
- 后端迁移:容器入口 `backend-entrypoint.sh` 自动 `alembic upgrade head`

## 配置项(deploy/.env)

| 变量 | 默认 | 说明 |
|------|------|------|
| `PG_USER/PG_PASSWORD/PG_DB` | adp / adp_dev_pw / adp | PostgreSQL 凭据(**生产改密码**) |
| `WEB_PORT` | 80 | 对外 Web 端口 |
| `ENGINE_NP` | 2 | 单 job 内 data-juicer 并行度 |
| `ENGINE_CONCURRENCY` | 3 | 多 job 并发上限 |
| `CORS_ORIGINS` | `["http://10.60.1.60"]` | 跨域来源(同源部署可不改) |
| `OPENAI_*` | 空 | 可选 LLM;留空则 AI 接口走启发式 |

## 常用运维

```bash
C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
$C ps                 # 状态
$C logs -f backend    # 后端日志
$C up -d --build      # 改动后重建
$C down               # 停止(数据卷保留)
$C down -v            # 停止并清空数据(危险)
```

## 数据持久化

- `adp_pg_data` — PostgreSQL 数据
- `adp_data` — 上传文件(`/data/uploads`)+ 受管数据集产物(`/data/datasets`)

二者均为命名卷,`down` 不删、`down -v` 才清。
