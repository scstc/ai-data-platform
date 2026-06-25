"""数据源 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DataSource(Base):
    """数据源：对象存储 / HDFS / 数据库 / API。"""

    __tablename__ = "datasources"

    # 主键形如 "ds-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # 类型：s3 | hdfs | database | api
    type: Mapped[str] = mapped_column(String, nullable=False)
    # 数据库品牌（仅 type=database 时使用）
    db_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # 状态：connected | failed | pending
    status: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # 分类(#15):受控分类库引用 categories.id(无 FK,可空,单选)
    category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    creator: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # 所属部门(RBAC 数据权限快照);存量回填为根部门
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
