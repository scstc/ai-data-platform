"""数据增强(augment)执行引擎:走 data-juicer LLM Mapper 链,产物落新版本。

与 ``services/make.py`` 的差异:本模块专管 LLM 改写已有数据(1→1 改写/优化/校准/打标)。
引擎代码与 make 100% 相同(都是 ``run_process_job``),仅算子白名单与报告标识不同。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.schemas.augment import AugmentGoal, AugmentReport
from app.services.engine import (
    EngineError,
    _new_version_id,
    _run_dj,
    build_config,
    materialized_version,
)


async def run_augment_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
    goal: AugmentGoal,
    output_dataset_id: str | None = None,
) -> tuple[DatasetVersion, str, str, AugmentReport]:
    """对输入版本跑增强算子链 → 写回 dataset 新版本。"""
    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise EngineError(f"输出数据集不存在:{dataset_id}")

    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.jsonl"
    yaml_path = out_dir / "job.yaml"
    log_path = out_dir / "run.log"
    report_path = out_dir / "report.json"

    started = time.time()
    operator_chain = [op["name"] for op in operators]

    async with materialized_version(input_version, session) as input_path:
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(out_path),
            operators=operators,
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        input_count = sum(1 for line in Path(input_path).open(encoding="utf-8") if line.strip())
        code, log = await _run_dj(yaml_path, job_id=job_id)
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not out_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise EngineError(f"dj-process 退出码 {code}\n{tail}")

    rows = sum(1 for line in out_path.open(encoding="utf-8") if line.strip())
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=str(out_path),
        format="jsonl",
        rows=rows,
        size=out_path.stat().st_size,
        origin="synthetic",  # 增强也归类为 synthetic(原始/合成 二分足够)
        produced_by_job_id=job_id,
        note=f"增强产出(来自 v{input_version.version_no},job={job_id})",
    )
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    warnings: list[str] = []
    if rows == 0:
        warnings.append("增强后样本数为 0,可能 LLM 调用失败或 prompt 不匹配")
    # 增强 1→1,扩增比通常 = 1
    expansion_ratio = (rows / input_count) if input_count > 0 else None
    if expansion_ratio is not None and abs(expansion_ratio - 1.0) > 0.1:
        warnings.append(
            f"增强通常 1→1,实际扩增比 {expansion_ratio:.2f},请检查算子链"
        )

    report = AugmentReport(
        job_id=job_id,
        input_version_id=input_version.id,
        output_version_id=version.id,
        mode=goal.mode,
        input_count=input_count,
        output_count=rows,
        expansion_ratio=expansion_ratio,
        elapsed_seconds=round(time.time() - started, 2),
        operator_chain=operator_chain,
        warnings=warnings,
        raw={"goal": goal.model_dump(mode="json")},
    )
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return version, yaml_text, str(log_path), report
