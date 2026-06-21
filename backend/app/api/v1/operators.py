"""算子目录路由:兼容旧 /operators + 全量算子市场(查询/分面/详情)。

设计见 docs/plan/04-算子市场设计.md。算子目录是无状态参考数据(不入库)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services import capabilities as caps
from app.services import operator_catalog as oc
from app.services.engine import multimodal_ready

router = APIRouter(tags=["operators"])


@router.get("/operators/capabilities")
async def operator_capabilities() -> JSONResponse:
    """当前环境探测到的执行能力(cuda/vllm/ray/llm),供前端「环境能力」指示。"""
    return JSONResponse(content={"data": caps.capabilities_api(), "success": True})


@router.get("/operators")
async def list_operators() -> JSONResponse:
    """加工算子目录(旧形态,供加工页编排选择)。

    含 ready;并按当前部署能力额外纳入:配了 LLM API → needs_api;装了多模态引擎
    (torch)→ needs_media。needs_compute 不列出(无 GPU 跑不通)。
    """
    data = oc.legacy_operators(
        llm_configured=bool(settings.openai_api_key),
        multimodal_ready=await multimodal_ready(),
    )
    return JSONResponse(content={"data": data, "success": True})


# 注意:/operators/catalog* 必须声明在 /operators/{name} 之前,
# 否则 "catalog" 会被当成 name 命中详情路由。
@router.get("/operators/catalog/meta")
async def catalog_meta() -> JSONResponse:
    """算子目录概览(总数/各维度分布/推荐数),驱动市场筛选项与统计卡。"""
    return JSONResponse(content={"data": oc.meta_api(), "success": True})


@router.get("/operators/catalog")
async def catalog(
    scenario: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    modality: Annotated[str | None, Query()] = None,
    resource_class: Annotated[str | None, Query(alias="resourceClass")] = None,
    runnable: Annotated[str | None, Query()] = None,
    recommend: Annotated[bool | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500, alias="pageSize")] = 24,
) -> JSONResponse:
    """算子市场主接口:多维分面过滤 + 分页。"""
    result = oc.query_catalog(
        scenario=scenario,
        category=category,
        modality=modality,
        resource_class=resource_class,
        runnable=runnable,
        recommend=recommend,
        keyword=keyword,
        current=current,
        page_size=page_size,
    )
    return JSONResponse(
        content={
            "data": [oc.to_api(op) for op in result["data"]],
            "total": result["total"],
            "success": True,
        }
    )


@router.get("/operators/{name}")
async def operator_detail(name: str) -> JSONResponse:
    """单算子详情(全字段:参数表 + 示例 + 引用 + 详情页链接)。"""
    op = oc.get_operator(name)
    if op is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "算子不存在"},
        )
    return JSONResponse(content={"data": oc.to_api(op), "success": True})
