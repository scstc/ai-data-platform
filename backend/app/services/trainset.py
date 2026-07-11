"""数据合成(trainset)执行引擎:走 data-juicer LLM Mapper 链,产物落新版本。

与 ``services/augment.py`` 的差异:本模块专管 LLM 造新训练样本(QA/COT/偏好,1→N),
不做 1→1 扩增比告警(生成场景合法扩增)。引擎逻辑与 augment/make 同构(成员级 dj-process)。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.schemas.trainset import TrainsetGoal, TrainsetReport
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
from app.services.version_alloc import with_version_conflict_retry

logger = logging.getLogger(__name__)


async def run_trainset_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    member_configs: list[dict[str, Any]] | None = None,
    target_members: list[str] | None = None,
    goal: TrainsetGoal,
    output_dataset_id: str | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str, TrainsetReport]:
    """对输入版本跑数据合成算子链 → 写回 dataset 新版本。

    member_configs: 新版成员独立配置，格式 [{member_name, operators}, ...]
    target_members: 要处理的成员名列表；None=处理所有成员
    goal: 全局生成目标参数（不按成员区分）
    llm_snapshot(可复现凭证):透传给 build_config/_run_dj,None 时行为不变。
    产物 origin='synthetic',返回 (新版本, yaml 文本, 日志路径, 报告)。
    失败抛 EngineError。
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
        return await _run_trainset_job_legacy(
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

    # 3. 本地处理目录:按 job_id 命名,与版本号解耦——版本号要等全部成员
    #    处理成功后才占用(见下方阶段二),避免"处理时先猜号、后占号时撞车"
    #    导致 storage_uri 里的 v<n> 前缀和实际占到的版本号对不上
    out_dir = Path(settings.datasets_dir) / dataset_id / f"job-{job_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()

    # 4. 对每个成员独立处理(本阶段只跑 dj-process 落本地,不占版本号、不传对象存储)
    from app.services.engine import (
        _new_member_id,
        materialized_member,
    )
    from app.services.external_store import (
        upload_jsonl_member,
        upload_parquet_member,
    )
    from app.services.landing import parquet_bytes_to_records

    all_logs: list[str] = []
    all_yamls: list[str] = []
    products: list[dict[str, Any]] = []  # 阶段一产出;阶段二占号后统一上传
    total_input_count = 0
    all_operator_names: list[str] = []

    for member in members_to_process:
        member_cfg = config_map[member.table_name]
        member_operators = member_cfg["operators"]

        # 物化成员文件(async with 兜底:s3 来源的临时落地文件退出时自动清理)
        async with materialized_member(session, member) as input_path:
            # 输出路径(本地,job_id 目录下,版本号未知阶段)
            out_format = (
                member.format if member.format in ("parquet", "jsonl") else "jsonl"
            )
            output_path = out_dir / f"{member.table_name}.{out_format}"
            yaml_path = out_dir / f"{member.table_name}_job.yaml"

            # 构建 DJ 配置（生成用 dj-process）
            cfg = build_config(
                project_name=f"{job_id}-{member.table_name}",
                input_path=str(input_path),
                output_path=str(output_path),
                operators=member_operators,
                llm_snapshot=llm_snapshot,
            )
            yaml_content = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
            yaml_path.write_text(yaml_content, encoding="utf-8")

            all_yamls.append(f"# Member: {member.table_name}\n{yaml_content}")

            # 计数输入行
            if input_path.suffix == ".jsonl":
                member_input_count = sum(
                    1 for line in input_path.open(encoding="utf-8") if line.strip()
                )
            else:
                # parquet
                member_input_count = len(
                    parquet_bytes_to_records(input_path.read_bytes())
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
                f"=== {member.table_name} ===\n算子: {operator_names}\n"
                f"输入: {member_input_count} 行\n{log}"
            )
            all_operator_names.extend(operator_names)

            if code != 0 or not output_path.exists():
                tail = "\n".join(log.strip().splitlines()[-8:])
                raise EngineError(
                    f"成员 {member.table_name} 数据合成失败"
                    f"(dj-process 退出码 {code})\n{tail}"
                )

            # 阶段一只落本地,不传对象存储(见下方阶段二统一上传)
            if out_format == "parquet":
                rows = len(parquet_bytes_to_records(output_path.read_bytes()))
            else:
                rows = sum(
                    1 for line in output_path.open(encoding="utf-8") if line.strip()
                )

            products.append(
                {
                    "member": member,
                    "out_format": out_format,
                    "out_path": output_path,
                    "rows": rows,
                    "size": output_path.stat().st_size,
                }
            )

    total_output_count = sum(p["rows"] for p in products)

    # 5. 占版本号(uq_dataset_version_no 冲突自动重试)→ 上传产物 → 成员行 →
    #    单事务提交。先占号再上传:storage_uri 的 v<n> 前缀与实际占到的版本号
    #    保证一致。写回输入同数据集时,未处理成员原样结转(跨数据集输出时不
    #    结转:输出集的版本只承载生成产物)
    from app.models.dataset_version_table import DatasetVersionTable
    from app.services.engine import carry_over_members

    carried = (
        carry_over_members(members, {m.table_name for m in members_to_process})
        if dataset_id == input_version.dataset_id
        else []
    )
    total_size = sum(p["size"] for p in products) + sum(
        m["size"] or 0 for m in carried
    )
    total_rows = total_output_count + sum(m["rows"] or 0 for m in carried)
    member_count = len(products) + len(carried)

    async def _build_version(version_no: int) -> DatasetVersion:
        v = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=version_no,
            storage_uri=(
                f"s3://{settings.storage_minio_datasets_bucket}"
                f"/{dataset_id}/v{version_no}/"
            ),
            format="multi" if member_count > 1 else products[0]["out_format"],
            rows=total_rows,
            size=total_size,
            origin="synthetic",
            produced_by_job_id=job_id,
            note=f"数据合成产出(来自 v{input_version.version_no})",
        )
        session.add(v)
        return v

    version = await with_version_conflict_retry(session, dataset_id, _build_version)
    new_vno = version.version_no
    try:
        for p in products:
            member = p["member"]
            data_bytes = p["out_path"].read_bytes()
            if p["out_format"] == "parquet":
                storage_uri = await upload_parquet_member(
                    dataset_id, new_vno, member.table_name, data_bytes
                )
            else:
                storage_uri = await upload_jsonl_member(
                    dataset_id, new_vno, member.table_name, data_bytes
                )
            session.add(
                DatasetVersionTable(
                    id=_new_member_id(),
                    dataset_version_id=version.id,
                    table_name=member.table_name,
                    storage_uri=storage_uri,
                    format=p["out_format"],
                    rows=p["rows"],
                    size=p["size"],
                    schema_variant=member.schema_variant,
                )
            )
        for m_data in carried:
            session.add(
                DatasetVersionTable(
                    id=_new_member_id(), dataset_version_id=version.id, **m_data
                )
            )
        session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
        await session.commit()
    except BaseException:
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
                job_id,
                new_vno,
                exc_info=True,
            )
        raise
    await session.refresh(version)

    # 6. 汇总日志和YAML
    combined_log = "\n\n".join(all_logs)
    log_path = out_dir / "run.log"
    log_path.write_text(combined_log, encoding="utf-8")

    combined_yaml = "\n\n---\n\n".join(all_yamls)

    # 7. 生成报告
    warnings: list[str] = []
    if total_output_count == 0:
        warnings.append("生成后样本数为 0,可能 LLM 调用失败或 prompt 不匹配")
    expansion_ratio = (
        (total_output_count / total_input_count) if total_input_count > 0 else None
    )

    # 去重算子名（保持顺序）
    unique_operators = []
    seen = set()
    for op_name in all_operator_names:
        if op_name not in seen:
            unique_operators.append(op_name)
            seen.add(op_name)

    report = TrainsetReport(
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

    return version, combined_yaml, str(log_path), report


async def _run_trainset_job_legacy(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    goal: TrainsetGoal,
    output_dataset_id: str | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str, TrainsetReport]:
    """旧版单文件生成逻辑（无成员表的版本）。"""
    if not operators:
        raise EngineError("旧版单文件版本必须提供 operators 参数")

    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise EngineError(f"输出数据集不存在:{dataset_id}")

    # 本地处理目录按 job_id 命名,与版本号解耦——版本号等 dj-process 成功后
    # 才占用(见下方),避免"先猜号处理、后占号时撞车"导致 storage_uri 对不上
    out_dir = Path(settings.datasets_dir) / dataset_id / f"job-{job_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.jsonl"
    yaml_path = out_dir / "job.yaml"
    log_path = out_dir / "run.log"
    report_path = out_dir / "report.json"

    started = time.time()
    operator_chain = [op["name"] for op in operators]

    async with materialized_version(input_version, session) as input_path:
        # text_keys 用户显式指定优先;留空则按字段名优先级自动探测主文本字段
        detected_key = None if text_keys else detect_text_key(
            _read_head_records(Path(input_path), 50)
        )
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(out_path),
            operators=operators,
            text_key=detected_key,
            text_keys=text_keys,
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        with Path(input_path).open(encoding="utf-8") as f:
            input_count = sum(1 for line in f if line.strip())
        code, log = await _run_dj(yaml_path, job_id=job_id)
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not out_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise EngineError(f"dj-process 退出码 {code}\n{tail}")

    rows = sum(1 for line in out_path.open(encoding="utf-8") if line.strip())
    size = out_path.stat().st_size

    # 占版本号(uq_dataset_version_no 冲突自动重试)→ 上传 → 提交;先占号再
    # 上传,storage_uri 的 v<n> 前缀与实际占到的版本号保证一致
    async def _build_version(version_no: int) -> DatasetVersion:
        v = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=version_no,
            storage_uri=(
                f"s3://{settings.storage_minio_datasets_bucket}"
                f"/{dataset_id}/v{version_no}/data.jsonl"
            ),
            format="jsonl",
            rows=rows,
            size=size,
            origin="synthetic",
            produced_by_job_id=job_id,
            note=f"数据合成产出(来自 v{input_version.version_no})",
        )
        session.add(v)
        return v

    version = await with_version_conflict_retry(session, dataset_id, _build_version)
    new_vno = version.version_no
    try:
        # 产出上传 MinIO(治理产出持久化到对象存储;读路径已按 s3:// 走)
        await upload_file_to_datasets(dataset_id, new_vno, out_path)
        session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
        await session.commit()
    except BaseException:
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
                job_id,
                new_vno,
                exc_info=True,
            )
        raise
    await session.refresh(version)

    warnings: list[str] = []
    if rows == 0:
        warnings.append("生成后样本数为 0,可能 LLM 调用失败或 prompt 不匹配")
    expansion_ratio = (rows / input_count) if input_count > 0 else None

    report = TrainsetReport(
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
