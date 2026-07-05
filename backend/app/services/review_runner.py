"""内容审核任务编排(#4)。

run_review:按版本形态分两路——
- 多表版本(有 dataset_version_tables 成员):逐成员物化 → scan_version →
  findings 带 table_name(行号为成员内相对行号)→ 产出多成员新版本(成员统一
  jsonl 落 MinIO,safety 为嵌套结构不写 parquet)→ 聚合报告(byTable)。
- 旧单文件版本:保持原逻辑(materialized_version + 本地 data.jsonl)。

处置方式 config.action:
- tag(默认):每行加 safety 字段产出打标版本。
- delete:命中行不写入产出(净化版),按 sampleLimit 扫描(与 tag 一致);超出
  样本上限的未扫行原样结转进净化版(scanned=false),此时产出版本 verdict 为
  unscanned 而非 passed。被删行(含 safety)写 <table>.removed.jsonl 存档,位置记入
  report.removedArchives——连同 review_findings 逐条命中与审计中间件的 POST
  留痕,构成删除的完整可追溯记录。

异常 → ReviewError(上层置 job failed)。并发信号量由 job_runner 统一持有。

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
from app.models.dataset_version_table import DatasetVersionTable
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.review_finding import ReviewFinding
from app.services.ai import get_ai_provider
from app.services.engine import (
    _get_version_members,
    _materialize_member,
    _new_member_id,
    carry_over_members,
)
from app.services.external_store import materialized_version, upload_jsonl_member
from app.services.landing import parquet_bytes_to_records
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


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False) + "\n" for row in rows
    ).encode("utf-8")


def _is_flagged(row: dict[str, Any]) -> bool:
    safety = row.get("safety")
    return bool(isinstance(safety, dict) and safety.get("flagged"))


def _split_action(
    tagged_rows: list[dict[str, Any]], action: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按处置方式切分产出:tag → 全保留;delete → (净化行, 被删行)。"""
    if action != "delete":
        return tagged_rows, []
    kept = [r for r in tagged_rows if not _is_flagged(r)]
    removed = [r for r in tagged_rows if _is_flagged(r)]
    return kept, removed


def _add_findings(
    session: AsyncSession,
    findings: list[dict[str, Any]],
    *,
    job_id: str,
    version_id: str,
    table_name: str | None,
) -> None:
    """批量写命中记录(rowIndex 为被审数据内的行号;多表时为成员内相对行号)。"""
    for f in findings:
        session.add(
            ReviewFinding(
                id=_new_finding_id(),
                job_id=job_id,
                version_id=version_id,
                table_name=table_name,
                row_index=f["rowIndex"],
                category=f["category"],
                severity=f["severity"],
                source=f["source"],
                detail=f["detail"],
                snippet=f["snippet"],
            )
        )


def _merge_counter(dst: dict[str, int], src: dict[str, int]) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0) + v


def _verdicts(
    *,
    flagged_rows: int,
    sample_applied: bool,
    audited_all: bool,
    action: str,
) -> tuple[str, str]:
    """(被审版本 verdict, 产出版本 verdict)。

    被审版本口径三态(#4 发布门,见 docs/plan/11):
      有命中 → failed;零命中但只扫了样本/只审了部分成员 → unscanned;
      零命中且全量全成员 → passed。"passed" 必须意味着"整版都扫过且干净"。
    产出版本:tag 模式内容与被审一致 → 同 verdict;delete 模式已剔除全部命中,
    产出无命中——但仅当全量全成员扫过才敢判 passed,否则(未扫行原样留在净化版)
    仍是 unscanned。
    """
    if flagged_rows > 0:
        input_verdict = "failed"
    elif sample_applied or not audited_all:
        input_verdict = "unscanned"
    else:
        input_verdict = "passed"
    if action == "delete":
        output_verdict = (
            "passed" if not sample_applied and audited_all else "unscanned"
        )
    else:
        output_verdict = input_verdict
    return input_verdict, output_verdict


async def _next_version_no(session: AsyncSession, dataset_id: str) -> int:
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    return (max_vno or 0) + 1


async def run_review(
    session: AsyncSession,
    *,
    job: Job,
    version: DatasetVersion,
    config: dict[str, Any],
    target_members: list[str] | None = None,
) -> DatasetVersion:
    """对被审版本跑内容审核 → 写命中 + 产出打标/净化版本 + 回写报告。

    多表版本逐成员审核(target_members 圈定范围,缺省全部;未被审成员原样
    结转,产出版本保持完整成员集,与 quality/engine 的多成员语义一致);
    无成员的旧版本走单文件路径。
    成功返回产出版本;数据文件缺失/成员不存在抛 ReviewError(上层置 job failed)。
    """
    members = await _get_version_members(session, version.id)
    if not members:
        return await _run_review_legacy(
            session, job=job, version=version, config=config
        )

    action = str(config.get("action") or "tag")
    if target_members:
        members_to_process = [m for m in members if m.table_name in target_members]
        if not members_to_process:
            raise ReviewError("未找到要审核的成员(target_members 不匹配任何表)")
    else:
        members_to_process = members
    audited_all = len(members_to_process) == len(members)

    provider = get_ai_provider(settings)
    dataset_id = version.dataset_id
    new_vno = await _next_version_no(session, dataset_id)

    # 聚合报告
    total_rows = 0
    scanned_rows = 0
    flagged_rows = 0
    sample_applied = False
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_table: dict[str, int] = {}
    warnings: list[str] = []
    deleted_rows = 0
    removed_archives: dict[str, str] = {}
    new_members_data: list[dict[str, Any]] = []

    for member in members_to_process:
        input_path = await _materialize_member(session, member)
        if not input_path.exists():
            raise ReviewError(
                f"成员 {member.table_name} 数据文件不存在:{member.storage_uri}"
            )
        if member.format == "parquet":
            rows = parquet_bytes_to_records(input_path.read_bytes())
        else:
            rows = _read_jsonl(input_path)

        findings, tagged_rows, report = await scan_version(
            rows,
            config,
            provider=provider,
        )
        _add_findings(
            session,
            findings,
            job_id=job.id,
            version_id=version.id,
            table_name=member.table_name,
        )

        kept, removed = _split_action(tagged_rows, action)
        # safety 为嵌套结构,产出统一 jsonl(parquet 成员在此转为 jsonl)
        data_bytes = _jsonl_bytes(kept)
        storage_uri = await upload_jsonl_member(
            dataset_id, new_vno, member.table_name, data_bytes
        )
        if removed:
            removed_archives[member.table_name] = await upload_jsonl_member(
                dataset_id,
                new_vno,
                f"{member.table_name}.removed",
                _jsonl_bytes(removed),
            )
            deleted_rows += len(removed)

        new_members_data.append(
            {
                "table_name": member.table_name,
                "storage_uri": storage_uri,
                "format": "jsonl",
                "rows": len(kept),
                "size": len(data_bytes),
                "schema_variant": member.schema_variant,
            }
        )

        total_rows += report["totalRows"]
        scanned_rows += report["scannedRows"]
        flagged_rows += report["flaggedRows"]
        sample_applied = sample_applied or report["sampleLimitApplied"]
        _merge_counter(by_category, report["byCategory"])
        _merge_counter(by_severity, report["bySeverity"])
        _merge_counter(by_source, report["bySource"])
        by_table[member.table_name] = report["flaggedRows"]
        warnings.extend(f"[{member.table_name}] {w}" for w in report["warnings"])

    agg_report: dict[str, Any] = {
        "totalRows": total_rows,
        "scannedRows": scanned_rows,
        "flaggedRows": flagged_rows,
        "sampleLimitApplied": sample_applied,
        "byCategory": by_category,
        "bySeverity": by_severity,
        "bySource": by_source,
        "byTable": by_table,
        "action": action,
        "deletedRows": deleted_rows if action == "delete" else None,
        "removedArchives": removed_archives,
        "warnings": warnings,
    }

    input_verdict, output_verdict = _verdicts(
        flagged_rows=flagged_rows,
        sample_applied=sample_applied,
        audited_all=audited_all,
        action=action,
    )

    note_action = (
        f"内容审核净化(删除 {deleted_rows} 行,来自 v{version.version_no})"
        if action == "delete"
        else f"内容审核打标(来自 v{version.version_no})"
    )
    # 未被审的成员原样结转,产出版本保持输入版本的完整成员集
    new_members_data += carry_over_members(
        members, {m.table_name for m in members_to_process}
    )
    out_version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=(
            f"s3://{settings.storage_minio_upload_bucket}/{dataset_id}/v{new_vno}/"
        ),
        format="multi" if len(new_members_data) > 1 else "jsonl",
        rows=sum(m["rows"] or 0 for m in new_members_data),
        size=sum(m["size"] or 0 for m in new_members_data),
        origin="review",
        produced_by_job_id=job.id,
        note=note_action,
        scan_verdict=output_verdict,
        verdict_source="auto",
    )
    session.add(out_version)
    await session.flush()
    for m_data in new_members_data:
        session.add(
            DatasetVersionTable(
                id=_new_member_id(),
                dataset_version_id=out_version.id,
                **m_data,
            )
        )

    # 回写被审版本 verdict(供发布门直接校验) + 血缘边 + 报告
    version.scan_verdict = input_verdict
    version.verdict_source = "auto"
    session.add(JobInput(job_id=job.id, dataset_version_id=version.id))
    job.review_report = agg_report

    await session.commit()
    await session.refresh(out_version)
    return out_version


async def _run_review_legacy(
    session: AsyncSession,
    *,
    job: Job,
    version: DatasetVersion,
    config: dict[str, Any],
) -> DatasetVersion:
    """旧单文件版本路径:materialized_version + 本地 data.jsonl 产出(原逻辑)。

    同样支持 action=tag|delete;delete 的被删行存档写产出目录 data.removed.jsonl。
    """
    action = str(config.get("action") or "tag")
    # 经解析器拿本地路径:hosted 按需从 S3 拉取并规范化(临时),managed 透传。
    # 打标产出仍写受管存储(origin=review),源不动;血缘指向 hosted 被审版本。
    async with materialized_version(version, session) as src_path:
        if not src_path.exists():
            raise ReviewError(f"被审版本数据文件不存在:{version.storage_uri}")
        rows = _read_jsonl(src_path)
    provider = get_ai_provider(settings)

    findings, tagged_rows, report = await scan_version(
        rows, config, provider=provider
    )
    _add_findings(
        session, findings, job_id=job.id, version_id=version.id, table_name=None
    )

    kept, removed = _split_action(tagged_rows, action)

    dataset_id = version.dataset_id
    new_vno = await _next_version_no(session, dataset_id)
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.jsonl"
    out_path.write_bytes(_jsonl_bytes(kept))

    removed_archives: dict[str, str] = {}
    if removed:
        removed_path = out_dir / "data.removed.jsonl"
        removed_path.write_bytes(_jsonl_bytes(removed))
        removed_archives["data"] = str(removed_path)

    report = {
        **report,
        "byTable": {},
        "action": action,
        "deletedRows": len(removed) if action == "delete" else None,
        "removedArchives": removed_archives,
    }

    input_verdict, output_verdict = _verdicts(
        flagged_rows=report["flaggedRows"],
        sample_applied=report["sampleLimitApplied"],
        audited_all=True,
        action=action,
    )

    note_action = (
        f"内容审核净化(删除 {len(removed)} 行,来自 v{version.version_no})"
        if action == "delete"
        else f"内容审核打标(来自 v{version.version_no})"
    )
    tagged_version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=str(out_path),
        format="jsonl",
        rows=len(kept),
        size=out_path.stat().st_size,
        origin="review",
        produced_by_job_id=job.id,
        note=note_action,
        scan_verdict=output_verdict,
        verdict_source="auto",
    )
    session.add(tagged_version)
    # 回写被审版本的 scan_verdict:使被审版本本身也持有扫描结论,
    # 从而让用户可直接对它执行 publish(发布门校验 scan_verdict==passed)。
    version.scan_verdict = input_verdict
    version.verdict_source = "auto"
    # 血缘边:被审版本 → review job
    session.add(JobInput(job_id=job.id, dataset_version_id=version.id))
    job.review_report = report

    await session.commit()
    await session.refresh(tagged_version)
    return tagged_version
