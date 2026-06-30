"""评估裁判逐条打分记录 ORM 模型(治理整改 G5)。

一次 judge job 对待评版本逐行(prompt/reference/completion)调裁判员打分,
每行落一条:得分(score)、判定(verdict)、理由(reason)。镜像 ReviewFinding 结构。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EvalResult(Base):
    """裁判任务逐条打分(可分页查、可按 verdict/category 过滤)。

    score 为空表示该条未评分(unscored:LLM 失败降级或缺 completion)。
    verdict:pass | fail | unscored。
    """

    __tablename__ = "eval_results"

    __table_args__ = (
        Index("ix_eval_results_job_id", "job_id"),
        Index("ix_eval_results_created_at", "created_at"),
    )

    # 主键形如 "er-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 产生该结果的 judge job
    job_id: Mapped[str] = mapped_column(String, nullable=False)
    # 被评版本 id(打分发生在该版本的第 row_index 行)
    version_id: Mapped[str] = mapped_column(String, nullable=False)
    # 行号(从 0 开始,与被评 jsonl 行对齐)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 评测问题
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # 参考答案(数据集里的标准答案)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    # 模型回答(被评对象)
    completion: Mapped[str] = mapped_column(Text, nullable=False)
    # 得分 0-100;unscored 时为空
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 判定:pass | fail | unscored
    verdict: Mapped[str] = mapped_column(String, nullable=False)
    # 评估维度/类别(便于分维度统计);可空
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    # 裁判理由;可空
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
