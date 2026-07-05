#!/usr/bin/env bash
# 本地构建并启动纯本地栈(PG + MinIO + 后端含 DJ 引擎 + 前端),全部本地新建容器。
# 用法:在仓库根目录执行  bash deploy/deploy.local.sh
set -euo pipefail

cd "$(dirname "$0")/.."   # 切到仓库根(= docker 构建上下文)

if [ ! -d data-juicer ]; then
  echo "ERROR: 缺少 data-juicer/ 子仓库(后端引擎依赖)。先跑 /adp-init。" >&2
  exit 1
fi

if [ ! -f deploy/.env.local ]; then
  echo "[deploy-local] 首次运行:从 .env.local.example 生成 deploy/.env.local(请按需改端口/密码)"
  cp deploy/.env.local.example deploy/.env.local
fi

COMPOSE="docker compose -f deploy/docker-compose.local.yml --env-file deploy/.env.local"

echo "[deploy-local] 构建并启动 ..."
$COMPOSE up -d --build

echo "[deploy-local] 当前状态:"
$COMPOSE ps
echo "[deploy-local] 完成。Web: http://localhost:$(grep -E '^WEB_PORT=' deploy/.env.local | cut -d= -f2)"
echo "[deploy-local] MinIO 控制台: http://localhost:$(grep -E '^MINIO_CONSOLE_PORT=' deploy/.env.local | cut -d= -f2)"
