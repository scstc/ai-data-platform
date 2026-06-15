"""OpenAI 兼容 LLM 提供者。

通过 httpx 调用 `{base_url}/chat/completions`，system prompt 强制只输出 JSON；
任何请求异常或 JSON 解析失败，一律回退到内部持有的 HeuristicProvider 对应方法，
并记 logging.warning，保证对外行为始终可用（前端契约不破）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.services.ai.base import AIProvider
from app.services.ai.heuristic import HeuristicProvider

logger = logging.getLogger(__name__)

# 三类能力的 system prompt，强约束「只输出 JSON、字段名与前端契约一致」
_SCHEMA_SYSTEM_PROMPT = (
    "你是数据接入助手。根据用户提供的样本数据推断其 Schema。"
    "只输出一个 JSON 对象，不要任何额外解释或 markdown 代码块。"
    'JSON 形状：{"format":string, "confidence":number(0~1), '
    '"fields":[{"name":string,"type":string,'
    '"example":string,"nullable":boolean}], '
    '"suggestion":string(中文), "recommendedConfig":object}。'
)
_TASK_SYSTEM_PROMPT = (
    "你是数据采集任务配置助手。根据用户的中文自然语言描述生成采集任务配置。"
    "只输出一个 JSON 对象，不要任何额外解释或 markdown 代码块。"
    'JSON 形状：{"name":string, '
    '"datasourceType":"s3"|"hdfs"|"database"|"api", '
    '"schedule":{"mode":"once"|"cron","cron"?:string}, '
    '"config":object, "explanation":string(中文)}。'
)
_QA_SYSTEM_PROMPT = (
    "你是 AI 数据平台的客服助手，用简洁中文回答用户关于数据源、文件格式、采集任务、"
    "cron 调度、测试连接的问题。只输出一个 JSON 对象："
    '{"answer":string(中文)}，不要任何额外解释或 markdown 代码块。'
)
_PIPELINE_SYSTEM_PROMPT = (
    "你是 data-juicer 数据加工流水线助手。用户给出加工目标,你只能从"
    "【可用算子清单】里选择算子,按合理顺序组成线性流水线。"
    "严格只输出 JSON:{\"operators\":[{\"name\":\"<算子名>\",\"params\":{}}],"
    "\"explanation\":\"<一句中文说明>\"}。"
    "name 必须是清单中的算子名;不确定参数就给 {};不要编造清单外的算子。"
)


class OpenAICompatProvider(AIProvider):
    """调用 OpenAI 兼容 Chat Completions 接口，失败回退启发式。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        heuristic: HeuristicProvider | None = None,
        timeout: float = 30.0,
    ) -> None:
        # base_url 末尾斜杠规范化，避免拼出 //chat/completions
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        # 内部持有启发式兜底实例
        self._heuristic = heuristic or HeuristicProvider()

    async def _chat_json(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        """调用 LLM 并解析其 JSON 输出；任一环节失败抛异常交由调用方回退。"""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 返回的 JSON 不是对象")
        return parsed

    async def infer_schema(self, sample: str) -> dict[str, Any]:
        try:
            return await self._chat_json(_SCHEMA_SYSTEM_PROMPT, sample)
        except Exception as exc:  # noqa: BLE001 — 任何失败都回退，保证可用性
            logger.warning("LLM infer_schema 失败，回退启发式：%s", exc)
            return await self._heuristic.infer_schema(sample)

    async def generate_task(self, prompt: str) -> dict[str, Any]:
        try:
            return await self._chat_json(_TASK_SYSTEM_PROMPT, prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM generate_task 失败，回退启发式：%s", exc)
            return await self._heuristic.generate_task(prompt)

    async def qa(self, question: str) -> dict[str, str]:
        try:
            result = await self._chat_json(_QA_SYSTEM_PROMPT, question)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM qa 失败，回退启发式：%s", exc)
            return await self._heuristic.qa(question)
        answer = result.get("answer")
        if not isinstance(answer, str):
            logger.warning("LLM qa 返回缺少 answer 字段，回退启发式")
            return await self._heuristic.qa(question)
        return {"answer": answer}

    async def generate_pipeline(
        self, goal: str, ready_ops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            catalog = "\n".join(
                f"- {o['name']} | {o['label']} | 场景:{o['scenario']} | "
                f"参数:{','.join(o['params']) or '无'}"
                for o in ready_ops
            )
            user = f"加工目标:{goal}\n\n【可用算子清单】\n{catalog}"
            return await self._chat_json(_PIPELINE_SYSTEM_PROMPT, user)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM generate_pipeline 失败，回退启发式：%s", exc)
            return await self._heuristic.generate_pipeline(goal, ready_ops)
