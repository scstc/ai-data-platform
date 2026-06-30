"""裁判任务编排(治理整改 G5):LLM-as-judge 对模型回答打分。

run_judge:load 待评版本 jsonl → 取 (prompt,reference,completion) → 裁判员打分 →
逐条写 eval_results → 聚合报告回写 job.eval_report。裁判失败降级(不 500)。
镜像 review_runner.run_review 的编排骨架。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.eval_result import EvalResult
from app.models.job import Job
from app.models.job_input import JobInput
from app.services.ai import get_ai_provider
from app.services.external_store import materialized_version
from app.services.review_runner import _read_jsonl

logger = logging.getLogger(__name__)


class JudgeError(RuntimeError):
    """裁判执行失败(数据文件缺失 / 待评版本无 completion 字段)。"""


def _new_result_id() -> str:
    return f"er-{secrets.token_hex(3)}"


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """把逐条结果聚合成 EvalReport 形状 dict(camelCase 由 schema 输出层处理)。"""
    total = len(results)
    scored = [r for r in results if r.get("score") is not None]
    avg = round(sum(r["score"] for r in scored) / len(scored), 2) if scored else None
    passed = sum(1 for r in results if r.get("verdict") == "pass")
    pass_rate = round(passed / total, 4) if total else None
    by_category: dict[str, dict[str, int]] = {}
    buckets = {"0-59": 0, "60-79": 0, "80-100": 0}
    for r in results:
        cat = r.get("category") or "未分类"
        c = by_category.setdefault(cat, {"total": 0, "pass": 0})
        c["total"] += 1
        if r.get("verdict") == "pass":
            c["pass"] += 1
        s = r.get("score")
        if s is not None:
            if s < 60:
                buckets["0-59"] += 1
            elif s < 80:
                buckets["60-79"] += 1
            else:
                buckets["80-100"] += 1
    return {
        "totalItems": total,
        "scoredItems": len(scored),
        "avgScore": avg,
        "passRate": pass_rate,
        "byCategory": by_category,
        "scoreBuckets": buckets,
    }


async def run_judge(
    session: AsyncSession,
    *,
    job: Job,
    version: DatasetVersion,
    config: dict[str, Any],
) -> Job:
    """对待评版本逐行裁判 → 写 eval_results + 回写 job.eval_report。

    待评版本须每行含 completion 字段(模型回答);全行缺失 → JudgeError(Fail loud,
    不静默落全 unscored)。裁判 provider 失败 → 该批 unscored + warnings(降级不 500)。
    """
    prompt_f = config.get("prompt_field", "prompt")
    ref_f = config.get("reference_field", "response")
    comp_f = config.get("completion_field", "completion")
    cat_f = config.get("category_field", "category")
    pass_score = int(config.get("pass_score", 60))
    sample_limit = config.get("sample_limit")
    use_llm = config.get("use_llm", True)

    async with materialized_version(version, session) as src_path:
        if not src_path.exists():
            raise JudgeError(f"待评版本数据文件不存在:{version.storage_uri}")
        rows = _read_jsonl(src_path)

    if sample_limit:
        rows = rows[: int(sample_limit)]

    # Fail loud:全行无 completion 字段 → 无从裁判
    if rows and all(comp_f not in r for r in rows):
        raise JudgeError(
            f"待评版本无 {comp_f!r} 字段,无法裁判;请先 join 模型回答(completion)"
        )

    warnings: list[str] = []
    items: list[dict[str, Any]] = [
        {
            "prompt": str(r.get(prompt_f, "")),
            "reference": str(r.get(ref_f, "")),
            "completion": str(r.get(comp_f, "")),
        }
        for r in rows
    ]

    # use_llm=False → 强制启发式;否则按活跃 LLM 配置(get_ai_provider 无 LLM 时
    # 本就返回 HeuristicProvider,OpenAICompatProvider 调用失败也会自回退启发式)。
    if use_llm:
        provider = get_ai_provider(settings)
        from app.services.ai import HeuristicProvider as _Heur

        if isinstance(provider, _Heur):
            warnings.append("未配置 LLM,裁判降级为启发式相似度")
    else:
        from app.services.ai import HeuristicProvider

        provider = HeuristicProvider()
        warnings.append("未启用 LLM,裁判使用启发式相似度")

    try:
        judgments = await provider.judge_answers(items)
    except Exception as exc:  # noqa: BLE001 — 降级不 500
        logger.warning("judge_answers 失败,全部置 unscored:%s", exc)
        warnings.append(f"裁判调用失败,全部未评分:{exc}")
        judgments = [
            {"score": None, "verdict": "unscored", "reason": ""} for _ in items
        ]

    results_for_report: list[dict[str, Any]] = []
    for idx, (row, item, judg) in enumerate(zip(rows, items, judgments, strict=False)):
        score = judg.get("score")
        verdict = judg.get("verdict") or "unscored"
        # completion 缺失的行标 unscored(单行降级,不影响整体)
        if comp_f not in row:
            score, verdict = None, "unscored"
        category = row.get(cat_f)
        session.add(
            EvalResult(
                id=_new_result_id(),
                job_id=job.id,
                version_id=version.id,
                row_index=idx,
                prompt=item["prompt"],
                reference=item["reference"],
                completion=item["completion"],
                score=score,
                verdict=verdict,
                category=category,
                reason=judg.get("reason"),
            )
        )
        results_for_report.append(
            {"score": score, "verdict": verdict, "category": category}
        )

    report = _aggregate(results_for_report)
    report["warnings"] = warnings
    report["passScore"] = pass_score
    job.eval_report = report
    session.add(JobInput(job_id=job.id, dataset_version_id=version.id))
    await session.commit()
    await session.refresh(job)
    return job
