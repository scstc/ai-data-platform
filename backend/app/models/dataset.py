"""数据集 ORM 模型(仓库重心,版本的容器)。"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _default_valid_until() -> datetime:
    """新建数据集默认有效期:生成时间 + 1 个自然月(#19 生命周期)。

    naive-UTC,与 job_runner/notifications 的 now 口径一致;
    日期按目标月末夹紧(1/31 → 2/28)。注意 SQLAlchemy 的列默认在
    flush 时值为 None 就生效——显式传 None 也会被填上,置空只能靠 UPDATE。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


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
    # 来源/接入方式(三轴拆分,取代 data_type 的来源语义):受控枚举
    # database | object_store | hdfs | local_upload | api_push;接入时确定。
    # 托管(origin=hosted)本期留空。设计见 docs/superpowers/specs §类型三轴拆分。
    source_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    # 原始格式(三轴拆分):接入时捕获的用户视角格式(txt/docx/csv/jsonl/image…),
    # 与 DatasetVersion.format(归一后存储格式,恒 jsonl)区分;数据库直连留空。
    source_format: Mapped[str | None] = mapped_column(String, nullable=True)
    # 分类(#15):受控分类库引用 categories.id(无 FK,可空,单选);
    # 收口原自由填 business_category(已由迁移 0009 删列)。
    category_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 归属(#19 共享/ACL);无 RBAC 前默认 admin
    owner: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # 创建人(#13,固化不变)
    creator: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # 最后变更人(#13,可变)
    last_modifier: Mapped[str | None] = mapped_column(String, nullable=True)
    # 有效期(#19 生命周期):到期清理;创建默认 +1 自然月,可在详情页改/清空
    valid_until: Mapped[datetime | None] = mapped_column(
        nullable=True, default=_default_valid_until
    )
    # 所属部门(RBAC 数据权限快照,创建时取创建人部门);存量回填为根部门
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 删除标记(#19 生命周期):过期扫描打标,普通接口一律不可见;
    # 恢复(清标+续期)仅超管经回收站。reason 目前仅 'expired'
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
