"""OpenAI 兼容 LLM 提供者。

通过 httpx 调用 `{base_url}/chat/completions`，system prompt 强制只输出 JSON；
任何请求异常或 JSON 解析失败，一律回退到内部持有的 HeuristicProvider 对应方法，
并记 logging.warning，保证对外行为始终可用（前端契约不破）。

每次 _chat_json / _moderate_batch 调用后 best-effort 写一条 llm_usage 记录（
token 数 + 延迟 + 成功/失败），异常全部吞掉，不影响主流程。
"""

from __future__ import annotations

import json
import logging
import time
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
_SUGGEST_NAME_SYSTEM_PROMPT = (
    "你是数据集命名助手。根据用户给出的文件名列表、数据格式、可选分类，"
    "起一个简洁、概括性的中文数据集名称（不超过 20 字，不带文件扩展名、不带引号）。"
    '只输出一个 JSON 对象：{"name":string(中文)}，不要任何额外解释或 markdown 代码块。'
)
_SUGGEST_TAGS_SYSTEM_PROMPT = (
    "你是数据集打标助手。根据数据集名称、描述、分类、数据类型等元数据,"
    "给出 3~5 个简洁的中文标签(每个不超过 8 字,名词短语,不带 # 与引号)。"
    "优先复用「已有标签库」中语义匹配的标签,不足再新造;不要输出「已有标签」中已存在的。"
    '只输出一个 JSON 对象:{"tags":[string]},不要任何额外解释或 markdown 代码块。'
)
# 内容安全审核(#4):分批把若干文本分类为 黄/赌/毒/政/恐 或 正常。
_MODERATE_SYSTEM_PROMPT = (
    "你是内容安全审核助手。给定带编号的若干文本,逐条判断是否含违规内容,"
    "并分类为:porn(黄/色情)、gambling(赌/博彩)、drugs(毒/毒品)、"
    "politics(政/违规政治)、terrorism(恐/暴恐),正常文本归为 other。"
    "严格只输出一个 JSON 对象:"
    '{"results":[{"index":<编号>,"flagged":<bool>,'
    '"category":"porn|gambling|drugs|politics|terrorism|other",'
    '"severity":"high|medium|low","reason":"<简短中文理由>"}]}。'
    "results 必须覆盖每个输入编号;正常文本 flagged=false、category=other。"
    "不要任何额外解释或 markdown 代码块。"
)
# 单批文本条数(控制单次提示长度与时延)
_MODERATE_BATCH = 20
# 审核单批超时(秒):比通用 timeout 略宽,但仍有界,失败即降级
_MODERATE_TIMEOUT = 60.0

# 裁判员(治理整改 G5):对比参考答案与模型回答打分
_JUDGE_SYSTEM_PROMPT = (
    "你是严格的评估裁判员。给定每条的问题(prompt)、参考答案(reference)、"
    "模型回答(completion),判断模型回答相对参考答案的质量,按 0-100 打分"
    "(100=完全正确且完整,0=完全错误/答非所问)。"
    "严格只输出一个 JSON 对象:"
    '{"results":[{"index":<编号>,"score":<0-100整数>,'
    '"verdict":"pass|fail","reason":"<简短中文理由>"}]}。'
    "results 必须覆盖每个输入编号。不要任何额外解释或 markdown 代码块。"
)
# 裁判单批条数(提示比审核更长,批小一些)
_JUDGE_BATCH = 10
# 裁判单批超时(秒)
_JUDGE_TIMEOUT = 60.0
# 合法 category 枚举(LLM 越界回退 other)
_MODERATE_CATEGORIES = {
    "porn",
    "gambling",
    "drugs",
    "politics",
    "terrorism",
    "other",
}
_MODERATE_SEVERITIES = {"high", "medium", "low"}


def _parse_json_content(content: str | None) -> Any:
    """宽松解析 LLM 返回的 JSON:剥 markdown 栅栏/前后杂讯后再 loads。

    推理型模型(如 deepseek-v4-flash)偶发在 JSON 外带说明文字、代码栅栏或
    返回空 content;直接 json.loads 报 "Expecting value: char 0",一批失败会让
    整个 LLM 检测降级。这里先原文解析,失败再提取首个 JSON 对象/数组子串;
    仍失败按原样抛(空 content 给出明确报错)——调用方各自降级,不伪造结果。
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("LLM 返回空 content(推理输出耗尽限额或被截断)")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        end = max(text.rfind("}"), text.rfind("]"))
        if not starts or end <= min(starts):
            raise
        return json.loads(text[min(starts) : end + 1])


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

    async def _chat_json(
        self, system_prompt: str, user_content: str, *, feature: str = "chat"
    ) -> dict[str, Any]:
        """调用 LLM 并解析其 JSON 输出；任一环节失败抛异常交由调用方回退。

        同时 best-effort 记录一条 llm_usage（成功/失败 + token + 延迟）。
        """
        from app.services.llm_config import record_usage  # 延迟导入避免循环

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
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            t1 = time.monotonic()
            usage = data.get("usage") or {}
            await record_usage(
                feature=feature,
                model=self._model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                success=True,
                latency_ms=int((t1 - t0) * 1000),
            )
        except Exception:
            t1 = time.monotonic()
            await record_usage(
                feature=feature,
                model=self._model,
                prompt_tokens=0,
                completion_tokens=0,
                success=False,
                latency_ms=int((t1 - t0) * 1000),
            )
            raise
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json_content(content)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 返回的 JSON 不是对象")
        return parsed

    async def infer_schema(self, sample: str) -> dict[str, Any]:
        try:
            return await self._chat_json(
                _SCHEMA_SYSTEM_PROMPT, sample, feature="infer_schema"
            )
        except Exception as exc:  # noqa: BLE001 — 任何失败都回退，保证可用性
            logger.warning("LLM infer_schema 失败，回退启发式：%s", exc)
            return await self._heuristic.infer_schema(sample)

    async def generate_task(self, prompt: str) -> dict[str, Any]:
        try:
            return await self._chat_json(
                _TASK_SYSTEM_PROMPT, prompt, feature="generate_task"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM generate_task 失败，回退启发式：%s", exc)
            return await self._heuristic.generate_task(prompt)

    async def qa(self, question: str) -> dict[str, str]:
        try:
            result = await self._chat_json(_QA_SYSTEM_PROMPT, question, feature="qa")
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM qa 失败，回退启发式：%s", exc)
            return await self._heuristic.qa(question)
        answer = result.get("answer")
        if not isinstance(answer, str):
            logger.warning("LLM qa 返回缺少 answer 字段，回退启发式")
            return await self._heuristic.qa(question)
        return {"answer": answer}

    async def suggest_dataset_name(
        self, filenames: list[str], data_type: str, category: str | None
    ) -> dict[str, str]:
        user = (
            f"数据格式：{data_type}\n"
            f"分类：{category or '(无)'}\n"
            f"文件名：{', '.join(filenames) or '(无)'}"
        )
        try:
            result = await self._chat_json(
                _SUGGEST_NAME_SYSTEM_PROMPT, user, feature="suggest_name"
            )
        except Exception as exc:  # noqa: BLE001 — 任何失败都回退，保证可用性
            logger.warning("LLM suggest_dataset_name 失败，回退启发式：%s", exc)
            return await self._heuristic.suggest_dataset_name(
                filenames, data_type, category
            )
        name = result.get("name")
        if not isinstance(name, str) or not name.strip():
            logger.warning("LLM suggest_dataset_name 返回缺少 name 字段，回退启发式")
            return await self._heuristic.suggest_dataset_name(
                filenames, data_type, category
            )
        return {"name": name.strip()}

    async def suggest_tags(
        self,
        name: str,
        description: str | None,
        category: str | None,
        data_type: str | None,
        existing_tags: list[str],
        known_tags: list[str],
    ) -> dict[str, Any]:
        user = (
            f"名称：{name}\n"
            f"描述：{description or '(无)'}\n"
            f"分类：{category or '(无)'}\n"
            f"数据类型：{data_type or '(无)'}\n"
            f"已有标签：{', '.join(existing_tags) or '(无)'}\n"
            f"已有标签库：{', '.join(known_tags[:100]) or '(无)'}"
        )
        try:
            result = await self._chat_json(
                _SUGGEST_TAGS_SYSTEM_PROMPT, user, feature="suggest_tags"
            )
        except Exception as exc:  # noqa: BLE001 — 任何失败都回退，保证可用性
            logger.warning("LLM suggest_tags 失败，回退启发式：%s", exc)
            return await self._heuristic.suggest_tags(
                name, description, category, data_type, existing_tags, known_tags
            )
        raw = result.get("tags")
        if not isinstance(raw, list):
            logger.warning("LLM suggest_tags 返回缺少 tags 数组，回退启发式")
            return await self._heuristic.suggest_tags(
                name, description, category, data_type, existing_tags, known_tags
            )
        # 去空/去重/剔除已有标签,防 LLM 越界
        existing = set(existing_tags)
        tags: list[str] = []
        for item in raw:
            t = str(item).strip().strip("#").strip()
            if t and t not in existing and t not in tags:
                tags.append(t)
        return {"tags": tags[:8]}

    async def _moderate_batch(self, batch: list[str]) -> list[dict[str, Any]]:
        """审核一批文本(<=_MODERATE_BATCH 条),返回与 batch 等长、按下标对齐的结果。

        任一环节失败(请求异常/超时/解析失败/缺编号)直接抛,交 moderate_texts 处理。
        同时 best-effort 记录用量（feature='moderate'）。
        """
        from app.services.llm_config import record_usage  # 延迟导入避免循环

        numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(batch))
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _MODERATE_SYSTEM_PROMPT},
                {"role": "user", "content": numbered},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            # 推理型模型先产 reasoning 再产 content;默认输出限额可能被推理
            # 耗尽致 content 为空(整批降级),显式给足上限。
            "max_tokens": 8192,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_MODERATE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            t1 = time.monotonic()
            usage = data.get("usage") or {}
            await record_usage(
                feature="moderate",
                model=self._model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                success=True,
                latency_ms=int((t1 - t0) * 1000),
            )
        except Exception:
            t1 = time.monotonic()
            await record_usage(
                feature="moderate",
                model=self._model,
                prompt_tokens=0,
                completion_tokens=0,
                success=False,
                latency_ms=int((t1 - t0) * 1000),
            )
            raise
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json_content(content)
        results = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(results, list):
            raise ValueError("LLM moderate 返回缺少 results 数组")

        # 按 index 归位;缺失的编号在 moderate_texts 兜底为正常,确保等长对齐
        by_index: dict[int, dict[str, Any]] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or not 0 <= idx < len(batch):
                continue
            by_index[idx] = item
        return [self._normalize_verdict(by_index.get(i)) for i in range(len(batch))]

    @staticmethod
    def _normalize_verdict(item: dict[str, Any] | None) -> dict[str, Any]:
        """规整单条裁决:越界 category/severity 收敛,缺失项当正常。"""
        if not item:
            return {
                "flagged": False,
                "category": "other",
                "severity": "low",
                "reason": "",
            }
        flagged = bool(item.get("flagged"))
        category = item.get("category")
        if category not in _MODERATE_CATEGORIES:
            category = "other"
        severity = item.get("severity")
        if severity not in _MODERATE_SEVERITIES:
            severity = "medium" if flagged else "low"
        reason = item.get("reason")
        return {
            "flagged": flagged,
            "category": category if flagged else "other",
            "severity": severity,
            "reason": str(reason) if reason is not None else "",
        }

    async def moderate_texts(self, texts: list[str]) -> list[dict[str, Any]]:
        """分批审核全部文本(每批 ~_MODERATE_BATCH 条),返回等长对齐结果。

        任一批失败即整体抛异常 —— 由 review.py 捕获后【整体跳过 llm source】(降级),
        其余 source 仍出结果,绝不让审核任务 500。
        """
        if not texts:
            return []
        out: list[dict[str, Any]] = []
        for start in range(0, len(texts), _MODERATE_BATCH):
            batch = texts[start : start + _MODERATE_BATCH]
            out.extend(await self._moderate_batch(batch))
        return out

    @staticmethod
    def _normalize_judgment(item: dict[str, Any] | None) -> dict[str, Any]:
        """规整单条裁判:score clamp 到 [0,100],verdict 越界按 score 兜底。"""
        if not isinstance(item, dict):
            return {"score": None, "verdict": "unscored", "reason": "LLM 未返回该条"}
        raw_score = item.get("score")
        score: int | None
        try:
            score = max(0, min(100, int(raw_score)))
        except (TypeError, ValueError):
            score = None
        verdict = item.get("verdict")
        if verdict not in ("pass", "fail"):
            verdict = (
                "unscored" if score is None else ("pass" if score >= 60 else "fail")
            )
        reason = item.get("reason")
        return {
            "score": score,
            "verdict": verdict,
            "reason": str(reason) if reason is not None else "",
        }

    async def _judge_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """裁判一批(<=_JUDGE_BATCH 条),返回与 batch 等长、按下标对齐的结果。

        任一环节失败直接抛,交 judge_answers 处理(降级回退启发式)。
        """
        from app.services.llm_config import record_usage  # 延迟导入避免循环

        numbered = "\n".join(
            f"[{i}] 问题:{it.get('prompt', '')}\n参考答案:{it.get('reference', '')}\n"
            f"模型回答:{it.get('completion', '')}"
            for i, it in enumerate(batch)
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": numbered},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_JUDGE_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            t1 = time.monotonic()
            usage = data.get("usage") or {}
            await record_usage(
                feature="judge",
                model=self._model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                success=True,
                latency_ms=int((t1 - t0) * 1000),
            )
        except Exception:
            t1 = time.monotonic()
            await record_usage(
                feature="judge",
                model=self._model,
                prompt_tokens=0,
                completion_tokens=0,
                success=False,
                latency_ms=int((t1 - t0) * 1000),
            )
            raise
        content = data["choices"][0]["message"]["content"]
        parsed = _parse_json_content(content)
        results = parsed.get("results") if isinstance(parsed, dict) else parsed
        if not isinstance(results, list):
            raise ValueError("LLM judge 返回缺少 results 数组")
        by_index: dict[int, dict[str, Any]] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or not 0 <= idx < len(batch):
                continue
            by_index[idx] = item
        return [self._normalize_judgment(by_index.get(i)) for i in range(len(batch))]

    async def judge_answers(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """分批裁判全部条目;任一批失败整体回退启发式(降级,不让 job 500)。"""
        if not items:
            return []
        try:
            out: list[dict[str, Any]] = []
            for start in range(0, len(items), _JUDGE_BATCH):
                batch = items[start : start + _JUDGE_BATCH]
                out.extend(await self._judge_batch(batch))
            return out
        except Exception as exc:  # noqa: BLE001 — 任何失败都回退,保证可用性
            logger.warning("LLM judge_answers 失败,回退启发式:%s", exc)
            return await self._heuristic.judge_answers(items)
