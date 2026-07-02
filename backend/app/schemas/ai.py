"""AI 相关 schema。

对照 frontend typings.d.ts 的 InferredSchema / GeneratedTaskConfig / Qa 类型。
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import CamelModel
from app.schemas.datasource import DataSourceType
from app.schemas.ingest_task import IngestSchedule


# ---- 推断 schema ----
class InferSchemaRequest(CamelModel):
    """推断 schema 请求。"""

    sample: str


class SchemaField(CamelModel):
    """推断出的单个字段。"""

    name: str
    type: str
    example: str
    nullable: bool


class InferredSchema(CamelModel):
    """推断出的完整 schema（作为 {data, success} 的 data）。"""

    format: str
    confidence: float
    fields: list[SchemaField]
    suggestion: str
    recommended_config: dict[str, Any] | None = None


# ---- 生成采集任务 ----
class GenerateTaskRequest(CamelModel):
    """生成采集任务请求。"""

    prompt: str


class GeneratedTaskConfig(CamelModel):
    """AI 生成的采集任务配置（作为 {data, success} 的 data）。"""

    name: str
    datasource_type: DataSourceType
    schedule: IngestSchedule
    config: dict[str, Any]
    explanation: str


# ---- 问答 ----
class QaRequest(CamelModel):
    """问答请求。"""

    question: str


class QaAnswer(CamelModel):
    """问答结果（作为 {data, success} 的 data，形状为 {answer}）。"""

    answer: str


# ---- 数据集 AI 命名 ----
class SuggestDatasetNameRequest(CamelModel):
    """AI 命名请求：据文件名 / 格式 / 分类起一个数据集名。"""

    filenames: list[str]
    data_type: str
    category: str | None = None


class SuggestedDatasetName(CamelModel):
    """AI 建议的数据集名（作为 {data, success} 的 data）。"""

    name: str
