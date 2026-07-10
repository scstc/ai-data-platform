"""LLM 提供商配置 ORM 模型。"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import Boolean, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _new_llm_provider_id() -> str:
    """生成形如 llm-<6位hex> 的主键。"""
    return f"llm-{secrets.token_hex(3)}"


class LlmProvider(Base):
    """LLM 提供商配置：deepseek / glm / minimax / openai / siliconflow / custom。"""

    __tablename__ = "llm_providers"

    # 主键形如 "llm-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # 提供商品牌：deepseek | glm | minimax | openai | siliconflow | custom
    provider: Mapped[str] = mapped_column(String, nullable=False)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    api_key: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    # 同一时刻至多一条记录为 True（激活路由负责保证）
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
