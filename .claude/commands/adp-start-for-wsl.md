---
description: 在 WSL 中一键启动本地全栈 Docker（adp-local：PG + MinIO + 后端 + 前端 + kkFileView）
---

# /adp-start-for-wsl — WSL 本地全栈启动

在 WSL Ubuntu 里把**纯本地 Docker 栈**跑起来：PostgreSQL / MinIO / 后端(含 DJ 引擎) /
前端(nginx) / kkFileView 全部本地新建容器，不连任何远程资源。

与 `/adp-start`（Windows 裸进程 + 远程 PG）是**两套完全独立的环境**，用途不同：
前者改 `.py` 即时生效适合开发调试，本命令跑的是容器化完整栈，适合验证部署形态。

| 项 | 值 |
|---|---|
| 仓库路径 | `~/ai-project/ai-data-platform`（WSL 内） |
| compose | `deploy/docker-compose.local.yml` + `deploy/.env.local` |
| project | `adp-local`（容器名 `adp-local-*`） |
| 数据卷 | `adp-local_adp_local_data` / `_pg_data` / `_minio_data` |

前置：WSL 里 docker 可用；`deploy/.env.local` 存在（没有就从 `.env.local.example` 复制）。

## 执行步骤

所有命令都通过 `wsl -d Ubuntu-26.04 -- bash -c "..."` 执行；或先 `ssh change@127.0.0.1`。

### 第一步：端口占用检查

`deploy/.env.local` 的端口已整体挪开，**与离线部署栈（project `adp`）不再冲突，两栈可同时运行**：

| 服务 | adp-local（本命令） | 离线部署栈（对照，勿改） |
|---|---|---|
| Web 前端 | **8091** | 8090 |
| 后端直连 | **18005** | 18004 |
| PostgreSQL | **25433** | 25432 |
| MinIO API / 控制台 | **9010 / 9011** | 9000 / 9001 |
| kkFileView | **8013** | 8012 |

起之前确认这几个端口空着（镜像网络下 Windows 侧程序也会占）：

```bash
for p in 8091 18005 25433 9010 9011 8013; do
  ss -ltn | grep -q ":$p " && echo "  $p 已占用" || echo "  $p 空闲"
done
```

> 改端口时 **`CORS_ORIGINS` 必须跟着 `WEB_PORT` 改**（现为 `["http://localhost:8091"]`），
> 否则后端会拒绝前端来源。

### 第二步：生成 WSL 专用 compose

仓库里的 `docker-compose.local.yml` 是给**有 GPU 的 Docker Desktop** 写的，WSL 无 CUDA
直通时必须改两处。**不要手改被跟踪的文件**（checkout 就丢，CLAUDE.local.md 记的老坑），
改为每次派生一份：

```bash
cd ~/ai-project/ai-data-platform
sed -e '/^    deploy:$/,/capabilities: \[gpu\]/d' \
    -e 's/^\( *\)DJ_EXTRAS: "\[generic\]"$/\1DJ_EXTRAS: ""/' \
    deploy/docker-compose.local.yml > deploy/docker-compose.local.wsl.yml
```

两处改动的原因：

- **剥掉 GPU `deploy.resources.reservations.devices` 段** —— 没有 nvidia runtime 时
  `up` 会因找不到 GPU 设备直接拒绝启动；`GPU_COUNT=0` 不管用，必须整段删。
  compose override 文件也删不掉（实测 `devices: []` 合并后 nvidia 条目仍在），
  只能生成时剔除。
- **`DJ_EXTRAS` 置空** —— `[generic]` 会拉 vllm，而 vllm 钉死的 xformers 只有 CUDA 版，
  CPU 上必然解析失败。这个字段在 compose 里是写死的字面量、**不读 `.env`**，
  所以改 `.env.local` 的 `DJ_EXTRAS=` 没用。

> 生成文件放 `deploy/` 下是必须的：compose 的 `build.context: ..` 相对 compose 文件
> 所在目录解析，挪到 `/tmp` 会把构建上下文指到错误的根。该文件已进 `.gitignore`。

派生正确性自检（可选）：

```bash
grep -q 'driver: nvidia' deploy/docker-compose.local.wsl.yml && echo "❌ GPU 段没剔干净" || echo "✅"
docker compose -f deploy/docker-compose.local.wsl.yml --env-file deploy/.env.local -p adp-local config >/dev/null && echo "✅ config OK"
```

### 第三步：启动

```bash
cd ~/ai-project/ai-data-platform
docker compose -f deploy/docker-compose.local.wsl.yml --env-file deploy/.env.local \
  -p adp-local up -d --build
```

run_in_background 启动。**首次会构建后端镜像并编译 data-juicer 的 C++/Cython 去重扩展，
5–15 分钟**，别等 SSH；后续无改动走缓存很快。

### 第四步：探测就绪

```bash
until curl -sf http://127.0.0.1:18005/healthz >/dev/null; do sleep 2; done
until curl -sf -o /dev/null http://127.0.0.1:8091/; do sleep 2; done
docker exec adp-local-backend /app/.venv/bin/alembic current   # 应到 head
```

后端 entrypoint 会自动跑 `alembic upgrade head`，首次是全新空库。

### 第五步：首次启动补算子目录

全新空库只建表、`operators` 表是空的，不导则**算子市场/编排页面全空**：

```bash
docker exec adp-local-backend /app/.venv/bin/python /app/scripts/import_operators.py
```

**仅首次或明确要重置目录时执行**——该脚本清空重插，会删掉自定义算子与 visible 隐藏标记。

### 第六步：告知用户

```
前端：http://localhost:8091/        登录 admin / ant.design
后端：http://localhost:18005/docs（Swagger）
PostgreSQL：localhost:25433（adp 库，全新空库）
MinIO 控制台：http://localhost:9011（adpadmin / 见 .env.local）
kkFileView：http://localhost:8013
```

WSL 镜像网络模式下 Windows 与 WSL 共享 `localhost`，两边浏览器都能直接访问。
注意别和离线栈的 8090/18004 搞混——两套可以同时开着。

## 停止 / 运维

```bash
cd ~/ai-project/ai-data-platform
C="docker compose -f deploy/docker-compose.local.wsl.yml --env-file deploy/.env.local -p adp-local"
$C ps                # 状态
$C logs -f backend   # 后端日志（API/入队）
$C logs -f worker    # worker 日志（任务实际在这里执行，与生产同形态）
$C down              # 停止，数据卷保留
$C down -v           # 停止并清空全部数据（慎用）
```

## 已知坑

- **端口不能用 49152–65535**：WSL2 镜像网络下与 Windows 共享端口命名空间，
  该区间是 Windows 的 TCP 动态端口范围，会被临时出站连接随机抢占，表现为
  `address already in use` 但 `ss -ltn` / `Get-NetTCPConnection` 都查不到占用方
  （它是转瞬即逝的临时连接，不是长期 listener）。本栈端口全部选在该区间之外。
- **执行形态与生产一致**：backend + 独立 worker 双容器（`JOB_EXECUTION_MODE=worker`，
  API 只入队、`adp-local-worker` 认领 PG 队列执行）；置 `.env.local` 的
  `JOB_EXECUTION_MODE=inline` 可回退 backend 进程内执行（此时 worker 容器闲置）。
- **改 `.py` 不生效**：镜像里是拷贝进去的代码，非挂载。改后端代码要
  `$C up -d --build backend` 重建；要即时生效请改用 `/adp-start`。
