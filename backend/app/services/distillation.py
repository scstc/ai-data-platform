"""数据蒸馏执行引擎:和加工共享 dj-process 入口,产物落原数据集新版本。

与 ``engine.run_process_job`` 的差异:
- 任务级参数(goal)在落库前写到 report JSON,不进 data-juicer YAML
- 报告里多保留 input/output 行数(蒸馏特有)
- 不支持 manifest 输入(蒸馏白名单不收多模态算子)
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
from app.schemas.distillation import DistillationGoal, DistillationReport
from app.services.external_store import upload_file_to_uploads
from app.services.engine import (
    EngineError,
    _new_version_id,
    _run_dj,
    build_config,
    materialized_version,
)


async def run_distillation_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
    goal: DistillationGoal,
    output_dataset_id: str | None = None,
) -> tuple[DatasetVersion, str, str, DistillationReport]:
    """对输入版本跑蒸馏算子链 → 写回 dataset(output_dataset_id 或 input 同 dataset)新版本。

    返回 (新版本, yaml 文本, 日志路径, 报告)。失败抛 EngineError。
    """
    dataset_id = output_dataset_id or input_version.dataset_id
    # 校验目标 dataset 存在
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

    # 蒸馏不支持 manifest 输入(白名单无多模态算子);如有 manifest 直接报
    if input_version.format == "manifest":
        raise EngineError("蒸馏不支持 manifest 输入,请使用文本 jsonl 版本")

    async with materialized_version(input_version, session) as input_path:
        # 蒸馏的 goal 不进 DJ YAML(任务级参数,只用于报告/回放);算子链本身已经是可执行的
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(out_path),
            operators=operators,
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        # 数输入行(蒸馏 report 必备)
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
        origin="managed",
        produced_by_job_id=job_id,
        note=f"蒸馏产出(来自 v{input_version.version_no},goal={goal.score_field})",
    )
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    warnings: list[str] = []
    if rows == 0:
        warnings.append(
            f"蒸馏后样本数为 0,可能 score_field({goal.score_field})不存在或过滤过严"
        )
    keep_ratio_actual = (rows / input_count) if input_count > 0 else None

    report = DistillationReport(
        job_id=job_id,
        input_version_id=input_version.id,
        output_version_id=version.id,
        input_count=input_count,
        output_count=rows,
        keep_ratio_actual=keep_ratio_actual,
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
