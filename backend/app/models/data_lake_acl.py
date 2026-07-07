"""数据湖级 ACL ORM 模型(共享/成员权限),镜像 dataset_acl 模式。

一行 = 把某数据湖授给某主体(用户或组织内所有人)某个级别(view/edit/admin)。
owner/超管隐式全权,不在此表;本表只记显式授权。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DataLakeAcl(Base):
    """数据湖授权条目:(lake, subject) → level,唯一约束防重复授权。"""

    __tablename__ = "data_lake_acl"
    __table_args__ = (
        UniqueConstraint(
            "lake_id", "subject_type", "subject_id", name="uq_data_lake_acl_subject"
        ),
    )

    # 主键形如 "lac-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    lake_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # user | all(与 dataset_acl 同,角色授权已取消)
    subject_type: Mapped[str] = mapped_column(String, nullable=False)
    # 用户 id(all 固定为 "*")
    subject_id: Mapped[str] = mapped_column(String, nullable=False)
    # view | edit | admin
    level: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
