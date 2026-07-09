"""LLM 用量代理:dj-process 算子的 LLM 调用经此转发,平台得以统计算子/任务级用量。

engine 起子进程时把 OPENAI_BASE_URL 指到 ``/api/v1/llm-proxy/{job_id}``,
needs_api 算子的 openai 客户端即向本端点发 ``/chat/completions``;代理把请求
原样转发到 LLM 配置页生效端点(透传调用方 Authorization,本端点不存 Key、
不校验平台登录态——没有有效 Key 上游自会 401),从响应 usage 解析 token 数
并 best-effort 记入 llm_usage(feature='operator',关联 job_id)。
"""

from __future__ import annotations

import json
import time
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.job import Job
from app.services.llm_config import record_usage, resolve_llm_config

router = APIRouter(tags=["llm-proxy"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# LLM 生成调用可能很慢(长文本/大批次),超时给足;连接失败快速失败
_TIMEOUT = httpx.Timeout(600.0, connect=10.0)


@router.post("/llm-proxy/{job_id}/chat/completions")
async def proxy_chat_completions(
    job_id: str, request: Request, session: SessionDep
) -> Response:
    """转发 chat/completions 到生效 LLM 端点并记录用量。

    转发目标按 job.spec["llm_snapshot"] 解析(resolve_llm_config):有快照则
    锁定该任务执行时固化的 base_url,真正做到"复现即回原端点";job 不存在 /
    spec 无该键(老任务、未走 job_id 的路径)时快照为 None,等价现取活跃配置,
    行为不变。
    """
    job = await session.get(Job, job_id)
    snapshot = (job.spec or {}).get("llm_snapshot") if job is not None else None
    cfg = resolve_llm_config(snapshot)
    base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
    body = await request.body()
    headers = {"Content-Type": "application/json"}
    if auth := request.headers.get("authorization"):
        headers["Authorization"] = auth
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/chat/completions", content=body, headers=headers
            )
    except httpx.HTTPError as exc:
        await record_usage(
            feature="operator",
            model=_model_from_request(body),
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            latency_ms=int((time.monotonic() - t0) * 1000),
            job_id=job_id,
        )
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"LLM 上游不可达:{exc}"}},
        )
    latency_ms = int((time.monotonic() - t0) * 1000)
    model = ""
    prompt_tokens = completion_tokens = 0
    content_type = resp.headers.get("content-type", "application/json")
    # 流式(SSE)响应无 usage 字段,只记调用不记 token;JSON 响应解析 usage
    if "application/json" in content_type:
        try:
            data = resp.json()
            model = str(data.get("model") or "")
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except ValueError:
            pass
    await record_usage(
        feature="operator",
        model=model or _model_from_request(body),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        success=resp.status_code == 200,
        latency_ms=latency_ms,
        job_id=job_id,
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=content_type,
    )


def _model_from_request(body: bytes) -> str:
    """上游异常/响应无 model 时,从请求体兜底取模型名(仅用于用量记录)。"""
    try:
        return str(json.loads(body).get("model") or "")
    except (ValueError, AttributeError):
        return ""
