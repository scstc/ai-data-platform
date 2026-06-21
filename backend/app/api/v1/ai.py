"""AI 能力路由：Schema 推断 / 采集任务生成 / 固定问答。

三端点均把 provider 返回的 camelCase dict 经对应 schema 校验后，
包成前端契约的 ``{data: ..., success: true}`` 形状返回。

provider 通过 ``get_ai_provider_dep`` 依赖注入，底层是模块级单例
（``functools.lru_cache``），默认启发式实现，零外部依赖；配置齐全时为 LLM。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.schemas.ai import (
    GeneratedPipeline,
    GeneratedTaskConfig,
    GeneratePipelineRequest,
    GenerateTaskRequest,
    InferredSchema,
    InferSchemaRequest,
    QaAnswer,
    QaRequest,
)
from app.schemas.common import CamelModel
from app.services import operator_catalog as oc
from app.services.ai import AIProvider, get_ai_provider

router = APIRouter(prefix="/ai", tags=["ai"])


def get_ai_provider_dep() -> AIProvider:
    """按当前活跃 LLM 配置选择 AI 提供者（启发式 / LLM）。

    每次请求重新解析，保证激活/切换配置后立即生效（不缓存）。
    """
    return get_ai_provider(settings)


# Annotated 依赖别名（与 datasources.py 的 SessionDep 同款写法，规避 ruff B008）
ProviderDep = Annotated[AIProvider, Depends(get_ai_provider_dep)]


# ---- 响应包装（{data, success} 形状）----
class InferSchemaResponse(CamelModel):
    """Schema 推断响应。"""

    data: InferredSchema
    success: bool = True


class GenerateTaskResponse(CamelModel):
    """采集任务生成响应。"""

    data: GeneratedTaskConfig
    success: bool = True


class QaResponse(CamelModel):
    """问答响应。"""

    data: QaAnswer
    success: bool = True


class GeneratePipelineResponse(CamelModel):
    """流水线生成响应。"""

    data: GeneratedPipeline
    success: bool = True


@router.post("/infer-schema", response_model=InferSchemaResponse)
async def infer_schema(
    body: InferSchemaRequest,
    provider: ProviderDep,
) -> InferSchemaResponse:
    """根据样本数据推断 Schema。"""
    result = await provider.infer_schema(body.sample)
    return InferSchemaResponse(data=InferredSchema.model_validate(result))


@router.post("/generate-task", response_model=GenerateTaskResponse)
async def generate_task(
    body: GenerateTaskRequest,
    provider: ProviderDep,
) -> GenerateTaskResponse:
    """根据自然语言描述生成采集任务配置。"""
    result = await provider.generate_task(body.prompt)
    return GenerateTaskResponse(data=GeneratedTaskConfig.model_validate(result))


@router.post("/qa", response_model=QaResponse)
async def qa(
    body: QaRequest,
    provider: ProviderDep,
) -> QaResponse:
    """回答平台使用相关问题。"""
    result = await provider.qa(body.question)
    return QaResponse(data=QaAnswer.model_validate(result))


@router.post("/generate-pipeline", response_model=GeneratePipelineResponse)
async def generate_pipeline(
    body: GeneratePipelineRequest,
    provider: ProviderDep,
) -> GeneratePipelineResponse:
    """据目标场景生成算子流水线(LLM 或启发式),经确定性校验后返回。"""
    ready = oc.ready_operator_context()
    raw = await provider.generate_pipeline(body.goal, ready)
    steps = oc.sanitize_pipeline(raw.get("operators", []))
    explanation = str(raw.get("explanation", ""))
    return GeneratePipelineResponse(
        data=GeneratedPipeline.model_validate(
            {"operators": steps, "explanation": explanation}
        )
    )


@router.post("/generate-quality", response_model=GeneratePipelineResponse)
async def generate_quality(
    body: GeneratePipelineRequest,
    provider: ProviderDep,
) -> GeneratePipelineResponse:
    """据目标生成质量评估流水线:只推 filter 类、可运行的算子。"""
    ready = oc.ready_operator_context(category="filter")
    raw = await provider.generate_pipeline(body.goal, ready)
    steps = oc.sanitize_pipeline(raw.get("operators", []))
    # 再加一道:只保留 filter 类(防 provider 越出限定上下文)
    steps = [
        s
        for s in steps
        if (op := oc.get_operator(s["name"])) and op["category"] == "filter"
    ]
    explanation = str(raw.get("explanation", ""))
    return GeneratePipelineResponse(
        data=GeneratedPipeline.model_validate(
            {"operators": steps, "explanation": explanation}
        )
    )
