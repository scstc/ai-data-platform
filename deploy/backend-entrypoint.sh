#!/bin/sh
# 后端容器入口:先跑数据库迁移,再起服务。
set -e

echo "[entrypoint] alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] starting uvicorn on 0.0.0.0:18003 ..."
exec uvicorn app.main:app --host 0.0.0.0 --port 18003
