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
    GeneratedTaskConfig,
    GenerateTaskRequest,
    InferredSchema,
    InferSchemaRequest,
    QaAnswer,
    QaRequest,
    SuggestDatasetNameRequest,
    SuggestedDatasetName,
    SuggestedTags,
    SuggestTagsRequest,
)
from app.schemas.common import CamelModel
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


class SuggestDatasetNameResponse(CamelModel):
    """数据集 AI 命名响应。"""

    data: SuggestedDatasetName
    success: bool = True


class SuggestTagsResponse(CamelModel):
    """数据集 AI 打标响应。"""

    data: SuggestedTags
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


@router.post(
    "/suggest-dataset-name", response_model=SuggestDatasetNameResponse
)
async def suggest_dataset_name(
    body: SuggestDatasetNameRequest,
    provider: ProviderDep,
) -> SuggestDatasetNameResponse:
    """据文件名 / 格式 / 分类用 AI 建议一个数据集名（LLM 或启发式）。"""
    result = await provider.suggest_dataset_name(
        body.filenames, body.data_type, body.category
    )
    return SuggestDatasetNameResponse(
        data=SuggestedDatasetName.model_validate(result)
    )


@router.post("/suggest-tags", response_model=SuggestTagsResponse)
async def suggest_tags(
    body: SuggestTagsRequest,
    provider: ProviderDep,
) -> SuggestTagsResponse:
    """据数据集名称与元数据用 AI 建议标签（LLM 或启发式兜底）。"""
    result = await provider.suggest_tags(
        body.name,
        body.description,
        body.category,
        body.data_type,
        body.existing_tags,
        body.known_tags,
    )
    return SuggestTagsResponse(data=SuggestedTags.model_validate(result))
