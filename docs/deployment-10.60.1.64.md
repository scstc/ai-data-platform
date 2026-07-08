# 10.60.1.64 部署记录

全新服务器 `10.60.1.64` 的独立部署过程与现状记录(2026-07-08/09)。与 `10.60.1.60`(线上生产)完全隔离,数据库/MinIO 均为全新实例,不共享任何数据。

## 服务器状况

| 项 | 值 |
|---|---|
| OS | Kylin Linux Advanced Server V10(Halberd),x86_64 |
| 硬件 | 45G 内存 / 726G 磁盘,无 NVIDIA GPU |
| 网络 | **完全无出网能力**——DNS 解析超时,直连公网 IP 被拒绝;且与内网其他机器(`.60`、内网 GitLab)也不通(防火墙/ACL 尚未对这台新机器放通) |
| `192.168.10.44` | 探测发现只是某网络设备自身的 Web 管理登录页(nginx,自签名证书),HTTP CONNECT 直接返回 400,**不能**当转发代理/网关用 |

因为 `.64` 完全无法访问外网或内网其他机器,常规的"目标机上 `docker compose up -d --build`"部署流程(参考根 `README.md` `/adp-deploy`)在这里行不通——build 阶段的 `apt-get`/`uv pip install`/`npm install` 全部会失败。

## 部署方案:借道 192.168.9.195 离线构建

`192.168.9.195`(Ubuntu 24.04,已装 Docker 29.6.1)有正常外网访问,且能直连 GitLab 内网(`223.112.233.194:8888`)和 `.64`(`10.60.1.64:22`),但 **`.195` 到不了 `.64` 的 SSH 密钥认证**,需要经本地开发机中转文件。

流程:

1. **Docker 引擎离线安装到 `.64`**:在 `.195` 下载 Docker 静态二进制(`download.docker.com/linux/static/stable/x86_64/docker-29.6.1.tgz`)+ `docker-compose` 独立二进制(GitHub Release `docker/compose`),连同手写的 `docker.service`/`containerd.service` systemd unit 一起,经本地机中转 scp 到 `.64` 安装。
   - **坑**:`docker.service` 若写 `ExecStart=... -H fd://` 会因未装 `docker.socket` 报 `no sockets found via socket activation`,必须去掉 `-H fd://`,直接监听默认 unix socket。
2. **源码同步到 `.195`**:`git -c core.autocrlf=false archive HEAD | ssh root@192.168.9.195 'cd .../ai-data-platform && tar x'`(当时分支 `feature/governance-remediation-plan`);`data-juicer/` 未入库,单独打 tar(排除 `.venv`/`.git`/`__pycache__`/`build`)传过去。
3. **在 `.195` 构建应用镜像**(CPU-only,`.64` 无 GPU):
   ```bash
   docker build -f deploy/backend.Dockerfile -t adp-backend:latest --build-arg DJ_EXTRAS="" .
   docker build -f deploy/frontend.Dockerfile -t adp-frontend:latest .
   docker pull postgres:16-alpine && docker pull minio/minio:latest && docker pull keking/kkfileview:latest
   ```
4. **`docker save` 单独打包**(按用户要求分开打包,不合并成一个 tar,共约 4.5GB):
   `adp-backend.tar`(2.6GB)、`kkfileview.tar`(1.6GB)、`postgres-16-alpine.tar`(297MB)、`minio.tar`(176MB)、`adp-frontend.tar`(64MB)。
5. **经本地开发机中转传到 `.64`**(`.195` 无法直接 scp 到 `.64`):`.195 → 本地暂存 → .64:/root/adp-images/`,再 `docker load` 逐个导入。
6. **部署编排**:采用仓库自带的"全新独立栈"模式(`deploy/docker-compose.local.yml` 的思路——PG + MinIO + backend + frontend + kkfileview 全部本地新建,不依赖 `.60`),去掉 GPU 预留段(`.64` 无显卡),镜像名对齐已 load 的 tag(`adp-backend:latest`/`adp-frontend:latest`,而非 `adp-local-*`)。落地为 `.64` 本机的:
   - `/opt/ai-data-platform/deploy/docker-compose.yml`
   - `/opt/ai-data-platform/deploy/.env`(`PG_PASSWORD`/`MINIO_ROOT_PASSWORD`/`AUTH_SECRET` 均为随机生成的强密钥,**只存在于 `.64` 本机,未回传/未入库**)
7. **算子目录导入**(alembic 迁移只建表,不建数据,是独立一步):
   ```bash
   docker exec adp-backend python /app/scripts/import_operators.py
   ```
   导入 212 个算子(从镜像内置的 `backend/app/data/operators_catalog.json`)。

## 当前状态

| 服务 | 地址 | 备注 |
|---|---|---|
| Web | http://10.60.1.64/ | admin / ant.design |
| 后端直连(Swagger) | http://10.60.1.64:18004/docs | |
| MinIO 控制台 | http://10.60.1.64:9001 | |
| kkFileView | http://10.60.1.64:8012 | |
| PostgreSQL | 10.60.1.64:55433 | 全新库,alembic 已跑到 `0063_operator_star`(对齐 `feature/governance-remediation-plan`) |

容器:`adp-postgres` / `adp-minio` / `adp-backend` / `adp-frontend` / `adp-kkfileview`(compose project 名 `adp`)。

## 已知限制 / 待办

- **`.64` 仍无出网/内网互通能力**:后续若要在 `.64` 本机直接 `docker pull`/`apt`/`pip`,需网络组开通 ACL,或继续走 `.195` 中转这条路径(本文档步骤可复用)。
- **`192.168.10.44` 当前不可用作代理网关**,需要有人登进它的管理后台配置转发规则才能用。
- **`.195` 是共享的多租户开发机**(挂了大量与本项目无关的其他服务容器),构建产物落在 `/root/adp-build/`,部署确认无误后应清理(`docker rmi adp-backend adp-frontend` 释放本地缓存 + 删除 `/root/adp-build/`、`/root/adp-images/` 等临时目录,`.64` 上同理清理 `/root/adp-images/`)。
- **每次这套栈需要更新**(新分支/新迁移):重复本文档的构建-传输-load 流程;`docker exec adp-backend python /app/scripts/import_operators.py` 会清空重插算子表,**若已有自定义上传的算子会被删除**,更新前需注意。
