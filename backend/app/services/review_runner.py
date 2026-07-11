"""内容审核任务编排(#4)。

run_review:按版本形态分两路——
- 多表版本(有 dataset_version_tables 成员):逐成员物化 → scan_version →
  findings 带 table_name(行号为成员内相对行号)→ 产出多成员新版本(成员统一
  jsonl 落 MinIO,safety 为嵌套结构不写 parquet)→ 聚合报告(byTable)。
- 旧单文件版本:保持原逻辑(materialized_version + 本地 data.jsonl)。

命中处置固定为删除:命中行不写入产出(净化版),按 sampleLimit 扫描;超出样本上限
的未扫行原样结转进净化版(scanned=false),此时产出版本 verdict 为 unscanned 而非
passed。被删行(含 safety)写 <table>.removed.jsonl 存档,位置记入
report.removedArchives——连同 review_findings 逐条命中与审计中间件的 POST 留痕,
构成删除的完整可追溯记录。

异常 → ReviewError(上层置 job failed)。并发信号量由 job_runner 统一持有。

设计见 docs/plan/07-内容安全设计.md §3.2。
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
from pathlib import Path
from typing import Any

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
    _new_member_id,
    carry_over_members,
    materialized_member,
)
from app.services.external_store import materialized_version, upload_jsonl_member
from app.services.landing import parquet_bytes_to_records
from app.services.review import scan_version
from app.services.version_alloc import with_version_conflict_retry

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


def _split_flagged(
    tagged_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """切分产出为 (净化行, 被删行):命中行进 removed,其余进净化版。"""
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
) -> tuple[str, str]:
    """(被审版本 verdict, 产出版本 verdict)。

    被审版本口径三态(#4 发布门,见 docs/plan/11):
      有命中 → failed;零命中但只扫了样本/只审了部分成员 → unscanned;
      零命中且全量全成员 → passed。"passed" 必须意味着"整版都扫过且干净"。
    产出版本(净化版)已剔除全部命中,产出无命中——但仅当全量全成员扫过才敢判
    passed,否则(未扫行原样留在净化版)仍是 unscanned。
    """
    if flagged_rows > 0:
        input_verdict = "failed"
    elif sample_applied or not audited_all:
        input_verdict = "unscanned"
    else:
        input_verdict = "passed"
    output_verdict = "passed" if not sample_applied and audited_all else "unscanned"
    return input_verdict, output_verdict


async def run_review(
    session: AsyncSession,
    *,
    job: Job,
    version: DatasetVersion,
    config: dict[str, Any],
    target_members: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> DatasetVersion:
    """对被审版本跑内容审核 → 写命中 + 产出打标/净化版本 + 回写报告。

    多表版本逐成员审核(target_members 圈定范围,缺省全部;未被审成员原样
    结转,产出版本保持完整成员集,与 quality/engine 的多成员语义一致);
    无成员的旧版本走单文件路径。
    llm_snapshot(可复现凭证):透传给 get_ai_provider,None 时行为不变。
    成功返回产出版本;数据文件缺失/成员不存在抛 ReviewError(上层置 job failed)。
    """
    members = await _get_version_members(session, version.id)
    if not members:
        return await _run_review_legacy(
            session, job=job, version=version, config=config, llm_snapshot=llm_snapshot
        )

    if target_members:
        members_to_process = [m for m in members if m.table_name in target_members]
        if not members_to_process:
            raise ReviewError("未找到要审核的成员(target_members 不匹配任何表)")
    else:
        members_to_process = members
    audited_all = len(members_to_process) == len(members)

    provider = get_ai_provider(settings, llm_snapshot)
    dataset_id = version.dataset_id

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
    # 阶段一产出(扫描/净化在内存中完成,不占版本号、不传对象存储):
    # [{member, data_bytes, removed_bytes|None}],阶段二占号后统一上传
    products: list[dict[str, Any]] = []

    for member in members_to_process:
        # 物化成员文件(async with 兜底:s3 来源的临时落地文件退出时自动清理)
        async with materialized_member(session, member) as input_path:
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

        kept, removed = _split_flagged(tagged_rows)
        # safety 为嵌套结构,产出统一 jsonl(parquet 成员在此转为 jsonl)
        data_bytes = _jsonl_bytes(kept)
        removed_bytes = _jsonl_bytes(removed) if removed else None
        if removed:
            deleted_rows += len(removed)

        products.append(
            {
                "member": member,
                "data_bytes": data_bytes,
                "removed_bytes": removed_bytes,
                "rows": len(kept),
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
        "action": "delete",
        "deletedRows": deleted_rows,
        "removedArchives": {},  # 阶段二上传后回填(见下方)
        "warnings": warnings,
    }

    input_verdict, output_verdict = _verdicts(
        flagged_rows=flagged_rows,
        sample_applied=sample_applied,
        audited_all=audited_all,
    )

    note_action = f"内容审核净化(删除 {deleted_rows} 行,来自 v{version.version_no})"
    # 未被审的成员原样结转,产出版本保持输入版本的完整成员集
    carried = carry_over_members(
        members, {m.table_name for m in members_to_process}
    )
    total_out_rows = sum(p["rows"] for p in products) + sum(
        m["rows"] or 0 for m in carried
    )
    total_out_size = sum(len(p["data_bytes"]) for p in products) + sum(
        m["size"] or 0 for m in carried
    )
    member_count = len(products) + len(carried)

    # 占版本号(uq_dataset_version_no 冲突自动重试)→ 上传净化/存档产物 →
    # 成员行 → 单事务提交。先占号再上传,storage_uri 的 v<n> 前缀与实际占到
    # 的版本号保证一致
    async def _build_version(version_no: int) -> DatasetVersion:
        v = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=version_no,
            storage_uri=(
                f"s3://{settings.storage_minio_datasets_bucket}"
                f"/{dataset_id}/v{version_no}/"
            ),
            format="multi" if member_count > 1 else "jsonl",
            rows=total_out_rows,
            size=total_out_size,
            origin="review",
            produced_by_job_id=job.id,
            note=note_action,
            scan_verdict=output_verdict,
            verdict_source="auto",
        )
        session.add(v)
        return v

    out_version = await with_version_conflict_retry(
        session, dataset_id, _build_version
    )
    new_vno = out_version.version_no
    try:
        removed_archives: dict[str, str] = {}
        for p in products:
            member = p["member"]
            storage_uri = await upload_jsonl_member(
                dataset_id, new_vno, member.table_name, p["data_bytes"]
            )
            if p["removed_bytes"] is not None:
                removed_archives[member.table_name] = await upload_jsonl_member(
                    dataset_id,
                    new_vno,
                    f"{member.table_name}.removed",
                    p["removed_bytes"],
                )
            session.add(
                DatasetVersionTable(
                    id=_new_member_id(),
                    dataset_version_id=out_version.id,
                    table_name=member.table_name,
                    storage_uri=storage_uri,
                    format="jsonl",
                    rows=p["rows"],
                    size=len(p["data_bytes"]),
                    schema_variant=member.schema_variant,
                )
            )
        for m_data in carried:
            session.add(
                DatasetVersionTable(
                    id=_new_member_id(),
                    dataset_version_id=out_version.id,
                    **m_data,
                )
            )
        agg_report["removedArchives"] = removed_archives

        # 回写被审版本 verdict(供发布门直接校验) + 血缘边 + 报告
        version.scan_verdict = input_verdict
        version.verdict_source = "auto"
        session.add(JobInput(job_id=job.id, dataset_version_id=version.id))
        job.review_report = agg_report
        # 契约 C:降级/跳过类告警(LLM 服务故障、规则运行异常等)同步进
        # Job.warnings,不只是埋在 review_report 里等着被忽略
        if warnings:
            job.warnings = [*(job.warnings or []), *warnings]

        await session.commit()
    except BaseException:
        # 上传/入库阶段失败:回滚已 flush 的版本行(否则 job_runner 落 failed
        # 态的 commit 会把半成品版本一并提交),并 best-effort 清掉已传到
        # v<n> 前缀的对象,不留孤立版本
        await session.rollback()
        try:
            from app.services.external_store import platform_config, remove_prefix

            await remove_prefix(
                platform_config(),
                settings.storage_minio_datasets_bucket,
                f"{dataset_id}/v{new_vno}/",
            )
        except Exception:  # noqa: BLE001 清理失败不掩盖原始错误,但必须留痕
            logger.warning(
                "job %s 回滚后清理 v%s 前缀残留对象失败,可能留下孤儿对象",
                job.id,
                new_vno,
                exc_info=True,
            )
        raise
    await session.refresh(out_version)
    return out_version


async def _run_review_legacy(
    session: AsyncSession,
    *,
    job: Job,
    version: DatasetVersion,
    config: dict[str, Any],
    llm_snapshot: dict[str, str | None] | None = None,
) -> DatasetVersion:
    """旧单文件版本路径:materialized_version + 本地 data.jsonl 产出(原逻辑)。

    命中行删除:被删行存档写产出目录 data.removed.jsonl。
    """
    # 经解析器拿本地路径:hosted 按需从 S3 拉取并规范化(临时),managed 透传。
    # 净化产出仍写受管存储(origin=review),源不动;血缘指向 hosted 被审版本。
    async with materialized_version(version, session) as src_path:
        if not src_path.exists():
            raise ReviewError(f"被审版本数据文件不存在:{version.storage_uri}")
        rows = _read_jsonl(src_path)
    provider = get_ai_provider(settings, llm_snapshot)

    findings, tagged_rows, report = await scan_version(
        rows, config, provider=provider
    )
    _add_findings(
        session, findings, job_id=job.id, version_id=version.id, table_name=None
    )

    kept, removed = _split_flagged(tagged_rows)
    data_bytes = _jsonl_bytes(kept)

    dataset_id = version.dataset_id
    # 本地处理目录先按 job_id 命名,与版本号解耦;占到版本号后再整目录改名
    # 为 v<n>(见下方),避免"先猜号写盘、后占号时撞车"导致目录名和实际
    # 占到的版本号对不上
    staging_dir = Path(settings.datasets_dir) / dataset_id / f"job-{job.id}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "data.jsonl").write_bytes(data_bytes)
    if removed:
        (staging_dir / "data.removed.jsonl").write_bytes(_jsonl_bytes(removed))

    report = {
        **report,
        "byTable": {},
        "action": "delete",
        "deletedRows": len(removed),
        "removedArchives": {},  # 目录改名为 v<n> 后回填(见下方)
    }

    input_verdict, output_verdict = _verdicts(
        flagged_rows=report["flaggedRows"],
        sample_applied=report["sampleLimitApplied"],
        audited_all=True,
    )

    note_action = f"内容审核净化(删除 {len(removed)} 行,来自 v{version.version_no})"

    # 占版本号(uq_dataset_version_no 冲突自动重试)→ 目录改名 → 提交;先占号
    # 再改名,storage_uri 的 v<n> 路径与实际占到的版本号保证一致
    async def _build_version(version_no: int) -> DatasetVersion:
        out_dir = Path(settings.datasets_dir) / dataset_id / f"v{version_no}"
        v = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=version_no,
            storage_uri=str(out_dir / "data.jsonl"),
            format="jsonl",
            rows=len(kept),
            size=len(data_bytes),
            origin="review",
            produced_by_job_id=job.id,
            note=note_action,
            scan_verdict=output_verdict,
            verdict_source="auto",
        )
        session.add(v)
        return v

    out_version = await with_version_conflict_retry(
        session, dataset_id, _build_version
    )
    new_vno = out_version.version_no
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    try:
        staging_dir.rename(out_dir)
        if removed:
            report["removedArchives"] = {"data": str(out_dir / "data.removed.jsonl")}
        # 回写被审版本的 scan_verdict:使被审版本本身也持有扫描结论,
        # 从而让用户可直接对它执行 publish(发布门校验 scan_verdict==passed)。
        version.scan_verdict = input_verdict
        version.verdict_source = "auto"
        # 血缘边:被审版本 → review job
        session.add(JobInput(job_id=job.id, dataset_version_id=version.id))
        job.review_report = report
        # 契约 C:降级/跳过类告警同步进 Job.warnings(同 run_review)
        if report["warnings"]:
            job.warnings = [*(job.warnings or []), *report["warnings"]]

        await session.commit()
    except BaseException:
        # 目录改名/提交失败:回滚已 flush 的版本行,不留孤立版本。
        # rename 半途失败时 staging_dir 可能仍残留(未成功改名为 out_dir),
        # 必须留痕 + best-effort 清理,否则磁盘上留一个无法从版本行反查的
        # 孤儿目录,且失败原因无处可查。
        logger.exception(
            "job %s 净化产出目录改名/提交失败(staging=%s, out=%s)",
            job.id,
            staging_dir,
            out_dir,
        )
        await session.rollback()
        if staging_dir.exists():
            try:
                shutil.rmtree(staging_dir)
            except OSError:
                logger.warning("清理孤儿 staging 目录失败:%s", staging_dir)
        raise
    await session.refresh(out_version)
    return out_version
