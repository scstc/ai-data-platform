"""数据合成(make)执行引擎:走 data-juicer LLM Mapper 链,产物落新版本。

增强(augment)走 ``services/augment.py``,本模块专管 LLM 造新数据。
产物 ``DatasetVersion.origin='synthetic'``(与 augment 共享约定)。
所有算子需 LLM;OPENAI_API_KEY 未配 → needs_api 拦截。
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
from app.schemas.make import MakeGoal, MakeReport
from app.services.external_store import upload_file_to_uploads
from app.services.engine import (
    EngineError,
    _new_version_id,
    _run_dj,
    build_config,
    materialized_version,
)


async def run_make_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
    goal: MakeGoal,
    output_dataset_id: str | None = None,
) -> tuple[DatasetVersion, str, str, MakeReport]:
    """对输入版本跑合成算子链 → 写回 dataset 新版本。

    产物 origin='synthetic',返回 (新版本, yaml 文本, 日志路径, 报告)。失败抛 EngineError。
    """
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
    # 产出上传 MinIO(治理产出持久化到对象存储;读路径已按 s3:// 走)
    storage_uri = await upload_file_to_uploads(dataset_id, new_vno, out_path)
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=storage_uri,
        format="jsonl",
        rows=rows,
        size=out_path.stat().st_size,
        origin="synthetic",
        produced_by_job_id=job_id,
        note=f"合成产出(来自 v{input_version.version_no},job={job_id})",
    )
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    warnings: list[str] = []
    if rows == 0:
        warnings.append("合成后样本数为 0,可能 LLM 调用失败或 prompt 不匹配")
    expansion_ratio = (rows / input_count) if input_count > 0 else None

    report = MakeReport(
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
