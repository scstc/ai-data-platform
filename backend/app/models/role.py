"""角色 ORM 模型(RBAC:权限与数据范围的载体)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Role(Base):
    """角色:role_key 为权限字符(如 admin);data_scope 决定数据范围。"""

    __tablename__ = "roles"

    # 主键形如 "role-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # 权限字符(唯一),前端/后端按它识别超管等
    role_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # all | custom | dept | dept_and_child | self
    data_scope: Mapped[str] = mapped_column(
        String, nullable=False, default="self"
    )
    # "0" 正常 / "1" 停用
    status: Mapped[str] = mapped_column(String, nullable=False, default="0")
    remark: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
