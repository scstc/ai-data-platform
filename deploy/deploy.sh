#!/usr/bin/env bash
# 在目标服务器上构建并启动整套平台(PG + 后端含 DJ 引擎 + 前端)。
# 用法:在仓库根目录执行  bash deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."   # 切到仓库根(= docker 构建上下文)

if [ ! -d data-juicer ]; then
  echo "ERROR: 缺少 data-juicer/ 子仓库(后端引擎依赖)。" >&2
  exit 1
fi

if [ ! -f deploy/.env ]; then
  echo "[deploy] 首次运行:从 .env.example 生成 deploy/.env(请按需改密码/端口)"
  cp deploy/.env.example deploy/.env
fi

COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"

echo "[deploy] 构建并启动 ..."
$COMPOSE up -d --build

echo "[deploy] 当前状态:"
$COMPOSE ps
echo "[deploy] 完成。Web: http://<host>:$(grep -E '^WEB_PORT=' deploy/.env | cut -d= -f2)"
