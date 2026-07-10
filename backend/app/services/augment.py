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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.schemas.augment import AugmentGoal, AugmentReport
from app.services.engine import (
    EngineError,
    _new_version_id,
    _read_head_records,
    _run_dj,
    build_config,
    detect_text_key,
    materialized_version,
)
from app.services.external_store import upload_file_to_datasets


async def _persist_augment_report(
    session: AsyncSession, job_id: str, report: AugmentReport
) -> None:
    """把报告快照写进 jobs.eval_report(治理整改:报告跨机器可读,不依赖本地磁盘)。

    与 review_runner 写 jobs.review_report 同模式——存 DB 而非 file,
    避免报告被清理 / 跨机器执行后无法读取。
    """
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(eval_report=report.model_dump(mode="json"))
    )
    await session.commit()


def _expand_input_jsonl(src: Path, dst: Path, n: int) -> int:
    """把 src 的 records 复制 n 倍写到 dst,返回新行数(治理整改:目标总量扩增)。

    augment 的算子默认 1→1(LLM 改写);为了让单次任务产出 N 倍数据,把
    输入样本复制 N 份,让 DJ 的 mapper 对每份独立调一次 LLM,从而产出
    N 种不同变体(配合 temperature>=0.5 + 不同 seed)。n<=1 时等同复制原文件。
    """
    if n < 1:
        raise EngineError(f"target_per_sample 必须 >= 1,实得 {n}")
    src_text = src.read_text(encoding="utf-8")
    # 单行写法:每条原始样本成为 N 个相邻副本(LLM 对每个副本独立生成)
    if not src_text.strip():
        dst.write_text(src_text, encoding="utf-8")
        return 0
    if n == 1:
        if src != dst:
            dst.write_text(src_text, encoding="utf-8")
        return src_text.count("\n") + (0 if src_text.endswith("\n") else 1)
    expanded = src_text * n
    dst.write_text(expanded, encoding="utf-8")
    return expanded.count("\n")


async def run_augment_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    member_configs: list[dict[str, Any]] | None = None,
    target_members: list[str] | None = None,
    goal: AugmentGoal,
    output_dataset_id: str | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str, AugmentReport]:
    """对输入版本跑增强算子链 → 写回 dataset 新版本。

    member_configs: 新版成员独立配置，格式 [{member_name, operators}, ...]
    target_members: 要处理的成员名列表；None=处理所有成员
    goal: 全局增强目标参数（不按成员区分）
    llm_snapshot(可复现凭证):透传给 build_config/_run_dj,None 时行为不变。
    产物 origin='synthetic',返回 (新版本, yaml 文本, 日志路径, 报告)。失败抛 EngineError。
    """
    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise EngineError(f"输出数据集不存在:{dataset_id}")

    # 1. 查询版本成员
    from app.services.engine import _get_version_members

    members = await _get_version_members(session, input_version.id)

    # 如果版本无成员表（旧版本），回退到原逻辑
    if not members:
        return await _run_augment_job_legacy(
            session,
            job_id=job_id,
            input_version=input_version,
            operators=operators,
            goal=goal,
            output_dataset_id=output_dataset_id,
            text_keys=text_keys,
            llm_snapshot=llm_snapshot,
        )

    # 2. 确定处理模式
    if member_configs:
        config_map = {cfg["member_name"]: cfg for cfg in member_configs}
        members_to_process = [m for m in members if m.table_name in config_map]
        if not members_to_process:
            raise EngineError("未找到 member_configs 中指定的成员")
    else:
        if not operators:
            raise EngineError("未提供 member_configs 时必须提供 operators 参数")
        if target_members:
            members_to_process = [m for m in members if m.table_name in target_members]
        else:
            members_to_process = members
        if not members_to_process:
            raise EngineError("未找到要处理的成员")
        config_map = {
            m.table_name: {"operators": operators}
            for m in members_to_process
        }

    # 3. 创建新版本目录
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()

    # 4. 对每个成员独立处理
    from app.services.engine import (
        _get_member_output_path,
        _materialize_member,
        _new_member_id,
    )
    from app.services.external_store import (
        upload_jsonl_member,
        upload_parquet_member,
    )
    from app.services.landing import parquet_bytes_to_records

    all_logs: list[str] = []
    all_yamls: list[str] = []
    new_members_data: list[dict[str, Any]] = []
    total_input_count = 0
    total_output_count = 0
    all_operator_names: list[str] = []

    for member in members_to_process:
        member_cfg = config_map[member.table_name]
        member_operators = member_cfg["operators"]

        # 物化成员文件
        input_path = await _materialize_member(session, member)

        # 目标总量扩增:把成员样本复制 target_per_sample 份,
        # 让单算子(如 calibrate_qa_mapper)对每份独立调一次 LLM
        n = goal.target_per_sample or 1
        if n > 1:
            expanded_path = out_dir / f"{member.table_name}_expanded.jsonl"
            _expand_input_jsonl(Path(input_path), expanded_path, n)
            dj_input_path = expanded_path
        else:
            dj_input_path = Path(input_path)

        # 输出路径
        out_format = member.format if member.format in ("parquet", "jsonl") else "jsonl"
        output_path = _get_member_output_path(
            dataset_id, new_vno, member.table_name, out_format
        )
        yaml_path = out_dir / f"{member.table_name}_job.yaml"

        # 构建 DJ 配置（增强用 dj-process）
        cfg = build_config(
            project_name=f"{job_id}-{member.table_name}",
            input_path=str(dj_input_path),
            output_path=str(output_path),
            operators=member_operators,
            llm_snapshot=llm_snapshot,
        )
        yaml_content = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_content, encoding="utf-8")

        all_yamls.append(f"# Member: {member.table_name}\n{yaml_content}")

        # 计数输入行(扩增后的实际行数)
        if dj_input_path.suffix == ".jsonl":
            member_input_count = sum(
                1 for line in dj_input_path.open(encoding="utf-8") if line.strip()
            )
        else:
            # parquet
            member_input_count = len(
                parquet_bytes_to_records(dj_input_path.read_bytes())
            )
        total_input_count += member_input_count

        # 运行 dj-process
        code, log = await _run_dj(
            yaml_path,
            job_id=f"{job_id}-{member.table_name}",
            llm_snapshot=llm_snapshot,
        )

        operator_names = [op["name"] for op in member_operators]
        all_logs.append(
            f"=== {member.table_name} ===\n算子: {operator_names}\n输入: {member_input_count} 行\n{log}"
        )
        all_operator_names.extend(operator_names)

        if code != 0 or not output_path.exists():
            tail = "\n".join(log.strip().splitlines()[-8:])
            raise EngineError(
                f"成员 {member.table_name} 增强失败(dj-process 退出码 {code})\n{tail}"
            )

        # 上传产出文件
        if out_format == "parquet":
            data_bytes = output_path.read_bytes()
            storage_uri = await upload_parquet_member(
                dataset_id, new_vno, member.table_name, data_bytes
            )
            rows = len(parquet_bytes_to_records(data_bytes))
        else:
            data_bytes = output_path.read_bytes()
            storage_uri = await upload_jsonl_member(
                dataset_id, new_vno, member.table_name, data_bytes
            )
            rows = sum(
                1 for line in output_path.open(encoding="utf-8") if line.strip()
            )

        total_output_count += rows

        new_members_data.append(
            {
                "table_name": member.table_name,
                "storage_uri": storage_uri,
                "format": out_format,
                "rows": rows,
                "size": len(data_bytes),
                "schema_variant": member.schema_variant,
            }
        )

    # 5. 创建新版本和成员记录;写回输入同数据集时,未处理成员原样结转
    #    (跨数据集输出时不结转:输出集的版本只承载增强产物)
    from app.models.dataset_version_table import DatasetVersionTable
    from app.services.engine import carry_over_members

    if dataset_id == input_version.dataset_id:
        new_members_data += carry_over_members(
            members, {m.table_name for m in members_to_process}
        )
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=f"s3://{settings.storage_minio_datasets_bucket}/{dataset_id}/v{new_vno}/",
        format="multi" if len(new_members_data) > 1 else new_members_data[0]["format"],
        rows=sum(m["rows"] or 0 for m in new_members_data),
        size=sum(m["size"] or 0 for m in new_members_data),
        origin="synthetic",
        produced_by_job_id=job_id,
        note=f"增强产出(来自 v{input_version.version_no})",
    )
    session.add(version)
    await session.flush()

    for m_data in new_members_data:
        member_rec = DatasetVersionTable(
            id=_new_member_id(),
            dataset_version_id=version.id,
            **m_data,
        )
        session.add(member_rec)

    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    # 6. 汇总日志和YAML
    combined_log = "\n\n".join(all_logs)
    log_path = out_dir / "run.log"
    log_path.write_text(combined_log, encoding="utf-8")

    combined_yaml = "\n\n---\n\n".join(all_yamls)

    # 7. 生成报告
    warnings: list[str] = []
    if total_output_count == 0:
        warnings.append("增强后样本数为 0,可能 LLM 调用失败或 prompt 不匹配")
    # 增强 1→1,扩增比通常 = 1
    expansion_ratio = (
        (total_output_count / total_input_count) if total_input_count > 0 else None
    )
    if expansion_ratio is not None and abs(expansion_ratio - 1.0) > 0.1:
        warnings.append(
            f"增强通常 1→1,实际扩增比 {expansion_ratio:.2f},请检查算子链"
        )

    # 去重算子名（保持顺序）
    unique_operators = []
    seen = set()
    for op_name in all_operator_names:
        if op_name not in seen:
            unique_operators.append(op_name)
            seen.add(op_name)

    report = AugmentReport(
        job_id=job_id,
        input_version_id=input_version.id,
        output_version_id=version.id,
        mode=goal.mode,
        input_count=total_input_count,
        output_count=total_output_count,
        expansion_ratio=expansion_ratio,
        elapsed_seconds=round(time.time() - started, 2),
        operator_chain=unique_operators,
        warnings=warnings,
        raw={"goal": goal.model_dump(mode="json")},
    )
    report_path = out_dir / "report.json"
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 同步落 DB(report.json 本地副本留作审计;跨机器读取靠 DB)
    await _persist_augment_report(session, job_id, report)

    return version, combined_yaml, str(log_path), report


async def _run_augment_job_legacy(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    goal: AugmentGoal,
    output_dataset_id: str | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str, AugmentReport]:
    """旧版单文件增强逻辑（无成员表的版本）。"""
    if not operators:
        raise EngineError("旧版单文件版本必须提供 operators 参数")

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
        # 目标总量扩增:同 run_augment_job,单文件版做同样处理
        n = goal.target_per_sample or 1
        if n > 1:
            expanded_path = out_dir / "expanded_input.jsonl"
            _expand_input_jsonl(Path(input_path), expanded_path, n)
            dj_input_path = expanded_path
        else:
            dj_input_path = Path(input_path)

        # text_keys 用户显式指定优先;留空则按字段名优先级自动探测主文本字段
        detected_key = None if text_keys else detect_text_key(
            _read_head_records(dj_input_path, 50)
        )
        cfg = build_config(
            project_name=job_id,
            input_path=str(dj_input_path),
            output_path=str(out_path),
            operators=operators,
            text_key=detected_key,
            text_keys=text_keys,
            llm_snapshot=llm_snapshot,
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        input_count = sum(
            1 for line in dj_input_path.open(encoding="utf-8") if line.strip()
        )
        code, log = await _run_dj(yaml_path, job_id=job_id, llm_snapshot=llm_snapshot)
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not out_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise EngineError(f"dj-process 退出码 {code}\n{tail}")

    rows = sum(1 for line in out_path.open(encoding="utf-8") if line.strip())
    # 产出上传 MinIO(治理产出持久化到对象存储;读路径已按 s3:// 走)
    storage_uri = await upload_file_to_datasets(dataset_id, new_vno, out_path)
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=storage_uri,
        format="jsonl",
        rows=rows,
        size=out_path.stat().st_size,
        origin="synthetic",  # 增强也归类为 synthetic(原始/合成 二分足够)
        produced_by_job_id=job_id,
        note=f"增强产出(来自 v{input_version.version_no})",
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
    # 同步落 DB(report.json 本地副本留作审计;跨机器读取靠 DB)
    await _persist_augment_report(session, job_id, report)
    return version, yaml_text, str(log_path), report

