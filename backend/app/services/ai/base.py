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
    async def generate_pipeline(self, goal: str) -> dict[str, Any]:
        """据中文目标描述生成一条算子流水线，返回 GeneratedPipeline 形状 dict。

        operators 只能来自算子目录中 ready 状态的算子（经 sanitize_pipeline
        裁剪未知/不可执行算子与非法参数键），保证前端拿到的流水线可直接执行。
        """
        raise NotImplementedError

    @abstractmethod
    async def suggest_dataset_name(
        self, filenames: list[str], data_type: str, category: str | None
    ) -> dict[str, str]:
        """据文件名 / 格式 / 分类建议一个简洁中文数据集名，返回 {"name": str}。"""
        raise NotImplementedError

    @abstractmethod
    async def suggest_tags(
        self,
        name: str,
        description: str | None,
        category: str | None,
        data_type: str | None,
        existing_tags: list[str],
        known_tags: list[str],
    ) -> dict[str, Any]:
        """据数据集名称与元数据建议若干标签，返回 {"tags": [str]}。

        known_tags 为平台已有标签库（优先复用）；结果不含 existing_tags 中已有的。
        """
        raise NotImplementedError

    @abstractmethod
    async def moderate_texts(self, texts: list[str]) -> list[dict[str, Any]]:
        """内容安全审核(#4):对一批文本逐条分类。

        返回与 texts 等长、按下标对齐的列表,每元素
        {"flagged":bool, "category":str, "severity":str, "reason":str}。
        category 用英文枚举:porn|gambling|drugs|politics|terrorism|other。
        正常文本 flagged=False、category="other"。
        失败(超时/解析失败)由实现抛异常或返回空,交 review.py 降级处理。
        """
        raise NotImplementedError

    @abstractmethod
    async def judge_answers(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """裁判员(治理整改 G5):对比参考答案与模型回答打分。

        items=[{"prompt":str,"reference":str,"completion":str}],返回等长、按下标
        对齐的列表,每元素 {"score":int(0-100),"verdict":"pass"|"fail","reason":str}。
        失败(超时/解析失败)由实现抛异常或回退启发式,交 judge_runner 降级处理。
        """
        raise NotImplementedError
