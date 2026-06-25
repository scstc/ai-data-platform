"""部门 ORM 模型(RBAC 数据权限的组织维度,树结构)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Department(Base):
    """部门:parent_id 构成树;ancestors 存逗号分隔祖先 id 路径(根='0'),

    供「本部门及子」按 ``ancestors like '%,<deptId>,%'`` 一次查出子树。
    """

    __tablename__ = "departments"

    # 主键形如 "dept-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 祖先路径,形如 "0,dept-000000,";根部门为 "0"
    ancestors: Mapped[str] = mapped_column(
        String, nullable=False, default="0"
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leader: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="0")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
