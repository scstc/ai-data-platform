---
description: 一键本地启动数据平台（后端 :18003 + 前端 :8001 真实后端）——日常"把项目跑起来"
---

# /adp-start — 一键启动数据平台

把数据平台前后端在本地跑起来，前端连真实后端（非 mock）。比分别 `/adp-server` + `/adp-web`
更省事，且用的是本机验证过的命令（避开 `uv run` exit 127、端口残留等坑）。

前置：后端连**远程 PG**（`backend/.env` 里 `DATABASE_URL` 指向 `10.60.1.60:55433`），无需本地起 docker；
前端依赖已装（`frontend/node_modules` 在）。

## 执行步骤

### 第一步：清端口残留（18003 / 8001）

会话退出可能留 node/uvicorn 残留进程占端口，先按 PID 清掉（Umi 残留会跳端口、原端口 405）。

```powershell
foreach ($p in 18003, 8001) {
  $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($c) { foreach ($x in $c) { taskkill /PID $x.OwningProcess /T /F }; Start-Sleep -Milliseconds 600 }
}
```

### 第二步：起后端（:18003，venv uvicorn 直跑）

```bash
cd backend && ./.venv/Scripts/uvicorn.exe app.main:app --host 0.0.0.0 --port 18003 > /tmp/adp_server.log 2>&1
```

run_in_background 启动。**坑**：不要用 `uv run uvicorn`（本机 `uv` 会 exit 127）；直接用 venv 里的 `uvicorn.exe`。
无 `--reload`，改 `.py` 要手动重启。

### 第三步：起前端（:8001，MOCK=none 连真实后端）

```bash
cd frontend && PORT=8001 MOCK=none npm run dev > /tmp/adp_web.log 2>&1
```

run_in_background 启动。**坑**：必须 `PORT=8001`（避开 data-juicer dj-api 的 8000）；`MOCK=none` 才走真实后端，
`npm run start` 默认走 mock。

### 第四步：探测就绪

```bash
until curl -sf http://127.0.0.1:18003/healthz >/dev/null; do sleep 1; done   # 后端
until curl -sf -o /dev/null http://127.0.0.1:8001/ >/dev/null; do sleep 2; done # 前端（umi 编译约 15-30s）
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/api/v1/data-tasks   # 代理连通 → 200
```

### 第五步：告知用户

```
前端：http://127.0.0.1:8001/   登录 admin / ant.design
后端：http://127.0.0.1:18003/docs（Swagger）
数据库：远程 PG 10.60.1.60:55433（经 backend/.env）
```

入口：**运维监控 → 数据任务**（统一控制台）；**数据评估 → 质量评估**（跑成功后报告 Tab 展示 dj-analyze analysis）。

## 停止

kill 两个后台任务；或按端口清进程（同第一步）。
