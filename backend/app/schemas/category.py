"""分类相关 schema(#15,受控扁平分类库)。

createdAt / usageCount 走 alias 驼峰,与全仓 by_alias 约定一致。
"""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class CategoryCreate(CamelModel):
    """新建分类入参。"""

    name: str
    note: str | None = None


class CategoryUpdate(CamelModel):
    """编辑分类入参:全部可选,只更新传入字段(exclude_unset)。"""

    name: str | None = None
    note: str | None = None


class CategoryRead(CamelModel):
    """分类读模型(响应)。

    usage_count = 三实体(数据集/数据源/采集任务)引用该分类的总数,
    由路由批量聚合填充,非 ORM 字段,默认 0。
    """

    id: str
    name: str
    note: str | None = None
    creator: str
    created_at: datetime
    usage_count: int = 0
