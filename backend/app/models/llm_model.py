"""LLM 供应商下的可用模型 ORM 模型。"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _new_llm_model_id() -> str:
    """生成形如 lmd-<6位hex> 的主键。"""
    return f"lmd-{secrets.token_hex(3)}"


class LlmModel(Base):
    """某个 LLM 提供商下的一个可用模型。

    通过「获取模型」拉取(source=fetched)或手动添加(source=manual)。
    「当前生效模型」仍由 llm_providers.model 决定,本表只做可选清单。
    无 DB 级外键(沿用 llm_usage.provider_id 的弱关联约定),
    provider 删除时由应用层清理对应行。
    """

    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model", name="uq_llm_models_provider_model"),
        Index("ix_llm_models_provider_id", "provider_id"),
    )

    # 主键形如 "lmd-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 所属提供商 id（llm_providers.id），应用层维护关联与级联删除
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    # 模型 id 字符串，如 "deepseek-v4-flash"
    model: Mapped[str] = mapped_column(String, nullable=False)
    # 来源：fetched（接口拉取）| manual（手动添加）
    source: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
