"""评估数据集落地服务(治理整改 G4):评估集 {prompt,response} + ≥300 硬指标。

≥300 是需求硬指标(规范 §3.7):确定性 code 校验、Fail loud(Rule 12),
绝不丢给 LLM 判断。落地经统一出口 land_records,打 semantic_type/train_type='eval'。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.services.landing import (
    LandingError,
    _stamp_lineage,
    land_records,
    normalize_to_records,
)


class EvalValidationError(LandingError):
    """评估集不满足硬指标(条数 < 阈值);由路由转 422。"""


def validate_eval_records(records: list[dict], *, min_count: int) -> None:
    """评估集条数硬校验(Fail loud):不足 min_count 即抛 EvalValidationError。"""
    if len(records) < min_count:
        raise EvalValidationError(
            f"评估数据集需 ≥{min_count} 条(规范 §3.7 硬指标),当前仅 {len(records)} 条"
        )


async def land_eval_dataset(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    source_format: str,
    dataset_name: str | None = None,
    description: str | None = None,
    creator: str = "admin",
) -> tuple[Dataset, DatasetVersion]:
    """解析 → 注入血缘 → ≥300 校验 → 落地为 eval 数据集。

    semantic_type='eval'(strict 逐行校验 prompt/response 非空),
    train_type/schema_variant='eval'。任一校验失败抛(Landing/Semantic/Eval)Error。
    """
    import hashlib
    from datetime import UTC, datetime

    records = normalize_to_records(content, source_format)
    _stamp_lineage(
        records,
        source_file=filename,
        doc_id=f"sha256:{hashlib.sha256(content).hexdigest()}",
        ingest_batch=datetime.now(UTC).isoformat(),
    )
    # ≥300 硬指标:确定性 Fail-loud,落地之前拒绝
    validate_eval_records(records, min_count=settings.eval_min_records)
    return await land_records(
        session,
        records,
        dataset_name=dataset_name or "评估数据集",
        semantic_type="eval",
        strict_semantic=True,
        train_type="eval",
        schema_variant="eval",
        source_kind="eval_upload",
        source_format=source_format.lower(),
        description=description,
        note=f"评估集落地:{filename}",
        creator=creator,
    )
