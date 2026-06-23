"""分类相关 schema(#15,受控分类库,多级树)。

createdAt / usageCount / parentId 走 alias 驼峰,与全仓 by_alias 约定一致。
"""

from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class CategoryCreate(CamelModel):
    """新建分类入参。parent_id 省略/为 null → 根分类。"""

    name: str
    parent_id: str | None = None
    note: str | None = None


class CategoryUpdate(CamelModel):
    """编辑分类入参:全部可选,只更新传入字段(exclude_unset)。"""

    name: str | None = None
    parent_id: str | None = None
    note: str | None = None


class CategoryRead(CamelModel):
    """分类读模型(响应)。

    usage_count = 三实体(数据集/数据源/采集任务)引用该分类的总数,
    由路由批量聚合填充,非 ORM 字段,默认 0。
    children = 直接子分类(GET /categories 组装成嵌套树时填充),默认空。
    """

    id: str
    name: str
    parent_id: str | None = None
    note: str | None = None
    creator: str
    created_at: UtcDateTime
    usage_count: int = 0
    children: list[CategoryRead] = []
