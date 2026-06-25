"""数据集级 ACL ORM 模型(共享/成员权限)。

一行 = 把某数据集授给某主体(用户或角色)某个级别(view/edit/admin)。
owner/超管隐式全权,不在此表;本表只记显式授权。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DatasetAcl(Base):
    """数据集授权条目:(dataset, subject) → level,唯一约束防重复授权。"""

    __tablename__ = "dataset_acl"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "subject_type", "subject_id", name="uq_dataset_acl_subject"
        ),
    )

    # 主键形如 "dac-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # user | role
    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    # 用户 id 或角色 id
    subject_id: Mapped[str] = mapped_column(String, nullable=False)
    # view | edit | admin
    level: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
