"""数据集交付/导出引擎(治理整改 G8/G9):治理后版本 → 训练平台三件套。

交付物:train.parquet(可分片)/jsonl + train_stats.jsonl + dataset_card.md,打包落 S3。
是**独立交付动作**,不混进治理 process.yaml,不产新 DatasetVersion(交付物非数据版本)。
"""

from __future__ import annotations

import io
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.schemas.export import ExportFileItem, ExportGoal, ExportReport
from app.services.engine import _read_jsonl_head
from app.services.external_store import (
    materialized_version,
    platform_config,
    upload_object,
)
from app.services.landing import (
    MANIFEST_FORMAT,
    ParquetCodecError,
    parquet_bytes_to_records,
    records_to_jsonl_bytes,
    records_to_parquet_bytes,
)
from app.services.lineage_service import build_lineage

logger = logging.getLogger(__name__)


class ExportError(RuntimeError):
    """导出执行失败(无可交付内容 / 不支持的格式 / 编码失败 / 目标无效)。"""


def shard_records(
    records: list[dict], shard_size: int | None
) -> list[list[dict]]:
    """按"每片约 shard_size 条"切分记录(纯切片;字节级 parquet 分片留给编码层)。

    shard_size 为 None/<=0 或不足一片 → 返回单片 [records]。
    注:DJ 的 export_shard_size 是按字节,这里以条数近似(平台侧确定性切分,
    避免依赖 DJ executor 的目录式分片产物,便于直接读回/上传)。
    """
    if not shard_size or shard_size <= 0 or len(records) <= shard_size:
        return [records]
    return [records[i : i + shard_size] for i in range(0, len(records), shard_size)]


def collect_limitations(
    version: DatasetVersion, upstream_formats: set[str]
) -> list[str]:
    """据版本元数据 + 上游来源格式确定性推导"已知局限"(写入 dataset_card)。"""
    lims: list[str] = []
    if version.schema_variant == "eval" and (version.rows or 0) < 300:
        lims.append(
            f"评估集仅 {version.rows or 0} 条(<300),统计显著性不足(规范 §3.7)"
        )
    if version.origin == "synthetic":
        lims.append("含 LLM 合成/增强数据,可能有幻觉,建议人工抽检")
    if upstream_formats & {"pdf", "doc", "docx", "ppt", "pptx"}:
        lims.append("源含 PDF/Word 解析,可能存在 OCR/版面识别误差")
    if version.scan_verdict != "passed":
        lims.append(f"未通过内容安全扫描(scan_verdict={version.scan_verdict})")
    if not version.stats_uri and not version.quality_stats:
        lims.append("无逐条质量统计(未跑质量评估)")
    return lims


def build_dataset_card(
    *,
    dataset_name: str,
    version: DatasetVersion,
    source_lines: list[str],
    operator_chain: list[dict],
    limitations: list[str],
) -> str:
    """渲染 dataset_card.md(规范 §6.1:名称/用途/schema/条数/血缘/治理流程/局限)。"""
    now = datetime.now(UTC).isoformat()
    lines = [
        f"# {dataset_name}",
        "",
        "## 数据集元信息",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 训练用途 train_type | {version.train_type or '(未标注)'} |",
        f"| schema 变体 | {version.schema_variant or '(未标注)'} |",
        f"| 格式 | {version.format} |",
        f"| 样本条数 | {version.rows if version.rows is not None else '(未知)'} |",
        f"| 来源 origin | {version.origin} |",
        f"| 安全扫描 | {version.scan_verdict} |",
        f"| 发布状态 | {version.publish_status} |",
        f"| 版本号 | v{version.version_no} |",
        f"| 导出时间 | {now} |",
        "",
        "## 数据来源(血缘)",
        "",
    ]
    lines += [f"- {s}" for s in source_lines] or ["- (无上游记录)"]
    lines += ["", "## 治理流程(算子链)", ""]
    if operator_chain:
        for op in operator_chain:
            params = op.get("params")
            ptxt = f" 参数:{params}" if params else ""
            lines.append(f"- [{op.get('jobType', '?')}] {op.get('name', '?')}{ptxt}")
    else:
        lines.append("- (无加工算子;原始落地或直接构造)")
    lines += ["", "## 已知局限", ""]
    lines += [f"- {x}" for x in limitations] or ["- (无)"]
    lines.append("")
    return "\n".join(lines)


async def collect_lineage(
    session: AsyncSession, version: DatasetVersion
) -> tuple[list[str], list[dict], set[str]]:
    """上游 BFS 收集血缘:返回 (来源行, 算子链, 上游来源格式集)。

    治理整改 P1-②:薄封装 `lineage_service.build_lineage`(direction="up" 只沿
    produced_by_job_id → Job → JobInput → 输入版本上溯,不查下游消费者;
    skip_lake_layer=True 避免导出接口引入湖层查询而变慢),不再自行维护第二套
    BFS。根版本(isOriginal)的来源行取 build_lineage 已顺带补上的版本节点
    sourceKind/sourceFormat(复用同一次 Dataset 查询,不为此另发查询)。
    """
    graph = await build_lineage(
        session, [version.id], direction="up", skip_lake_layer=True
    )
    source_lines: list[str] = []
    operator_chain: list[dict] = []
    upstream_formats: set[str] = set()
    for node in graph["nodes"]:
        if node["kind"] == "job":
            for op in node["operators"]:
                operator_chain.append(
                    {
                        "jobType": node["jobType"],
                        "name": op.get("name"),
                        "params": op.get("params"),
                    }
                )
        elif node["kind"] == "version" and node["isOriginal"]:
            fmt = node.get("sourceFormat")
            if fmt:
                upstream_formats.add(fmt.lower())
            source_lines.append(
                f"{node['datasetName']} · 来源={node.get('sourceKind') or '?'}"
                f"/{fmt or '?'}"
            )
    return source_lines, operator_chain, upstream_formats


async def run_export_job(
    session: AsyncSession,
    *,
    job_id: str,
    version: DatasetVersion,
    goal: ExportGoal,
) -> tuple[str, ExportReport]:
    """把治理后版本导出为训练三件套并落 S3。

    返回 (日志路径, 报告)。失败抛 ExportError。
    """
    if version.format == MANIFEST_FORMAT:
        raise ExportError(
            "多模态 manifest 交付走媒体目录+清单(规范 §8.6),暂不支持本导出"
        )

    out_dir = (
        Path(settings.datasets_dir)
        / version.dataset_id
        / f"v{version.version_no}"
        / "export"
        / job_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"
    report_path = out_dir / "report.json"
    started = time.time()
    warnings: list[str] = []

    cfg = platform_config()
    bucket = settings.storage_minio_upload_bucket
    prefix = (
        goal.target_prefix
        or f"{version.dataset_id}/v{version.version_no}/delivery"
    ).strip("/")

    # 读全量记录(parquet/jsonl 分流)
    async with materialized_version(version, session) as input_path:
        if not input_path.exists():
            raise ExportError(f"版本数据文件不存在:{version.storage_uri}")
        if version.format == "parquet":
            records = parquet_bytes_to_records(input_path.read_bytes(), limit=0)
        else:
            records = _read_jsonl_head(input_path, 0)

    files: list[ExportFileItem] = []

    async def _put(name: str, blob: bytes, ctype: str) -> None:
        key = f"{prefix}/{name}"
        await upload_object(cfg, bucket, key, io.BytesIO(blob), len(blob), ctype)
        files.append(
            ExportFileItem(name=name, key=key, bucket=bucket, size=len(blob))
        )

    # ① train 产物
    shard_count = 0
    if goal.export_format == "jsonl":
        await _put(
            "train.jsonl",
            records_to_jsonl_bytes(records),
            "application/x-ndjson",
        )
        shard_count = 1
    else:
        shards = shard_records(records, goal.export_shard_size)
        try:
            if len(shards) == 1:
                await _put(
                    "train.parquet",
                    records_to_parquet_bytes(records),
                    "application/octet-stream",
                )
            else:
                for i, shard in enumerate(shards):
                    await _put(
                        f"train-{i:05d}.parquet",
                        records_to_parquet_bytes(shard),
                        "application/octet-stream",
                    )
        except ParquetCodecError as exc:
            raise ExportError(f"parquet 编码失败(空/异构列):{exc}") from exc
        shard_count = len(shards)

    # ② train_stats.jsonl
    if goal.include_stats:
        if version.stats_uri and Path(version.stats_uri).exists():
            await _put(
                "train_stats.jsonl",
                Path(version.stats_uri).read_bytes(),
                "application/x-ndjson",
            )
        elif version.quality_stats:
            await _put(
                "train_stats.jsonl",
                records_to_jsonl_bytes([version.quality_stats]),
                "application/x-ndjson",
            )
        else:
            warnings.append("无质量统计,已跳过 train_stats.jsonl")

    # ③ dataset_card.md
    if goal.include_card:
        ds = await session.get(Dataset, version.dataset_id)
        source_lines, operator_chain, upstream_formats = await collect_lineage(
            session, version
        )
        card = build_dataset_card(
            dataset_name=(ds.name if ds else version.dataset_id),
            version=version,
            source_lines=source_lines,
            operator_chain=operator_chain,
            limitations=collect_limitations(version, upstream_formats),
        )
        await _put("dataset_card.md", card.encode("utf-8"), "text/markdown")

    if not files:
        raise ExportError("无可交付内容(train/stats/card 全部跳过)")

    # 记血缘边:export 任务作为该版本下游(不建 DatasetVersion)
    session.add(JobInput(job_id=job_id, dataset_version_id=version.id))
    await session.commit()

    report = ExportReport(
        job_id=job_id,
        version_id=version.id,
        target_uri=f"s3://{bucket}/{prefix}",
        files=files,
        record_count=len(records),
        shard_count=shard_count,
        train_format=goal.export_format,
        included_stats=goal.include_stats,
        included_card=goal.include_card,
        elapsed_seconds=round(time.time() - started, 2),
        warnings=warnings,
    )
    log_path.write_text(
        f"导出完成:{len(files)} 个交付对象 → s3://{bucket}/{prefix}\n"
        + "\n".join(f.name for f in files),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(log_path), report
