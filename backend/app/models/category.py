"""分类 ORM 模型(受控扁平分类库,跨实体共享单选,#15)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Category(Base):
    """分类:受控的扁平分类项;数据集/数据源/采集任务各可单选挂一个。

    扁平单层(无 parent_id);三实体用 category_id(String)纯引用、无 FK
    (沿用本仓库无-FK 约定),改名天然同步。
    """

    __tablename__ = "categories"

    # 主键形如 "cat-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 分类名:唯一、非空
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # 备注:可空
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    # 创建人(无 RBAC 前默认 admin)
    creator: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
