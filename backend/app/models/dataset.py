"""数据集 ORM 模型(仓库重心,版本的容器)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Dataset(Base):
    """数据集:版本化资产的容器;数据内容落在各 DatasetVersion 上。

    承载需求 #13(元信息)/ #15(分级分类)/ #19(归属与生命周期)。
    """

    __tablename__ = "datasets"

    # 主键形如 "dset-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # 接入/格式功能键(#2):text | multimodal | cot | qa | sql | image | csv-tsv 等。
    # 被数据接入页分栏过滤 + 媒体字段解析复用,保持 free-string,勿收紧(见 docs/plan/14)。
    data_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # 语义类型(#1/#2/#8,与 data_type 正交):10 类 LLM 语义之一(SemanticType 枚举,
    # 校验在 Pydantic 层),供统一语义展示/筛选。存量行为空(见 docs/plan/14 §3)。
    semantic_type: Mapped[str | None] = mapped_column(String, nullable=True)
    # 分级(#15):敏感级别,如 public | internal | confidential
    sensitivity_level: Mapped[str | None] = mapped_column(String, nullable=True)
    # 分类(#15):受控分类库引用 categories.id(无 FK,可空,单选);
    # 收口原自由填 business_category(已由迁移 0009 删列)。
    category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 归属(#19 共享/ACL);无 RBAC 前默认 admin
    owner: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # 创建人(#13,固化不变)
    creator: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # 最后变更人(#13,可变)
    last_modifier: Mapped[str | None] = mapped_column(String, nullable=True)
    # 有效期(#19 生命周期):到期清理
    valid_until: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
