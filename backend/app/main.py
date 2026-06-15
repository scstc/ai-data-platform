"""FastAPI 应用入口：CORS、路由装配与健康检查。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import compat
from app.api.v1 import (
    ai,
    audit,
    content_safety,
    datasets,
    datasources,
    ingest_tasks,
    jobs,
    operators,
    quality,
    uploads,
)
from app.core.audit import audit_middleware
from app.core.config import settings


def create_app() -> FastAPI:
    """构建并返回 FastAPI 应用实例。"""
    app = FastAPI(title="AI 数据平台 API")

    # 审计中间件先注册;CORS 后注册以保证其在最外层(OPTIONS 预检不被审计干扰)。
    app.middleware("http")(audit_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(datasources.router, prefix="/api/v1")
    app.include_router(datasets.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(operators.router, prefix="/api/v1")
    app.include_router(quality.router, prefix="/api/v1")
    app.include_router(content_safety.router, prefix="/api/v1")
    app.include_router(ingest_tasks.router, prefix="/api/v1")
    app.include_router(uploads.router, prefix="/api/v1")
    app.include_router(ai.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(compat.router, prefix="/api")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok"}

    return app


app = create_app()
