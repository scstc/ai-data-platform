"""LLM 配置解析：活跃提供商缓存 + 用量记录（best-effort）。

模块级缓存 _active 存放当前激活的 LLM 配置；启动时 / 激活操作后由
refresh_cache 刷新。所有消费方通过 get_active_llm_config() 读取，
不再直接引用 settings.openai_* 字段。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedLLMConfig:
    """解析后的 LLM 配置（不可变）。"""

    base_url: str | None
    api_key: str | None
    model: str


# 模块级活跃配置缓存；None 表示无激活提供商，回退到 env
_active: ResolvedLLMConfig | None = None


def get_active_llm_config() -> ResolvedLLMConfig:
    """同步读取当前活跃 LLM 配置（无 I/O）。

    有缓存则返回缓存；否则回退到 env 变量（与旧行为一致）。
    """
    if _active is not None:
        return _active
    # env 回退：与旧 settings.openai_* 语义完全一致
    return ResolvedLLMConfig(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_model or "gpt-4o-mini",
    )


def set_active_cache(cfg: ResolvedLLMConfig | None) -> None:
    """写入活跃缓存（激活 / 停用时调用）。"""
    global _active
    _active = cfg


async def refresh_cache(session: AsyncSession) -> None:
    """从数据库查询激活的 LLM 提供商并刷新缓存。

    找到 is_active=True 的第一条 → 更新缓存；
    没有 → 清空缓存（回退到 env 变量）。
    """
    from app.models.llm_provider import LlmProvider  # 延迟导入避免循环

    row = (
        await session.scalars(
            select(LlmProvider).where(LlmProvider.is_active.is_(True)).limit(1)
        )
    ).first()
    if row is not None:
        set_active_cache(
            ResolvedLLMConfig(
                base_url=row.base_url,
                api_key=row.api_key,
                model=row.model,
            )
        )
    else:
        set_active_cache(None)


async def record_usage(
    *,
    feature: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    success: bool,
    latency_ms: int,
    provider_id: str | None = None,
) -> None:
    """Best-effort 记录一条 LLM 调用用量。

    开独立 session 写入，任何异常均吞掉（记 warning），绝不影响主调用链。
    """
    from app.core.db import async_session_factory  # 延迟导入避免循环
    from app.models.llm_usage import LlmUsage, _new_llm_usage_id

    try:
        async with async_session_factory() as session:
            usage = LlmUsage(
                id=_new_llm_usage_id(),
                provider_id=provider_id,
                feature=feature,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                success=success,
                latency_ms=latency_ms,
            )
            session.add(usage)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort，不影响主流程
        logger.warning("record_usage 写入失败（已忽略）：%s", exc)
