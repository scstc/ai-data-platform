"""FastAPI 应用入口：CORS、路由装配与健康检查。"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import compat
from app.api.v1 import (
    ai,
    audit,
    augment,
    categories,
    construct,
    content_safety,
    data_lakes,
    data_tasks,
    datasets,
    datasources,
    distillation,
    evaluation,
    export,
    files,
    ingest_push,
    ingest_tasks,
    jobs,
    llm_config,
    llm_proxy,
    make,
    model_store,
    notifications,
    operators,
    pipelines,
    profile,
    quality,
    tags,
    trainset,
    uploads,
)
from app.api.v1.system import depts as system_depts
from app.api.v1.system import menus as system_menus
from app.api.v1.system import permissions as system_permissions
from app.api.v1.system import roles as system_roles
from app.api.v1.system import users as system_users
from app.core.audit import audit_middleware
from app.core.config import settings
from app.core.db import async_session_factory
from app.services import job_runner
from app.services import scheduler as scheduler_mod
from app.services.llm_config import refresh_cache

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """启动时：回收孤儿任务 + best-effort 刷新 LLM 配置缓存。"""
    async with async_session_factory() as session:
        await job_runner.reconcile_orphans(session)
    # best-effort：LLM 缓存刷新失败不阻断启动
    try:
        async with async_session_factory() as session:
            await refresh_cache(session)
    except Exception:  # noqa: BLE001
        _logger.warning("启动时刷新 LLM 配置缓存失败（已忽略）", exc_info=True)
    # best-effort：模型仓库根路径缓存（供 engine 注入 dj-process env）
    try:
        from app.services import model_store as model_store_svc

        async with async_session_factory() as session:
            await model_store_svc.refresh_cache(session)
    except Exception:  # noqa: BLE001
        _logger.warning("启动时刷新模型仓库路径缓存失败（已忽略）", exc_info=True)
    # best-effort:确保平台上传桶 + 数据湖桶存在(未配置 MinIO 时静默跳过)
    try:
        from app.services.external_store import (
            ensure_lake_bucket,
            ensure_upload_bucket,
        )

        await ensure_upload_bucket()
        await ensure_lake_bucket()
    except Exception:  # noqa: BLE001
        _logger.warning("启动时确保平台 MinIO 桶失败（已忽略）", exc_info=True)

    # 调度器(切片 C):scheduler_enabled=False 时跳过;启动 / 对账失败仅告警,
    # 不阻断 app 启动——采集主流程不依赖调度器在线(可手工触发)。
    # 注册到 scheduler 模块的全局引用,供 routes best-effort upsert/remove。
    scheduler: scheduler_mod.AsyncIOScheduler | None = None
    if settings.scheduler_enabled:
        try:
            scheduler = scheduler_mod.init_scheduler()
            scheduler.start()
            async with async_session_factory() as session:
                await scheduler_mod.reconcile(session, scheduler)
            _logger.info("调度器启动并对账完成")
        except Exception:  # noqa: BLE001
            _logger.warning(
                "调度器启动/对账失败（已忽略，采集主流程不依赖调度器）",
                exc_info=True,
            )
            scheduler_mod.shutdown_scheduler(scheduler)
            scheduler = None
    # 不论启动成功/失败都暴露给 routes:成功→实例;失败/未启用→None(routes 静默跳过)
    scheduler_mod.set_scheduler(scheduler)
    try:
        yield
    finally:
        # 关闭路径必须无条件执行:即便启动失败(scheduler=None)也要进入 finally
        scheduler_mod.shutdown_scheduler(scheduler)
        scheduler_mod.set_scheduler(None)


def create_app() -> FastAPI:
    """构建并返回 FastAPI 应用实例。"""
    app = FastAPI(title="AI 数据平台 API", lifespan=_lifespan)

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
    app.include_router(data_lakes.router, prefix="/api/v1")
    app.include_router(datasets.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(data_tasks.router, prefix="/api/v1")
    app.include_router(operators.router, prefix="/api/v1")
    app.include_router(pipelines.router, prefix="/api/v1")
    app.include_router(quality.router, prefix="/api/v1")
    app.include_router(distillation.router, prefix="/api/v1")
    app.include_router(make.router, prefix="/api/v1")
    app.include_router(augment.router, prefix="/api/v1")
    app.include_router(construct.router, prefix="/api/v1")
    app.include_router(trainset.router, prefix="/api/v1")
    app.include_router(content_safety.router, prefix="/api/v1")
    app.include_router(evaluation.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(ingest_tasks.router, prefix="/api/v1")
    app.include_router(ingest_push.router, prefix="/api/v1")
    app.include_router(uploads.router, prefix="/api/v1")
    app.include_router(files.router, prefix="/api/v1")
    app.include_router(ai.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(categories.router, prefix="/api/v1")
    app.include_router(tags.router, prefix="/api/v1")
    app.include_router(llm_config.router, prefix="/api/v1")
    app.include_router(llm_proxy.router, prefix="/api/v1")
    app.include_router(model_store.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    app.include_router(profile.router, prefix="/api/v1")
    app.include_router(system_menus.router, prefix="/api/v1")
    app.include_router(system_roles.router, prefix="/api/v1")
    app.include_router(system_users.router, prefix="/api/v1")
    app.include_router(system_depts.router, prefix="/api/v1")
    app.include_router(system_permissions.router, prefix="/api/v1")
    app.include_router(compat.router, prefix="/api")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok"}

    return app


app = create_app()
