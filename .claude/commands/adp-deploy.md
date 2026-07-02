---
description: 部署最新版本到 10.60.1.60（git archive 同步源码 + docker compose 重建）——生产部署
---

# /adp-deploy — 部署最新版到 10.60.1.60

把本地最新提交（`origin/dev` 的 HEAD）部署到生产机 **10.60.1.60**（`/opt/ai-data-platform`）：
同步源码 → `docker compose up -d --build` 重建 backend(含 DJ 引擎)/frontend 镜像。

## 前置

- 本地是干净 git 仓库、HEAD 已 `git push`（部署的是已提交版本）。
- `ssh root@10.60.1.60` 免密可用。
- `.60` 上 `/opt/ai-data-platform` **不是 git 仓库**（拷贝部署）→ 用 `git archive` 同步，**别 git pull**。

## 执行步骤

### 第一步：同步源码（git archive → ssh 解压）

`git archive HEAD` 只含已跟踪的平台源码，自动排除 `data-juicer/`、`deploy/.env`、`node_modules`、
`.venv`、`var` 等本地产物——故**保留 .60 上的 `data-juicer/` 与 `deploy/.env`**（构建必需 / 含配置）。

> **坑(必读)**：Windows 上 `core.autocrlf=true` 会让 `git archive` 把文本文件转成 **CRLF** 输出，
> 同步到 .60 后 `backend-entrypoint.sh` 的 shebang 变 `#!/bin/sh\r`，容器报
> `exec /usr/local/bin/entrypoint.sh: no such file or directory` 而 crash-loop。
> **必须加 `-c core.autocrlf=false`** 强制 LF 输出。

```bash
cd <repo-root>
git -c core.autocrlf=false archive HEAD | ssh -o ConnectTimeout=10 root@10.60.1.60 \
  'cd /opt/ai-data-platform && tar x && echo SYNC_OK && \
   echo "data-juicer: $([ -d data-juicer ] && echo yes || echo NO)  deploy/.env: $([ -f deploy/.env ] && echo yes || echo NO)"'
```

确认 `SYNC_OK`、data-juicer / deploy/.env 均 yes，且 `deploy/backend-entrypoint.sh` 是 LF
（`file ...` 不含 "CRLF"；`sed -n 1p | cat -A` 行尾是 `$` 不是 `^M$`）。

### 第二步：重建容器（.60 后台，DJ 编译慢）

> **坑**：`deploy/deploy.sh` 的 `set -o pipefail` 在 .60 的 dash 下会报 `pipefail: invalid option`
> （非交互 ssh 可能用 dash）。**直接跑 compose 命令**，或显式 `/usr/bin/bash deploy/deploy.sh`。

构建含 DJ 的 C++/Cython 编译 + 前端 npm build，5–15 分钟。用 `nohup` 脱离 ssh 防超时：

```bash
ssh -o ConnectTimeout=10 root@10.60.1.60 \
  'cd /opt/ai-data-platform && nohup /usr/bin/bash -c \
   "docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build" \
   > /tmp/adp_deploy.log 2>&1 < /dev/null & disown; echo kicked-off'
```

旧容器在构建期间继续服务，仅切换瞬间短暂中断。

### 第三步：轮询构建完成（等进程退出）

不要 grep 日志里的 "error"（`liberror-perl` 等包名会误命中）。等**构建进程退出**再判结果：

```bash
# 后台跑，完成会通知
until ! ssh -o ConnectTimeout=8 root@10.60.1.60 'pgrep -f "deploy/docker-compose.yml" >/dev/null'; do
  sleep 20
done
ssh root@10.60.1.60 'tail -25 /tmp/adp_deploy.log; \
  echo "--- containers ---"; docker ps -a --format "{{.Names}}\t{{.Status}}" | grep adp-'
```

成功的标志：`Container adp-backend/frontend  Started` + 容器状态刷新为 `Up X seconds`。

### 第四步：验证 + 告知用户

```bash
ssh root@10.60.1.60 'docker ps --format "{{.Names}}\t{{.Status}}" | grep -E "adp-(backend|frontend|postgres)"'
curl -sf http://10.60.1.60/ -o /dev/null -w "web HTTP %{http_code}\n"
```

```
部署完成：http://10.60.1.60/  登录 admin / ant.design
镜像：adp-backend / adp-frontend（已重建）；PG/MinIO/kkFileView 不变
```

## 平行栈部署（不动线上 :80，另起一套）

给某个分支单独起一套栈做验证/演示（如治理整改 `feature/governance-remediation-plan` 连 `adp_gov` 库），
**不覆盖线上 `adp:80`**。要点：独立 compose project 名 + 独立容器名/镜像 tag + 换端口 + **复用**现有
postgres/MinIO/kkFileView（不重复起 infra 容器）。编排文件 `deploy/docker-compose.gov.yml`（project `adp-gov`）：
只含 backend + frontend，backend 经宿主机 `10.60.1.60:55433` 连现有 `adp-postgres` 的 `adp_gov` 库，
独立数据卷 `adp_gov_data`。

流程（与主流程同，差异在参数）：

1. **选端口先探真占用**：`ss -ltn` **只列本机 listener，漏掉 docker-proxy 发布的端口**。务必也查
   `docker ps --format "{{.Ports}}"`——曾踩 8082 被 `report-center-backend-java` 占用，compose 起容器时才报
   `Bind for :::8082 failed: port is already allocated`，前端 502/500。确认空闲后落到 `deploy/.env.gov` 的 `WEB_PORT`。
2. **`.env.gov` 在 .60 上生成**（不入库，`.gitignore` 已排除 `deploy/.env.gov`），从线上 `deploy/.env` 派生：
   ```bash
   ssh root@10.60.1.60 'cd /opt/ai-data-platform && \
     sed -e "s/^PG_DB=.*/PG_DB=adp_gov/" -e "s#10.60.1.60:80#10.60.1.60:8090#" deploy/.env > deploy/.env.gov && \
     printf "\nPG_HOST=10.60.1.60\nPG_PORT=55433\nWEB_PORT=8090\n" >> deploy/.env.gov'
   ```
   > compose env-file **同名 key 后出现的覆盖前者**，故把 `WEB_PORT=8090` 追加到末尾即可盖掉派生来的 `WEB_PORT=80`。
   > `CORS_ORIGINS` 也要一并改成新端口的 origin，否则跨域直连被拒。
3. **git archive 同步照旧**（`-c core.autocrlf=false`），gov compose 文件随源码走（已入库）。
4. **起栈用独立 `-p` project**（关键，否则会并入 `adp` project 覆盖线上容器）：
   ```bash
   ssh root@10.60.1.60 'cd /opt/ai-data-platform && nohup /usr/bin/bash -c \
     "docker compose -p adp-gov -f deploy/docker-compose.gov.yml --env-file deploy/.env.gov up -d --build" \
     > /tmp/adp_gov_deploy.log 2>&1 < /dev/null & disown; echo kicked-off'
   ```
   只改后端时 `up -d --build backend`：DJ venv 层在 pyproject COPY 之前，**命中缓存**，重建只跑后端 app venv，很快。
5. **验证走真实路由**：登录是 `POST /api/login/account`（`app/api/compat.py`，`/api` 前缀），**不是** `/api/v1/auth/login`
   （那个路径线上也 404，别拿它判健康）：
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST http://10.60.1.60:8090/api/login/account \
     -H 'Content-Type: application/json' -d '{"username":"admin","password":"ant.design","type":"account"}'   # 期望 200
   ```
   > `/openapi.json` 在部分分支代码上会 500（`UserDep` 前向引用未 rebuild 的 schema 生成问题），**仅影响 Swagger**，
   > 运行期路由不受影响——别用它判健康。

平行栈注意：

- **数据卷全新**：`adp_gov_data` 是空卷，看不到线上 `adp_data` 里已上传的文件/数据集产物；`adp_gov` 库里指向这些
  文件的行会指到不存在的对象。要连旧文件则改挂 `adp_data` 卷（会与线上共写，慎用）。
- **共用 postgres**：backend 经宿主机端口连 `adp-postgres`，`alembic upgrade head` 会作用到 `adp_gov`——
  部署前确认本分支迁移与 `adp_gov` 现有版本兼容。
- **停平行栈**：`docker compose -p adp-gov -f deploy/docker-compose.gov.yml --env-file deploy/.env.gov down`
  （只停 gov 的 backend/frontend，不动线上和共用 infra；卷保留）。
- **依赖钉版本**：无 lock 文件，`uv pip install -r pyproject.toml` 每次重建重解析。曾遇 `fastapi` 浮到 0.139.0
  路由懒加载回归；已在 pyproject 封顶 `<0.139`。重建后新分支若行为异常,先比对 `docker exec ... python -c "import fastapi;print(fastapi.__version__)"` 与线上是否一致。

## 注意 / 影响

- **数据卷保留**：`adp_pg_data`（PG）、`adp_data`（上传 + 数据集产物）不动；`up -d` 不删卷。
- **DB 迁移**：backend 入口 `backend-entrypoint.sh` 自动 `alembic upgrade head`——有新迁移会自动跑（部署前确认迁移可回退/兼容）。
- **Docker Hub 封锁**：.60 拉不到 Docker Hub；基础镜像（postgres/node/nginx）须已缓存。本命令只重建 `adp-backend`/`adp-frontend`（基于已缓存基础镜像），不拉新基础镜像。若 Dockerfile 改了基础镜像，需先 `docker pull --platform linux/amd64` → `save` → `scp` → `load`。
- **data-juicer 不随平台更新**：`git archive` 排除它。若 DJ 也要更新，单独 rsync `data-juicer/`（重建会更久）。
- **回退**：`docker compose ... down` 后用旧镜像 tag 重启；或 `git archive <旧commit>` 重新同步重建。生产前留意保留旧镜像 tag（如 `adp-backend:prev`）。
- **停整个栈**：`ssh root@10.60.1.60 'cd /opt/ai-data-platform && docker compose -f deploy/docker-compose.yml --env-file deploy/.env down'`（卷保留）。
