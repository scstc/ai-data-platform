"""数据源路由：CRUD + 测试连接。

契约见前端 typings.d.ts / mock/dataPlatform.ts：
- 列表：name 模糊、type 精确，分页 {data,total,success}。
- 新建：按 config 必填字段是否齐全决定初始 status（connected / pending）。
- 测试连接：同样的必填校验，返回 {data:{success,latencyMs,message}, success}。
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_perm
from app.api.v1.categories import build_category_name_map
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
from app.services import external_store
from app.services.connectors import resolve
from app.services.connectors.base import ConnectorNotReady, IngestError
from app.services.external_store import ExternalStoreError

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


def _push_url(request: Request, token: str) -> str:
    """由当前请求的 host 构造真实入站推送地址 .../api/v1/ingest/push/<token>。

    用请求 base_url(scheme+host)而非硬编码,适配代理/不同部署域名。
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/ingest/push/{token}"


async def _probe_via_connector(
    type_: str, db_kind: str | None, config: dict[str, Any] | None
) -> tuple[bool, int, str]:
    """经连接器注册表探测连接(§4.8 取代原 randint 假成功 + 单独的 PG 探测)。

    - 命中连接器 → 调 ``probe`` 真测(PG/S3 真连;国产库未装驱动诚实 not-ready)。
    - 未命中(如 db_kind 缺失/未知)→ (False, 0, 明确文案),绝不伪造 success(Rule 12)。
    探测失败/驱动未就绪均不崩、不 500。
    """
    connector = resolve(type_, db_kind)
    if connector is None:
        kind = f"{type_}/{db_kind}" if db_kind else type_
        return (False, 0, f"暂不支持的数据源类型「{kind}」,无可用连接器")
    try:
        return await connector.probe(config or {})
    except ConnectorNotReady as exc:
        # 连接器结构就绪但环境未就绪(缺驱动/无集群):诚实失败,不崩
        return (False, 0, str(exc))
    except Exception as exc:  # noqa: BLE001 探测任何异常都不应 500
        return (False, 0, f"连接失败:{exc}")


class _SingleDataSource(CamelModel):
    """单个数据源响应：{data:{...}, success:true}。"""

    data: DataSourceRead
    success: bool = True


def _not_found(message: str = "数据源不存在") -> JSONResponse:
    """统一的 404 响应：{success:false, message}。"""
    return JSONResponse(status_code=404, content={"success": False, "message": message})


async def _read_with_category(
    session: AsyncSession, item: DataSource
) -> DataSourceRead:
    """组装单个数据源读模型并回填 categoryName(分类可空)。"""
    read = DataSourceRead.model_validate(item)
    if item.category_id:
        names = await build_category_name_map(session, [item.category_id])
        read.category_name = names.get(item.category_id)
    return read


@router.get(
    "/datasources",
    response_model=PageResponse[DataSourceRead],
    dependencies=[Depends(require_perm("ingest:datasource:list"))],
)
async def list_datasources(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, alias="pageSize")] = 10,
    name: Annotated[str | None, Query()] = None,
    type: Annotated[DataSourceType | None, Query()] = None,
    category_id: Annotated[str | None, Query(alias="categoryId")] = None,
) -> PageResponse[DataSourceRead]:
    """分页查询数据源：name 模糊匹配、type 精确匹配、categoryId 精确匹配。"""
    conditions = []
    if name:
        conditions.append(DataSource.name.ilike(f"%{name}%"))
    if type:
        conditions.append(DataSource.type == type)
    if category_id:
        conditions.append(DataSource.category_id == category_id)

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

    # 批量取分类名(避免 N+1),回填 categoryName
    cat_names = await build_category_name_map(
        session, [row.category_id for row in rows]
    )
    data = []
    for row in rows:
        item = DataSourceRead.model_validate(row)
        if row.category_id:
            item.category_name = cat_names.get(row.category_id)
        data.append(item)
    return PageResponse[DataSourceRead](data=data, total=total)


@router.post(
    "/datasources",
    response_model=_SingleDataSource,
    dependencies=[Depends(require_admin)],
)
async def create_datasource(
    body: DataSourceCreate,
    session: SessionDep,
    request: Request,
) -> _SingleDataSource:
    """新建数据源。

    - postgresql / s3:经连接器真连定状态(connected/failed)。
    - api:生成入站 pushToken 并把 config.url 回填为真实入站地址,状态 connected。
    - 其余:按 config 必填字段齐全 → connected,否则 pending。
    """
    config: dict[str, Any] = dict(body.config or {})
    ds_id = _new_id()
    if body.type == "database" and body.db_kind == "postgresql":
        ok, _, _ = await _probe_via_connector(body.type, body.db_kind, config)
        status = "connected" if ok else "failed"
    elif body.type == "s3":
        ok, _, _ = await _probe_via_connector(body.type, body.db_kind, config)
        status = "connected" if ok else "failed"
    elif body.type == "api":
        # API 推送:生成入站凭证 token 并回填真实入站地址(替换前端占位 url)。
        token = secrets.token_urlsafe(16)
        config["pushToken"] = token
        config["url"] = _push_url(request, token)
        status = "connected"
    else:
        status = "connected" if _config_is_valid(body.type, config) else "pending"
    item = DataSource(
        id=ds_id,
        name=body.name,
        type=body.type,
        db_kind=body.db_kind,
        status=status,
        config=config,
        description=body.description,
        category_id=body.category_id,
        creator="admin",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _SingleDataSource(data=await _read_with_category(session, item))


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
    return _SingleDataSource(data=await _read_with_category(session, item))


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
    """测试连接:经连接器注册表派发 probe(§4.8,删除原 randint 假成功)。

    - PG/S3:真连真测;goldendb 有 asyncmy 时真测,否则诚实 not-ready。
    - 国产库未装驱动 / 不支持类型:success=False + 明确文案,绝不伪造成功(Rule 12)。
    返回 bare {success, latencyMs, message}(与 mock/前端契约一致,不套 data 信封)。
    """
    ok, latency_ms, message = await _probe_via_connector(
        body.type, body.db_kind, body.config
    )
    return TestConnectionResult(success=ok, latency_ms=latency_ms, message=message)


@router.get("/datasources/{ds_id}/tables")
async def list_datasource_tables(ds_id: str, session: SessionDep) -> JSONResponse:
    """列出数据源库内的表/对象(供采集任务勾选)。

    经连接器注册表派发 list_tables(§4.8,去掉原「仅 PostgreSQL」硬卡):
    - PG/MySQL 族 → information_schema 表名;s3/hdfs → 对象/路径;
    - 不支持的类型 / 未装驱动 / 无集群 → 400 明确文案,不崩、不 500。
    """
    ds = await session.get(DataSource, ds_id)
    if ds is None:
        return _not_found()
    connector = resolve(ds.type, ds.db_kind)
    if connector is None:
        kind = f"{ds.type}/{ds.db_kind}" if ds.db_kind else ds.type
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"暂不支持列出「{kind}」的表/对象,无可用连接器",
            },
        )
    try:
        tables = await connector.list_tables(ds.config or {})
    except ConnectorNotReady as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": str(exc)},
        )
    except IngestError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"获取表失败:{exc}"},
        )
    return JSONResponse(content={"data": tables, "success": True})


async def _require_s3_datasource(
    ds_id: str, session: AsyncSession
) -> DataSource | JSONResponse:
    """取数据源并校验为 s3 类型(供托管向导列桶/列对象)。失败直接给响应。"""
    ds = await session.get(DataSource, ds_id)
    if ds is None:
        return _not_found()
    if ds.type != "s3":
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "仅支持 s3 数据源"},
        )
    return ds


@router.get("/datasources/{ds_id}/buckets")
async def list_datasource_buckets(
    ds_id: str, session: SessionDep
) -> JSONResponse:
    """列出 s3 数据源下的全部桶(真连,供托管向导,#18)。"""
    ds = await _require_s3_datasource(ds_id, session)
    if isinstance(ds, JSONResponse):
        return ds
    try:
        buckets = await external_store.list_buckets(ds.config or {})
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"列桶失败:{exc}"},
        )
    return JSONResponse(content={"data": buckets, "success": True})


@router.get("/datasources/{ds_id}/objects")
async def list_datasource_objects(
    ds_id: str,
    session: SessionDep,
    bucket: Annotated[str, Query()],
    prefix: Annotated[str, Query()] = "",
) -> JSONResponse:
    """列出 s3 数据源指定桶/前缀下的对象 [{key,size,lastModified}](上限 1000,#18)。"""
    ds = await _require_s3_datasource(ds_id, session)
    if isinstance(ds, JSONResponse):
        return ds
    try:
        objects = await external_store.list_objects(ds.config or {}, bucket, prefix)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"列对象失败:{exc}"},
        )
    return JSONResponse(content={"data": objects, "success": True})
