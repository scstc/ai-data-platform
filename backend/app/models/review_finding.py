"""内容审核命中记录 ORM 模型(#4)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ReviewFinding(Base):
    """审核任务逐条命中记录(可分页查、可按类别/来源/严重度过滤)。

    一次 review job 扫描被审版本(version_id),每命中一处违规落一条:
    哪一行(row_index)/什么类别(category)/多严重(severity)/哪一路检测(source)/
    命中详情(detail,如命中词、正则名、PII 类型、LLM 理由)/命中文本片段(snippet)。
    """

    __tablename__ = "review_findings"

    __table_args__ = (
        # 按 job 查全部命中(报告明细分页);按时间倒序看最新审核
        Index("ix_review_findings_job_id", "job_id"),
        Index("ix_review_findings_created_at", "created_at"),
    )

    # 主键形如 "rf-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 产生该命中的 review job
    job_id: Mapped[str] = mapped_column(String, nullable=False)
    # 被审版本 id(命中发生在该版本的第 row_index 行)
    version_id: Mapped[str] = mapped_column(String, nullable=False)
    # 命中行号(从 0 开始,与被审 jsonl 行对齐)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 类别:porn | gambling | drugs | politics | terrorism | pii | other
    category: Mapped[str] = mapped_column(String, nullable=False)
    # 严重度:high | medium | low
    severity: Mapped[str] = mapped_column(String, nullable=False)
    # 来源:keyword | regex | flagged_words | llm | pii
    source: Mapped[str] = mapped_column(String, nullable=False)
    # 命中详情:命中词 / 正则名 / PII 类型 / LLM 理由;可空
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 命中文本片段(截断 ~200 字);可空
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
