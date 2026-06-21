"""AI 双模式服务层。

对外暴露 AIProvider 抽象与 get_ai_provider 工厂：
- 活跃 LLM 配置（DB 优先 → env 回退）同时有 base_url 与 api_key → OpenAICompatProvider
  （内置启发式兜底）
- 否则 → HeuristicProvider（纯本地，零外部依赖）

工厂签名保留 settings 参数（兼容调用方），但实际配置从 get_active_llm_config() 读取。
"""

from __future__ import annotations

from typing import Any

from app.services.ai.base import AIProvider
from app.services.ai.heuristic import HeuristicProvider
from app.services.ai.llm import OpenAICompatProvider

__all__ = [
    "AIProvider",
    "HeuristicProvider",
    "OpenAICompatProvider",
    "get_ai_provider",
]


def get_ai_provider(settings: Any) -> AIProvider:  # noqa: ARG001 — settings 保留兼容签名
    """根据活跃 LLM 配置选择 AI 提供者。

    从 get_active_llm_config() 读取（DB 激活优先，回退 env），
    base_url + api_key 均非空时启用 LLM（失败自动回退启发式），
    否则使用启发式提供者。
    """
    from app.services.llm_config import get_active_llm_config  # 延迟导入避免循环

    cfg = get_active_llm_config()
    if cfg.base_url and cfg.api_key:
        return OpenAICompatProvider(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            model=cfg.model,
            heuristic=HeuristicProvider(),
        )
    return HeuristicProvider()
