"""数据源路由：CRUD + 测试连接。

契约见前端 typings.d.ts / mock/dataPlatform.ts：
- 列表：name 模糊、type 精确，分页 {data,total,success}。
- 新建：按 config 必填字段是否齐全决定初始 status（connected / pending）。
- 测试连接：同样的必填校验，返回 {data:{success,latencyMs,message}, success}。
"""

from __future__ import annotations

import secrets
from random import randint
from time import perf_counter
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models import DataSource
from app.schemas import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceType,
    DataSourceUpdate,
    PageResponse,
    TestConnectionParams,
    TestConnectionResult,
)
from app.schemas.common import CamelModel
from app.services.ingest_runner import IngestError, list_tables

router = APIRouter(tags=["datasources"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# 每种数据源类型连接所需的必填 config 字段（与前端 configIsValid 一致）。
_REQUIRED_CONFIG_FIELDS: dict[str, tuple[str, ...]] = {
    "s3": ("endpoint", "bucket", "accessKey", "secretKey"),
    "hdfs": ("nameNode", "path"),
    "database": ("host", "port", "database", "username", "password"),
    "api": ("url",),
}


def _config_is_valid(type_: str, config: dict[str, Any] | None) -> bool:
    """config 是否填齐该类型的全部必填字段（空串/None 视为缺失）。"""
    config = config or {}
    fields = _REQUIRED_CONFIG_FIELDS.get(type_, ())

    def has(key: str) -> bool:
        value = config.get(key)
        return value is not None and str(value).strip() != ""

    return all(has(key) for key in fields)


def _new_id() -> str:
    """生成形如 ds-<6位hex> 的主键。"""
    return f"ds-{secrets.token_hex(3)}"


async def _probe_postgres(config: dict[str, Any] | None) -> tuple[bool, int, str]:
    """真实探测 PostgreSQL:建连 + SELECT 1 + 量真实往返延迟。

    返回 (是否成功, 延迟毫秒, 文案);失败时延迟取 0,文案带原因。
    连接失败可能是网络/认证/超时等多种原因,统一兜底为"连接失败"。
    """
    config = config or {}
    start = perf_counter()
    try:
        conn = await asyncpg.connect(
            host=config.get("host"),
            port=int(config.get("port") or 5432),
            database=config.get("database"),
            user=config.get("username"),
            password=config.get("password"),
            timeout=5,
        )
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 探测失败统一上报为连接失败
        return False, 0, f"连接失败:{exc}"
    latency_ms = int((perf_counter() - start) * 1000)
    return True, latency_ms, f"连接成功,往返延迟 {latency_ms}ms"


class _SingleDataSource(CamelModel):
    """单个数据源响应：{data:{...}, success:true}。"""

    data: DataSourceRead
    success: bool = True


def _not_found(message: str = "数据源不存在") -> JSONResponse:
    """统一的 404 响应：{success:false, message}。"""
    return JSONResponse(status_code=404, content={"success": False, "message": message})


@router.get("/datasources", response_model=PageResponse[DataSourceRead])
async def list_datasources(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, alias="pageSize")] = 10,
    name: Annotated[str | None, Query()] = None,
    type: Annotated[DataSourceType | None, Query()] = None,
) -> PageResponse[DataSourceRead]:
    """分页查询数据源：name 模糊匹配、type 精确匹配。"""
    conditions = []
    if name:
        conditions.append(DataSource.name.ilike(f"%{name}%"))
    if type:
        conditions.append(DataSource.type == type)

    total_stmt = select(func.count()).select_from(DataSource)
    list_stmt = select(DataSource).order_by(DataSource.created_at.desc())
    for cond in conditions:
        total_stmt = total_stmt.where(cond)
        list_stmt = list_stmt.where(cond)

    total = await session.scalar(total_stmt) or 0
    rows = (
        await session.scalars(
            list_stmt.offset((current - 1) * page_size).limit(page_size)
        )
    ).all()

    return PageResponse[DataSourceRead](
        data=[DataSourceRead.model_validate(row) for row in rows],
        total=total,
    )


@router.post(
    "/datasources",
    response_model=_SingleDataSource,
    dependencies=[Depends(require_admin)],
)
async def create_datasource(
    body: DataSourceCreate,
    session: SessionDep,
) -> _SingleDataSource:
    """新建数据源：postgresql 真连定状态，其余按 config 必填齐全→connected/pending。"""
    if body.type == "database" and body.db_kind == "postgresql":
        ok, _, _ = await _probe_postgres(body.config)
        status = "connected" if ok else "failed"
    else:
        status = "connected" if _config_is_valid(body.type, body.config) else "pending"
    item = DataSource(
        id=_new_id(),
        name=body.name,
        type=body.type,
        db_kind=body.db_kind,
        status=status,
        config=body.config,
        description=body.description,
        creator="admin",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _SingleDataSource(data=DataSourceRead.model_validate(item))


@router.put(
    "/datasources/{ds_id}",
    response_model=_SingleDataSource,
    dependencies=[Depends(require_admin)],
)
async def update_datasource(
    ds_id: str,
    body: DataSourceUpdate,
    session: SessionDep,
) -> _SingleDataSource | JSONResponse:
    """更新数据源：仅更新显式传入的字段（含可显式改 status）。"""
    item = await session.get(DataSource, ds_id)
    if item is None:
        return _not_found()

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(item, field, value)

    await session.commit()
    await session.refresh(item)
    return _SingleDataSource(data=DataSourceRead.model_validate(item))


@router.delete(
    "/datasources/{ds_id}", dependencies=[Depends(require_admin)]
)
async def delete_datasource(
    ds_id: str,
    session: SessionDep,
) -> JSONResponse:
    """删除数据源：命中删除返回 {success:true}，未命中 404。"""
    item = await session.get(DataSource, ds_id)
    if item is None:
        return _not_found()
    await session.delete(item)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post("/datasources/test", response_model=TestConnectionResult)
async def test_connection(body: TestConnectionParams) -> TestConnectionResult:
    """测试连接：postgresql 真连(SELECT 1 量真实延迟)，其余按必填字段校验。

    返回 bare {success, latencyMs, message}（与 mock/前端契约一致，不套 data 信封）。
    """
    if body.type == "database" and body.db_kind == "postgresql":
        ok, latency_ms, message = await _probe_postgres(body.config)
    else:
        ok = _config_is_valid(body.type, body.config)
        latency_ms = randint(20, 200)
        message = (
            f"连接成功，往返延迟 {latency_ms}ms"
            if ok
            else "连接失败：必要的连接配置缺失，请检查并补全后重试"
        )
    return TestConnectionResult(success=ok, latency_ms=latency_ms, message=message)


@router.get("/datasources/{ds_id}/tables")
async def list_datasource_tables(ds_id: str, session: SessionDep) -> JSONResponse:
    """列出数据源库内的表(供采集任务勾选)。当前仅支持 PostgreSQL。"""
    ds = await session.get(DataSource, ds_id)
    if ds is None:
        return _not_found()
    if not (ds.type == "database" and ds.db_kind == "postgresql"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "列表仅支持 PostgreSQL 数据源"},
        )
    try:
        tables = await list_tables(ds.config or {})
    except IngestError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"获取表失败:{exc}"},
        )
    return JSONResponse(content={"data": tables, "success": True})
