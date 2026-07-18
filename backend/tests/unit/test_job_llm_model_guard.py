"""跨供应商模型拦截(jobs._cross_provider_model_block)单测。

为什么:任务级 LLM 凭证是单例——engine 只注入生效供应商的 key,llm-proxy 只
转发到快照里的一个 base_url。算子若显式选了其他供应商清单里的模型,运行期
必然 401/模型不存在,必须在建任务时拦截。同时不可误伤:api_or_hf_model 的
合法值可以是本地 HF 模型(不在任何供应商清单),自由输入的未知名称也放行。
"""

from __future__ import annotations

import pytest

from app.api.v1.jobs import _cross_provider_model_block
from app.models.llm_model import LlmModel
from app.models.llm_provider import LlmProvider
from app.schemas.job import OperatorSpec


def _op(**params) -> OperatorSpec:
    return OperatorSpec(name="generate_qa_from_text_mapper", params=params or None)


@pytest.mark.asyncio
async def test_cross_provider_model_block(db_session) -> None:
    """选非生效供应商的模型 → 400;生效供应商/本地/未知模型 → 放行。"""
    # 库里无供应商(纯 env 回退)→ 无从判定归属,放行
    assert (
        await _cross_provider_model_block(db_session, [_op(api_model="whatever")])
        is None
    )

    db_session.add_all(
        [
            LlmProvider(
                id="llm-a",
                name="DeepSeek",
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-a",
                model="deepseek-chat",
                is_active=True,
            ),
            LlmProvider(
                id="llm-b",
                name="硅基流动",
                provider="siliconflow",
                base_url="https://api.siliconflow.cn/v1",
                api_key="sk-b",
                model="Qwen/Qwen2.5-72B-Instruct",
                is_active=False,
            ),
            LlmModel(id="lmd-a1", provider_id="llm-a", model="deepseek-reasoner"),
            LlmModel(
                id="lmd-b1", provider_id="llm-b", model="Qwen/Qwen2.5-72B-Instruct"
            ),
            # 两家同名模型:生效方清单里也有 → 不算跨供应商
            LlmModel(id="lmd-a2", provider_id="llm-a", model="shared-model"),
            LlmModel(id="lmd-b2", provider_id="llm-b", model="shared-model"),
        ]
    )
    await db_session.commit()

    # 选了非生效供应商(硅基流动)清单里的模型 → 400,报文说清两边供应商
    resp = await _cross_provider_model_block(
        db_session, [_op(api_model="Qwen/Qwen2.5-72B-Instruct")]
    )
    assert resp is not None and resp.status_code == 400
    body = resp.body.decode()
    assert "硅基流动" in body and "DeepSeek" in body

    # 生效供应商清单内的模型 / 其当前生效模型 → 放行
    assert (
        await _cross_provider_model_block(
            db_session, [_op(api_model="deepseek-reasoner")]
        )
        is None
    )
    assert (
        await _cross_provider_model_block(
            db_session, [_op(api_model="deepseek-chat")]
        )
        is None
    )

    # 两家同名模型(生效方也有)→ 放行
    assert (
        await _cross_provider_model_block(db_session, [_op(api_model="shared-model")])
        is None
    )

    # 本地 HF 模型 / 自由输入(不在任何供应商清单)→ 放行,不误伤
    assert (
        await _cross_provider_model_block(
            db_session, [_op(api_or_hf_model="Qwen/Qwen2-0.5B")]
        )
        is None
    )

    # 未填模型参数 → 放行
    assert await _cross_provider_model_block(db_session, [_op()]) is None
