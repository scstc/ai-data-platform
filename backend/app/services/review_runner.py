"""内容审核任务编排(#4)。

run_review:load 被审版本 jsonl → scan_version → 批量写 review_findings →
产出打标新版本(jsonl 每行加 safety,落 datasets_dir/<dataset_id>/v<n>/data.jsonl,
注册 DatasetVersion origin="review"、produced_by_job_id=job.id,version_no=max+1,
复用 engine 产版本写法) → report 回写 job.review_report → job state=success;
异常 → failed + error。复用引擎并发信号量。

设计见 docs/plan/07-内容安全设计.md §3.2。
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.review_finding import ReviewFinding
from app.services.ai import get_ai_provider
from app.services.external_store import materialized_version
from app.services.review import scan_version

logger = logging.getLogger(__name__)


class ReviewError(RuntimeError):
    """审核执行失败(数据文件缺失等)。"""


def _new_version_id() -> str:
    return f"dsv-{secrets.token_hex(3)}"


def _new_finding_id() -> str:
    return f"rf-{secrets.token_hex(3)}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """读 jsonl 全部非空行,逐行 json.loads。"""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


async def run_review(
    session: AsyncSession,
    *,
    job: Job,
    version: DatasetVersion,
    config: dict[str, Any],
) -> DatasetVersion:
    """对被审版本跑内容审核 → 写命中 + 产出打标版本 + 回写报告。

    成功返回打标版本;数据文件缺失抛 ReviewError(上层置 job failed)。
    并发受 engine 信号量限流(scan 为 CPU/IO 轻量但 LLM 路可能慢)。
    """
    # 经解析器拿本地路径:hosted 按需从 S3 拉取并规范化(临时),managed 透传。
    # 打标产出仍写受管存储(origin=review),源不动;血缘指向 hosted 被审版本。
    async with materialized_version(version, session) as src_path:
        if not src_path.exists():
            raise ReviewError(f"被审版本数据文件不存在:{version.storage_uri}")
        rows = _read_jsonl(src_path)
    provider = get_ai_provider(settings)

    # 并发信号量由调用方(job_runner._run_job)统一持有;此处只做扫描
    findings, tagged_rows, report = await scan_version(rows, config, provider=provider)

    # 1) 批量写命中记录(rowIndex 即被审版本的绝对行号)
    for f in findings:
        session.add(
            ReviewFinding(
                id=_new_finding_id(),
                job_id=job.id,
                version_id=version.id,
                row_index=f["rowIndex"],
                category=f["category"],
                severity=f["severity"],
                source=f["source"],
                detail=f["detail"],
                snippet=f["snippet"],
            )
        )

    # 2) 产出打标新版本(沿用 engine.run_process_job 的产版本写法)
    dataset_id = version.dataset_id
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.jsonl"
    out_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in tagged_rows
        ),
        encoding="utf-8",
    )

    # 自动安全判据(#4 发布门,设计见 docs/plan/11),通过性口径三态:
    #   有命中               → failed(任何敏感词/PII/LLM 命中都需处置或脱敏)
    #   零命中但仅扫了样本前缀 → unscanned(未完整覆盖,不能据此certify整版安全;
    #                            需调大 sampleLimit 全量重扫,或人工接受风险)
    #   零命中且全量扫描       → passed
    # "passed" 必须意味着"整版都扫过且干净",否则尾部 PII 会从发布门漏过。
    if report.get("flaggedRows", 0) > 0:
        verdict = "failed"
    elif report.get("sampleLimitApplied"):
        verdict = "unscanned"
    else:
        verdict = "passed"

    tagged_version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=str(out_path),
        format="jsonl",
        rows=len(tagged_rows),
        size=out_path.stat().st_size,
        origin="review",
        produced_by_job_id=job.id,
        note=f"内容审核打标(来自 v{version.version_no})",
        scan_verdict=verdict,
        verdict_source="auto",
    )
    session.add(tagged_version)
    # 3) 回写被审版本的 scan_verdict:使被审版本本身也持有扫描结论,
    #    从而让用户可直接对它执行 publish(发布门校验 scan_verdict==passed)。
    #    打标版本 already 持有相同 verdict 供下游血缘追溯。
    version.scan_verdict = verdict
    version.verdict_source = "auto"
    # 4) 血缘边:被审版本 → review job
    session.add(JobInput(job_id=job.id, dataset_version_id=version.id))

    # 4) 报告回写 job
    job.review_report = report

    await session.commit()
    await session.refresh(tagged_version)
    return tagged_version
