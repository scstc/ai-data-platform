"""标签 ORM 模型(全局标签池 + 数据集多对多关联)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tag(Base):
    """标签:全局标签池(自由输入时 find-or-create),与数据集多对多(#标签)。

    沿用仓库无-FK 约定;关联走 DatasetTag,纯 String 引用。颜色不落库——
    前端按 name 做 deterministic 哈希到预设色板(零管理、每标签稳定一色)。
    """

    __tablename__ = "tags"

    # 主键形如 "tag-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 标签名:全局唯一、非空
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )


class DatasetTag(Base):
    """数据集-标签关联(多对多)。复合主键 (dataset_id, tag_id) 天然去重。"""

    __tablename__ = "dataset_tags"

    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    tag_id: Mapped[str] = mapped_column(String, primary_key=True)
