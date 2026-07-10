"""系统默认模型 ORM 模型（按能力位，Dify 风格「系统模型设置」）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# 能力位固定枚举：系统推理 / Embedding / Rerank / 语音转文本 / 文本转语音
CAPABILITIES = ("chat", "embedding", "rerank", "speech2text", "tts")


class LlmSystemModel(Base):
    """按能力位记录系统默认模型，每个能力位至多一行。

    chat 位与 llm_providers.is_active 联动（保存时由 API 层同步激活并
    写 provider.model，兼容既有运行时消费方）；其余能力位平台暂无消费方，
    先落库供「系统模型设置」界面与后续 RAG / 语音等场景使用。
    无 DB 级外键（沿用 llm_models.provider_id 的弱关联约定），
    provider 删除时由应用层清理对应行。
    """

    __tablename__ = "llm_system_models"

    # 能力位：chat | embedding | rerank | speech2text | tts
    capability: Mapped[str] = mapped_column(String, primary_key=True)
    # 所属提供商 id（llm_providers.id），应用层维护关联
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    # 模型 id 字符串，如 "BAAI/bge-m3"
    model: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
