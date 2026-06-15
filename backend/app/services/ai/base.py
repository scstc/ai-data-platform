"""AI 服务抽象基类。

定义三种 AI 能力的统一接口：Schema 推断、采集任务生成、固定问答。
返回值均为与前端契约（frontend/src/services/data-platform/typings.d.ts）一一对应的
camelCase dict，由上层 API 层直接透传给前端。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """AI 能力提供者抽象基类。

    所有方法均为异步，返回形状由前端 TS 类型定义：
    - infer_schema -> InferredSchema
    - generate_task -> GeneratedTaskConfig
    - qa -> {"answer": str}
    """

    @abstractmethod
    async def infer_schema(self, sample: str) -> dict[str, Any]:
        """根据样本数据推断 Schema，返回 InferredSchema 形状 dict。"""
        raise NotImplementedError

    @abstractmethod
    async def generate_task(self, prompt: str) -> dict[str, Any]:
        """根据自然语言描述生成采集任务配置，返回 GeneratedTaskConfig 形状 dict。"""
        raise NotImplementedError

    @abstractmethod
    async def qa(self, question: str) -> dict[str, str]:
        """回答平台使用相关问题，返回 {"answer": str}。"""
        raise NotImplementedError

    @abstractmethod
    async def generate_pipeline(
        self, goal: str, ready_ops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """据目标场景生成算子流水线,返回 GeneratedPipeline 形状 dict
        ({"operators": [{"name","params"}], "explanation": str})。
        ready_ops 为可选算子上下文(name/label/scenario/params)。"""
        raise NotImplementedError
