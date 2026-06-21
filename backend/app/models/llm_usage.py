"""LLM 调用用量记录 ORM 模型。"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _new_llm_usage_id() -> str:
    """生成形如 llu-<6位hex> 的主键。"""
    return f"llu-{secrets.token_hex(3)}"


class LlmUsage(Base):
    """LLM 调用用量记录：按特性/模型/token 数追踪每次调用。"""

    __tablename__ = "llm_usage"

    # 主键形如 "llu-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 关联的提供商 id（可空：env 回退时无 provider 记录）
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 调用特性：infer_schema | generate_task | qa | generate_pipeline | moderate | chat
    feature: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
