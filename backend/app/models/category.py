"""分类 ORM 模型(受控分类库,跨实体共享单选,#15)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Category(Base):
    """分类:受控分类项;数据集/数据源/采集任务各可单选挂一个。

    多级树(邻接表 parent_id,根为 null);三实体用 category_id(String)纯引用、
    无 FK(沿用本仓库无-FK 约定)。同级 (parent_id, name) 唯一;环检测/后代判定走应用层
    (categories.py 路由)。PG 复合 unique 下 NULL 互不冲突,根的同级查重由应用层兜底。
    """

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_categories_parent_name"),
    )

    # 主键形如 "cat-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 上级分类 id(邻接表);根分类为 null
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 分类名:同级(parent_id 下)唯一、非空
    name: Mapped[str] = mapped_column(String, nullable=False)
    # 备注:可空
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    # 创建人(无 RBAC 前默认 admin)
    creator: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
