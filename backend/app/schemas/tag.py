"""标签 schema(全局标签池,扁平,仅挂数据集)。

createdAt / usageCount 走 alias 驼峰,与全仓 by_alias 约定一致。
"""
from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class TagCreate(CamelModel):
    name: str


class TagUpdate(CamelModel):
    name: str


class TagMerge(CamelModel):
    source_id: str
    target_id: str


class TagBatchDelete(CamelModel):
    ids: list[str]


class TagRead(CamelModel):
    """标签读模型。usageCount = 引用该标签的数据集数,由路由聚合填充,非 ORM 字段,默认 0。"""

    id: str
    name: str
    created_at: UtcDateTime
    usage_count: int = 0
