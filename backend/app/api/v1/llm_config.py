"""LLM 提供商配置路由：CRUD + 激活 + 连接测试 + 用量统计。

设计约束：
- api_key 在列表/读端点统一掩码（first4...last4），不下发明文；
  唯一例外是 GET /{id}/reveal（require_admin），供编辑对话框回填真实 Key。
- 写端点（新建/编辑/删除/激活）均需 require_admin。
- GET /llm-providers/usage 必须声明在 GET /llm-providers/{id} 之前，
  否则 "usage" 会被当成 id 命中详情路由。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.job import Job
from app.models.llm_model import LlmModel, _new_llm_model_id
from app.models.llm_provider import LlmProvider, _new_llm_provider_id
from app.models.llm_system_model import CAPABILITIES, LlmSystemModel
from app.models.llm_usage import LlmUsage
from app.schemas.common import CamelModel, UtcDateTime
from app.services.llm_config import refresh_cache

router = APIRouter(tags=["llm-config"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ---- 响应 schema -------------------------------------------------------

class LlmProviderRead(CamelModel):
    """LLM 提供商读模型（api_key 掩码）。"""

    id: str
    name: str
    provider: str
    base_url: str
    model: str
    api_key_masked: str  # "first4...last4" 或 "未配置"
    is_active: bool
    created_at: UtcDateTime
    updated_at: UtcDateTime


class LlmProviderCreate(CamelModel):
    """新建 LLM 提供商入参。model 可空：经「显示模型」拉取/系统模型设置指定。"""

    name: str
    provider: str
    base_url: str
    api_key: str
    model: str = ""


class LlmProviderUpdate(CamelModel):
    """编辑 LLM 提供商入参：全部可选，api_key 为空串时保留旧值。"""

    name: str | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 空串 / None = 保留旧值
    model: str | None = None


class LlmProviderTest(CamelModel):
    """无落库的连通性测试入参（用对话框里正在输入的配置，保存前校验）。

    model 为空时探测 GET /models（新建弹窗已不含模型字段）。
    """

    base_url: str
    api_key: str
    model: str = ""


class LlmSystemModelItem(CamelModel):
    """一个能力位的系统默认模型；provider_id/model 为 None 表示未设置。"""

    capability: str
    provider_id: str | None = None
    model: str | None = None


class LlmSystemModelsUpdate(CamelModel):
    """「系统模型设置」保存入参：提交的能力位覆盖写，provider_id 空 = 清除。"""

    items: list[LlmSystemModelItem]


class LlmModelRead(CamelModel):
    """供应商下一个可选模型的读模型。"""

    id: str
    provider_id: str
    model: str
    source: str  # fetched | manual
    created_at: UtcDateTime


class LlmModelCreate(CamelModel):
    """手动添加一个模型入参。"""

    model: str


class LlmModelSelect(CamelModel):
    """设置当前生效模型入参。"""

    model: str


# ---- 工具函数 -----------------------------------------------------------

def _mask_api_key(api_key: str) -> str:
    """把 api_key 掩码为 first4...last4；太短则全掩。"""
    if not api_key:
        return "未配置"
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:4]}...{api_key[-4:]}"


def _to_read(row: LlmProvider) -> LlmProviderRead:
    """ORM 行 → 读模型（含 api_key 掩码）。"""
    return LlmProviderRead(
        id=row.id,
        name=row.name,
        provider=row.provider,
        base_url=row.base_url,
        model=row.model,
        api_key_masked=_mask_api_key(row.api_key),
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _not_found(message: str = "LLM 提供商不存在") -> JSONResponse:
    """统一 404。"""
    return JSONResponse(
        status_code=404, content={"success": False, "message": message}
    )


def _extract_model_ids(body: Any) -> list[str]:
    """从 /models 响应中容错解析模型 id 列表（去重保序）。

    兼容多种实现差异：
    - OpenAI 标准：{"object":"list","data":[{"id":"..."}]}
    - 裸数组（Together 等）：[{"id":"..."}] 或 ["id1","id2"]
    - 数组项为 dict 时取 id / model / name，为 str 时直接用
    """
    items = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        mid: str | None = None
        if isinstance(item, str):
            mid = item
        elif isinstance(item, dict):
            val = item.get("id") or item.get("model") or item.get("name")
            if isinstance(val, str):
                mid = val
        mid = (mid or "").strip()
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def _model_to_read(row: LlmModel) -> LlmModelRead:
    """ORM 行 → 模型读模型。"""
    return LlmModelRead(
        id=row.id,
        provider_id=row.provider_id,
        model=row.model,
        source=row.source,
        created_at=row.created_at,
    )


async def _list_models_payload(
    session: AsyncSession, provider_id: str
) -> list[dict[str, Any]]:
    """查询某供应商的全部可选模型（创建时间倒序）→ 可序列化列表。"""
    rows = (
        await session.scalars(
            select(LlmModel)
            .where(LlmModel.provider_id == provider_id)
            .order_by(LlmModel.created_at.desc())
        )
    ).all()
    return [
        _model_to_read(row).model_dump(by_alias=True, mode="json") for row in rows
    ]


async def _probe_chat(base_url: str, api_key: str, model: str) -> dict[str, Any]:
    """向 {base_url}/chat/completions 发一条最小请求，验证连通性与凭证（timeout=15s）。

    model 为空时改为 GET {base_url}/models 探测（新建供应商可不填模型，
    模型经「显示模型」拉取 / 系统模型设置指定）。
    返回 {success, latencyMs, message, model}；任何异常都转成 success=False
    并带上错误信息，绝不抛出（由调用方包成统一响应）。
    """
    base = base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    t0 = time.monotonic()

    def _elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if model:
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                    "temperature": 0,
                }
                resp = await client.post(
                    f"{base}/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "success": True,
                    "latencyMs": _elapsed(),
                    "message": "连接成功",
                    "model": data.get("model", model),
                }
            resp = await client.get(f"{base}/models", headers=headers)
            if resp.status_code in (404, 405):
                return {
                    "success": False,
                    "latencyMs": _elapsed(),
                    "message": "该供应商无 /models 接口;请保存后在"
                    "「显示模型」中手动添加模型,再对模型测试",
                    "model": model,
                }
            resp.raise_for_status()
            count = len(_extract_model_ids(resp.json()))
        return {
            "success": True,
            "latencyMs": _elapsed(),
            "message": f"连接成功,发现 {count} 个可用模型",
            "model": model,
        }
    except Exception as exc:  # noqa: BLE001 — 网络/凭证错误均转为 success=False
        return {
            "success": False,
            "latencyMs": _elapsed(),
            # ConnectError/Timeout 的 str() 可能为空,兜底给出异常类名
            "message": str(exc) or type(exc).__name__,
            "model": model,
        }


# ---- 端点 ---------------------------------------------------------------

@router.get("/llm-providers")
async def list_llm_providers(session: SessionDep) -> JSONResponse:
    """列出全部 LLM 提供商（创建时间倒序）。"""
    rows = (
        await session.scalars(
            select(LlmProvider).order_by(LlmProvider.created_at.desc())
        )
    ).all()
    data = [_to_read(row).model_dump(by_alias=True, mode="json") for row in rows]
    return JSONResponse(content={"data": data, "success": True})


@router.post("/llm-providers", dependencies=[Depends(require_admin)])
async def create_llm_provider(
    body: LlmProviderCreate, session: SessionDep
) -> JSONResponse:
    """新建 LLM 提供商（默认未激活）。"""
    row = LlmProvider(
        id=_new_llm_provider_id(),
        name=body.name,
        provider=body.provider,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        is_active=False,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    # 无激活项时新建的提供商即回退生效项,刷新缓存使其立即可用
    await refresh_cache(session)
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


# 注意：/llm-providers/usage 必须在 /llm-providers/{id} 之前声明
@router.get("/llm-providers/usage")
async def llm_usage_stats(
    session: SessionDep,
    days: int = 7,
) -> JSONResponse:
    """LLM 调用用量统计（最近 N 天）。"""
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    # 总量聚合（token 数）
    _token_cols = select(
        func.count().label("total_calls"),
        func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LlmUsage.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(
            func.sum(LlmUsage.completion_tokens), 0
        ).label("completion_tokens"),
    ).where(LlmUsage.created_at >= since)
    agg = (await session.execute(_token_cols)).first()

    # 成功次数单独查（避免 bool→int cast 的跨数据库差异）
    success_count_row = await session.scalar(
        select(func.count()).where(
            LlmUsage.created_at >= since, LlmUsage.success.is_(True)
        )
    )
    total_calls = agg.total_calls if agg else 0
    success_count = success_count_row or 0
    success_rate = (success_count / total_calls) if total_calls > 0 else 0.0

    # 按 feature 分组
    by_feature_rows = (
        await session.execute(
            select(
                LlmUsage.feature,
                func.count().label("calls"),
                func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("tokens"),
            )
            .where(LlmUsage.created_at >= since)
            .group_by(LlmUsage.feature)
            .order_by(func.count().desc())
        )
    ).all()
    by_feature = [
        {"feature": r.feature, "calls": r.calls, "tokens": int(r.tokens)}
        for r in by_feature_rows
    ]

    # 按任务分组:算子经 llm-proxy 的调用带 job_id,取 token 用量 Top 20;
    # 外连 jobs 补任务名/类型(任务被删后 job_id 仍在,回退显示 id)
    tokens_col = func.coalesce(func.sum(LlmUsage.total_tokens), 0)
    by_job_rows = (
        await session.execute(
            select(
                LlmUsage.job_id,
                func.max(Job.name).label("job_name"),
                func.max(Job.type).label("job_type"),
                func.count().label("calls"),
                tokens_col.label("tokens"),
            )
            .join(Job, Job.id == LlmUsage.job_id, isouter=True)
            .where(LlmUsage.created_at >= since, LlmUsage.job_id.is_not(None))
            .group_by(LlmUsage.job_id)
            .order_by(tokens_col.desc())
            .limit(20)
        )
    ).all()
    by_job = [
        {
            "jobId": r.job_id,
            "jobName": r.job_name or r.job_id,
            "jobType": r.job_type or "",
            "calls": r.calls,
            "tokens": int(r.tokens),
        }
        for r in by_job_rows
    ]

    # 按天分组（YYYY-MM-DD）。date_trunc 表达式必须复用同一对象,否则 SELECT 与
    # GROUP BY 各生成不同绑定参数($1/$4),PG 认作不同表达式 → created_at 未分组报错。
    day_col = func.date_trunc("day", LlmUsage.created_at)
    by_day_rows = (
        await session.execute(
            select(
                day_col.label("day"),
                func.count().label("calls"),
                func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("tokens"),
            )
            .where(LlmUsage.created_at >= since)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    by_day = [
        {
            "day": r.day.strftime("%Y-%m-%d") if r.day else "",
            "calls": r.calls,
            "tokens": int(r.tokens),
        }
        for r in by_day_rows
    ]

    # 最近 20 条明细
    recent_rows = (
        await session.scalars(
            select(LlmUsage)
            .where(LlmUsage.created_at >= since)
            .order_by(LlmUsage.created_at.desc())
            .limit(20)
        )
    ).all()
    recent = [
        {
            "feature": r.feature,
            "model": r.model,
            "totalTokens": r.total_tokens,
            "success": r.success,
            "latencyMs": r.latency_ms,
            "createdAt": r.created_at.replace(tzinfo=UTC).isoformat().replace(
                "+00:00", "Z"
            ),
        }
        for r in recent_rows
    ]

    return JSONResponse(
        content={
            "data": {
                "totalCalls": total_calls,
                "totalTokens": int(agg.total_tokens) if agg else 0,
                "promptTokens": int(agg.prompt_tokens) if agg else 0,
                "completionTokens": int(agg.completion_tokens) if agg else 0,
                "successRate": round(success_rate, 4),
                "byFeature": by_feature,
                "byJob": by_job,
                "byDay": by_day,
                "recent": recent,
            },
            "success": True,
        }
    )


@router.put("/llm-providers/{provider_id}", dependencies=[Depends(require_admin)])
async def update_llm_provider(
    provider_id: str,
    body: LlmProviderUpdate,
    session: SessionDep,
) -> JSONResponse:
    """编辑 LLM 提供商；api_key 为空时保留原值。"""
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()

    updates = body.model_dump(exclude_unset=True)
    # 空 api_key 视为"不更新"
    if updates.get("api_key") == "" or updates.get("api_key") is None:
        updates.pop("api_key", None)

    for field, value in updates.items():
        setattr(row, field, value)

    await session.commit()
    await session.refresh(row)
    # 刷新缓存使 base_url / api_key / model 改动即时生效(激活项或回退生效项)
    await refresh_cache(session)
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


@router.delete("/llm-providers/{provider_id}", dependencies=[Depends(require_admin)])
async def delete_llm_provider(
    provider_id: str, session: SessionDep
) -> JSONResponse:
    """删除 LLM 提供商及其名下模型；若删除的是当前激活项则同时刷新缓存。"""
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()
    # 应用层级联：先清理名下模型与系统模型能力位引用（无 DB 外键）
    await session.execute(
        delete(LlmModel).where(LlmModel.provider_id == provider_id)
    )
    await session.execute(
        delete(LlmSystemModel).where(LlmSystemModel.provider_id == provider_id)
    )
    await session.delete(row)
    await session.commit()
    # 删除的可能是激活项或回退生效项,一律刷新缓存
    await refresh_cache(session)
    return JSONResponse(content={"success": True})


@router.post(
    "/llm-providers/{provider_id}/activate",
    dependencies=[Depends(require_admin)],
)
async def activate_llm_provider(
    provider_id: str, session: SessionDep
) -> JSONResponse:
    """激活指定提供商：先将所有其他记录设为未激活，再激活目标并刷新缓存。"""
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()

    # 全部置为未激活
    await session.execute(
        update(LlmProvider)
        .where(LlmProvider.id != provider_id)
        .values(is_active=False)
    )
    row.is_active = True
    await session.commit()
    await session.refresh(row)
    # 刷新模块级缓存
    await refresh_cache(session)
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


# ---- 系统模型设置（按能力位） --------------------------------------------


async def _system_models_payload(session: AsyncSession) -> list[dict[str, Any]]:
    """全部能力位的当前配置；chat 位未显式设置时回退展示当前激活供应商。"""
    rows = {
        r.capability: r
        for r in (await session.scalars(select(LlmSystemModel))).all()
    }
    items: list[LlmSystemModelItem] = []
    for cap in CAPABILITIES:
        row = rows.get(cap)
        if row is None and cap == "chat":
            active = (
                await session.scalars(
                    select(LlmProvider)
                    .where(LlmProvider.is_active.is_(True))
                    .limit(1)
                )
            ).first()
            if active is not None:
                items.append(
                    LlmSystemModelItem(
                        capability=cap,
                        provider_id=active.id,
                        model=active.model,
                    )
                )
                continue
        items.append(
            LlmSystemModelItem(
                capability=cap,
                provider_id=row.provider_id if row else None,
                model=row.model if row else None,
            )
        )
    return [i.model_dump(by_alias=True) for i in items]


@router.get("/llm-system-models")
async def list_llm_system_models(session: SessionDep) -> JSONResponse:
    """系统模型设置：返回全部能力位（chat/embedding/rerank/speech2text/tts）。"""
    data = await _system_models_payload(session)
    return JSONResponse(content={"data": data, "success": True})


@router.put("/llm-system-models", dependencies=[Depends(require_admin)])
async def update_llm_system_models(
    body: LlmSystemModelsUpdate, session: SessionDep
) -> JSONResponse:
    """保存系统模型设置：覆盖写提交的能力位，provider_id/model 空 = 清除。

    chat 位联动既有激活语义：激活对应供应商、写 provider.model 并刷新缓存，
    运行时消费方（AI 助手 / needs_api 算子）继续走 is_active，无需感知本表。
    """
    for item in body.items:
        if item.capability not in CAPABILITIES:
            return JSONResponse(
                status_code=422,
                content={
                    "success": False,
                    "message": f"未知能力位: {item.capability}",
                },
            )
    chat_changed = False
    for item in body.items:
        row = await session.get(LlmSystemModel, item.capability)
        if not item.provider_id or not item.model:
            # 清除该能力位
            if row is not None:
                await session.delete(row)
            if item.capability == "chat":
                await session.execute(update(LlmProvider).values(is_active=False))
                chat_changed = True
            continue
        provider = await session.get(LlmProvider, item.provider_id)
        if provider is None:
            return _not_found(f"提供商不存在: {item.provider_id}")
        if row is None:
            session.add(
                LlmSystemModel(
                    capability=item.capability,
                    provider_id=item.provider_id,
                    model=item.model,
                )
            )
        else:
            row.provider_id = item.provider_id
            row.model = item.model
        if item.capability == "chat":
            await session.execute(
                update(LlmProvider)
                .where(LlmProvider.id != item.provider_id)
                .values(is_active=False)
            )
            provider.is_active = True
            provider.model = item.model
            chat_changed = True
    await session.commit()
    if chat_changed:
        await refresh_cache(session)
    data = await _system_models_payload(session)
    return JSONResponse(content={"data": data, "success": True})


@router.post("/llm-providers/test", dependencies=[Depends(require_admin)])
async def test_llm_provider_config(body: LlmProviderTest) -> JSONResponse:
    """用未保存的配置测试连通性（不读写数据库）。

    供「新建/编辑供应商」对话框在保存前校验 base_url / api_key / model。
    路径无 {id} 段，与 /llm-providers/{id}/test 不冲突。
    """
    data = await _probe_chat(body.base_url, body.api_key, body.model)
    return JSONResponse(content={"data": data, "success": True})


@router.post("/llm-providers/{provider_id}/test")
async def test_llm_provider(
    provider_id: str, session: SessionDep
) -> JSONResponse:
    """向已保存的提供商发一条最小请求，验证连通性与凭证有效性（timeout=15s）。"""
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()
    data = await _probe_chat(row.base_url, row.api_key, row.model)
    return JSONResponse(content={"data": data, "success": True})


@router.get(
    "/llm-providers/{provider_id}/reveal",
    dependencies=[Depends(require_admin)],
)
async def reveal_llm_provider_key(
    provider_id: str, session: SessionDep
) -> JSONResponse:
    """下发某供应商的明文 API Key（仅管理员，供编辑对话框回填）。

    本模块"读端点只下发掩码"的唯一例外：受 require_admin 保护，
    仅服务于管理员可见的编辑页面。
    """
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()
    return JSONResponse(
        content={"data": {"apiKey": row.api_key}, "success": True}
    )


# ---- 模型清单（多模型管理 + 获取模型） ------------------------------------


@router.get("/llm-providers/{provider_id}/models")
async def list_llm_models(provider_id: str, session: SessionDep) -> JSONResponse:
    """列出该供应商已存的可选模型（创建时间倒序）。"""
    if await session.get(LlmProvider, provider_id) is None:
        return _not_found()
    data = await _list_models_payload(session, provider_id)
    return JSONResponse(content={"data": data, "success": True})


@router.post(
    "/llm-providers/{provider_id}/fetch-models",
    dependencies=[Depends(require_admin)],
)
async def fetch_llm_models(provider_id: str, session: SessionDep) -> JSONResponse:
    """调用供应商 GET {base_url}/models 拉取模型清单并 upsert 入库。

    用已存的 api_key（不依赖前端下发明文）。供应商不支持 / 报错时不抛 500，
    返回 data.success=false + message，由前端走预置清单兜底。
    """
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()

    base_url = row.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {row.api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            resp.raise_for_status()
            body = resp.json()
        model_ids = _extract_model_ids(body)
    except Exception as exc:  # noqa: BLE001 — 供应商不支持/网络错误，走前端兜底
        return JSONResponse(
            content={
                "data": {
                    "success": False,
                    "message": str(exc),
                    "added": 0,
                    "models": await _list_models_payload(session, provider_id),
                },
                "success": True,
            }
        )

    if not model_ids:
        return JSONResponse(
            content={
                "data": {
                    "success": False,
                    "message": "未从响应中解析到模型列表",
                    "added": 0,
                    "models": await _list_models_payload(session, provider_id),
                },
                "success": True,
            }
        )

    existing = set(
        (
            await session.scalars(
                select(LlmModel.model).where(LlmModel.provider_id == provider_id)
            )
        ).all()
    )
    added = 0
    for mid in model_ids:
        if mid not in existing:
            session.add(
                LlmModel(
                    id=_new_llm_model_id(),
                    provider_id=provider_id,
                    model=mid,
                    source="fetched",
                )
            )
            existing.add(mid)
            added += 1
    await session.commit()
    return JSONResponse(
        content={
            "data": {
                "success": True,
                "message": f"拉取成功，新增 {added} 个模型",
                "added": added,
                "models": await _list_models_payload(session, provider_id),
            },
            "success": True,
        }
    )


@router.post(
    "/llm-providers/{provider_id}/models",
    dependencies=[Depends(require_admin)],
)
async def add_llm_model(
    provider_id: str, body: LlmModelCreate, session: SessionDep
) -> JSONResponse:
    """手动添加一个模型（已存在则忽略，去重）。"""
    if await session.get(LlmProvider, provider_id) is None:
        return _not_found()
    model = body.model.strip()
    if not model:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "模型名不能为空"}
        )
    exists = await session.scalar(
        select(LlmModel)
        .where(LlmModel.provider_id == provider_id, LlmModel.model == model)
        .limit(1)
    )
    if exists is None:
        session.add(
            LlmModel(
                id=_new_llm_model_id(),
                provider_id=provider_id,
                model=model,
                source="manual",
            )
        )
        await session.commit()
    data = await _list_models_payload(session, provider_id)
    return JSONResponse(content={"data": data, "success": True})


@router.delete(
    "/llm-providers/{provider_id}/models/{model_id}",
    dependencies=[Depends(require_admin)],
)
async def delete_llm_model(
    provider_id: str, model_id: str, session: SessionDep
) -> JSONResponse:
    """删除一个已存模型。"""
    row = await session.get(LlmModel, model_id)
    if row is None or row.provider_id != provider_id:
        return _not_found("模型不存在")
    await session.delete(row)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post(
    "/llm-providers/{provider_id}/select-model",
    dependencies=[Depends(require_admin)],
)
async def select_llm_model(
    provider_id: str, body: LlmModelSelect, session: SessionDep
) -> JSONResponse:
    """把指定模型设为该供应商的当前生效模型（写回 provider.model）。

    刷新模块级缓存使切换即时对所有 AI 功能生效（激活项或回退生效项）。
    """
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        return _not_found()
    model = body.model.strip()
    if not model:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "模型名不能为空"}
        )
    row.model = model
    await session.commit()
    await session.refresh(row)
    await refresh_cache(session)
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )
