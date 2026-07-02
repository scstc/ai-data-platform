"""内容安全自定义规则库 ORM 模型。

一行 = 一条可复用的审核规则(敏感词或正则)。建审核任务时按选择(或全部启用项)
冻结进 job.spec;上传前置预检自动合并全部启用项。enabled=false 即下线不删除。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ReviewRule(Base):
    """一条自定义审核规则(敏感词 kind=word / 正则 kind=regex)。"""

    __tablename__ = "review_rules"

    __table_args__ = (Index("ix_review_rules_enabled", "enabled"),)

    # 主键形如 "rr-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 规则名(展示用;word 规则可与 pattern 相同)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # word:pattern 作子串(大小写不敏感);regex:pattern 作正则
    kind: Mapped[str] = mapped_column(String, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    # 命中归入的类别:porn | gambling | drugs | politics | terrorism | pii | other
    category: Mapped[str] = mapped_column(
        String, nullable=False, server_default="other"
    )
    # 命中判定的严重度:high | medium | low
    severity: Mapped[str] = mapped_column(
        String, nullable=False, server_default="medium"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
