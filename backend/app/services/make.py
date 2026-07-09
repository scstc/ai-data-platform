"""数据合并(make)执行引擎。

mode='merge':多个 jsonl 成员按行(号或 id)对齐,拼接共同字段(如 text)成
新行——横向拼接,产物行数 = 主文件行数。
mode='concat':多个 jsonl 成员整体追加(A 10 行 + B 10 行 → 20 行)——纵向
堆叠,不拼字段,每行保留自身原始字段。
两者均纯 Python 执行,不进 data-juicer 子进程、不依赖 LLM。
mode='synthesize' 保留:走 data-juicer LLM Mapper 链(存量任务重跑/流水线)。
产物 ``DatasetVersion.origin='synthetic'``(与 augment 共享约定)。
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
    _read_head_records,
    _run_dj,
    build_config,
    detect_text_key,
    materialized_version,
)


# 视为句末标点的分隔符:片段先去尾重复,再在整段结尾补一个,
# 使产物形如「片段A。片段B。」(见需求示例);空格/换行等分隔符不补尾。
_TERMINAL_SEPARATORS = ("。", ".", "!", "?", "！", "？", ";", "；")


def _fragment(row: dict[str, Any], field: str, terminal: str) -> str | None:
    if row.get(field) is None:
        return None
    frag = str(row[field]).strip()
    if terminal:
        frag = frag.rstrip(terminal)
    return frag or None


def merge_records(
    named_rows: list[tuple[str, list[dict[str, Any]]]],
    field: str,
    separator: str,
    key_field: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """多文件记录对齐,拼接 ``field`` 字段;纯函数,便于单测。

    ``key_field`` 为空:按行号位置对齐(兼容早于按 id 匹配特性创建的任务)。
    产物行数 = 主文件(第一个)行数,其余字段沿用主文件对应行;
    扩展文件比主文件短则缺失行少拼一段,长则多余行丢弃,均记 warning。

    ``key_field`` 非空:按该字段的值跨文件匹配对应行,而非位置——各扩展
    文件先建「键值→行」索引(重复键取首次出现,记 warning);主文件仍逐行
    驱动输出(行数与顺序不变),缺键的主行/扩展文件里找不到匹配键的,只是
    该片段跳过,不影响其余片段拼接。
    """
    warnings: list[str] = []
    primary_name, primary = named_rows[0]
    terminal = separator if separator in _TERMINAL_SEPARATORS else ""
    merged: list[dict[str, Any]] = []

    if key_field:
        indexes: list[tuple[str, dict[Any, dict[str, Any]]]] = []
        for name, rows in named_rows[1:]:
            index: dict[Any, dict[str, Any]] = {}
            duplicates = 0
            for row in rows:
                key = row.get(key_field)
                if key is None:
                    continue
                if key in index:
                    duplicates += 1
                    continue
                index[key] = row
            if duplicates:
                warnings.append(
                    f"{name} 存在 {duplicates} 个重复「{key_field}」,"
                    "已取首次出现的行参与匹配"
                )
            indexes.append((name, index))

        missing_key_in_primary = 0
        unmatched_counts = dict.fromkeys((name for name, _ in indexes), 0)
        for base in primary:
            key = base.get(key_field)
            if key is None:
                missing_key_in_primary += 1
            parts: list[str] = []
            own = _fragment(base, field, terminal)
            if own:
                parts.append(own)
            if key is not None:
                for name, index in indexes:
                    row = index.get(key)
                    if row is None:
                        unmatched_counts[name] += 1
                        continue
                    frag = _fragment(row, field, terminal)
                    if frag:
                        parts.append(frag)
            rec = dict(base)
            rec[field] = separator.join(parts) + (terminal if parts else "")
            merged.append(rec)

        if missing_key_in_primary:
            warnings.append(
                f"{primary_name} 有 {missing_key_in_primary} 行缺少键字段"
                f"「{key_field}」,未参与按 id 匹配"
            )
        for name, count in unmatched_counts.items():
            if count:
                warnings.append(
                    f"{name} 未找到 {count} 行匹配的「{key_field}」,"
                    "缺失片段未拼接"
                )
        return merged, warnings

    for i, base in enumerate(primary):
        parts = []
        for _name, rows in named_rows:
            if i >= len(rows):
                continue
            frag = _fragment(rows[i], field, terminal)
            if frag:
                parts.append(frag)
        rec = dict(base)
        rec[field] = separator.join(parts) + (terminal if parts else "")
        merged.append(rec)
    for name, rows in named_rows[1:]:
        if len(rows) > len(primary):
            warnings.append(
                f"{name} 比主文件 {primary_name} 多 "
                f"{len(rows) - len(primary)} 行,多余行已丢弃"
            )
        elif len(rows) < len(primary):
            warnings.append(
                f"{name} 比主文件 {primary_name} 少 "
                f"{len(primary) - len(rows)} 行,缺失行未拼接该片段"
            )
    return merged, warnings


async def _run_merge_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    goal: MakeGoal,
    output_dataset_id: str | None = None,
) -> tuple[DatasetVersion, str, str, MakeReport]:
    """merge 模式:多 jsonl 成员按行拼接 → 新版本(纯 Python,不走 DJ)。

    产物成员沿用主文件名(主文件的演进),被合并的扩展文件不结转,
    未参与的其他成员原样结转。失败抛 EngineError(落任务错误,fail loud)。
    """
    names = goal.merge_members or []
    field = (goal.merge_field or "").strip()
    separator = goal.merge_separator or "。"
    key_field = (goal.merge_key or "").strip() or None
    if len(names) < 2 or not field:
        raise EngineError("合并模式需要至少 2 个成员文件和合并字段")

    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise EngineError(f"输出数据集不存在:{dataset_id}")

    from app.services.engine import (
        _get_member_output_path,
        _get_version_members,
        _materialize_member,
        _new_member_id,
    )
    from app.services.external_store import upload_jsonl_member

    members = await _get_version_members(session, input_version.id)
    by_name = {m.table_name: m for m in members}
    if missing := [n for n in names if n not in by_name]:
        raise EngineError(f"版本中不存在成员:{', '.join(missing)}")
    if non_jsonl := [n for n in names if by_name[n].format != "jsonl"]:
        raise EngineError(f"仅支持 jsonl 成员合并:{', '.join(non_jsonl)}")

    started = time.time()

    # 读入各成员并校验合并字段确为共同字段(抽样前 50 行)
    named_rows: list[tuple[str, list[dict[str, Any]]]] = []
    for n in names:
        path = await _materialize_member(session, by_name[n])
        rows = [
            json.loads(line)
            for line in path.open(encoding="utf-8")
            if line.strip()
        ]
        sampled_fields = sorted({k for r in rows[:50] for k in r})
        if field not in sampled_fields:
            raise EngineError(
                f"成员 {n} 缺少合并字段「{field}」,"
                f"可用字段:{', '.join(sampled_fields) or '(空文件)'}"
            )
        if key_field and key_field not in sampled_fields:
            raise EngineError(
                f"成员 {n} 缺少键字段「{key_field}」,"
                f"可用字段:{', '.join(sampled_fields) or '(空文件)'}"
            )
        named_rows.append((n, rows))

    merged, warnings = merge_records(named_rows, field, separator, key_field)

    # 落盘 + 上传:产物成员沿用主文件名
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    primary_name = names[0]
    out_path = _get_member_output_path(dataset_id, new_vno, primary_name, "jsonl")
    data_bytes = "".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in merged
    ).encode("utf-8")
    out_path.write_bytes(data_bytes)
    storage_uri = await upload_jsonl_member(
        dataset_id, new_vno, primary_name, data_bytes
    )

    new_members_data: list[dict[str, Any]] = [
        {
            "table_name": primary_name,
            "storage_uri": storage_uri,
            "format": "jsonl",
            "rows": len(merged),
            "size": len(data_bytes),
            "schema_variant": by_name[primary_name].schema_variant,
        }
    ]
    # 写回输入同数据集时,未参与合并的成员原样结转(被合并的扩展文件已并入主文件)
    from app.models.dataset_version_table import DatasetVersionTable
    from app.services.engine import carry_over_members

    if dataset_id == input_version.dataset_id:
        new_members_data += carry_over_members(members, set(names))

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=f"s3://{settings.storage_minio_upload_bucket}/{dataset_id}/v{new_vno}/",
        format="multi" if len(new_members_data) > 1 else "jsonl",
        rows=sum(m["rows"] or 0 for m in new_members_data),
        size=sum(m["size"] or 0 for m in new_members_data),
        origin="synthetic",
        produced_by_job_id=job_id,
        note=goal.note
        or f"合并产出({' + '.join(names)},来自 v{input_version.version_no})",
    )
    session.add(version)
    await session.flush()
    for m_data in new_members_data:
        session.add(
            DatasetVersionTable(
                id=_new_member_id(), dataset_version_id=version.id, **m_data
            )
        )
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    # 配置回显(job.config_yaml)与日志/报告
    yaml_text = yaml.safe_dump(
        {
            "mode": "merge",
            "members": names,
            "field": field,
            "separator": separator,
            "key_field": key_field,
        },
        allow_unicode=True,
        sort_keys=False,
    )
    out_dir = out_path.parent
    align_desc = f"按「{key_field}」匹配" if key_field else "按行号位置对齐"
    log_lines = [
        f"merge 模式:{' + '.join(names)},字段={field},"
        f"分隔符={separator!r},{align_desc}",
        *(f"输入 {n}: {len(rows)} 行" for n, rows in named_rows),
        f"输出 {primary_name}: {len(merged)} 行",
        *warnings,
    ]
    log_path = out_dir / "run.log"
    log_path.write_text("\n".join(log_lines), encoding="utf-8")

    total_input = sum(len(rows) for _n, rows in named_rows)
    report = MakeReport(
        job_id=job_id,
        input_version_id=input_version.id,
        output_version_id=version.id,
        mode="merge",
        input_count=total_input,
        output_count=len(merged),
        # 合并没有"扩增比"概念(不造新数据),expansion_ratio 留空
        elapsed_seconds=round(time.time() - started, 2),
        operator_chain=[],
        warnings=warnings,
        raw={
            "goal": goal.model_dump(mode="json"),
            "input_rows": {n: len(rows) for n, rows in named_rows},
        },
    )
    (out_dir / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return version, yaml_text, str(log_path), report


async def _run_concat_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    goal: MakeGoal,
    output_dataset_id: str | None = None,
) -> tuple[DatasetVersion, str, str, MakeReport]:
    """concat 模式:多 jsonl 成员整体追加 → 新版本(纵向堆叠,不拼字段)。

    A 10 行 + B 10 行 → 产物 20 行,每行保留自身原始字段(与 merge 模式的
    横向字段拼接不同,产物行数 = 各成员行数之和)。产物成员沿用第一个
    成员的文件名,被追加的其余成员不结转,未参与的其他成员原样结转。
    """
    names = goal.merge_members or []
    if len(names) < 2:
        raise EngineError("追加合并模式需要至少 2 个成员文件")

    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise EngineError(f"输出数据集不存在:{dataset_id}")

    from app.services.engine import (
        _get_member_output_path,
        _get_version_members,
        _materialize_member,
        _new_member_id,
    )
    from app.services.external_store import upload_jsonl_member

    members = await _get_version_members(session, input_version.id)
    by_name = {m.table_name: m for m in members}
    if missing := [n for n in names if n not in by_name]:
        raise EngineError(f"版本中不存在成员:{', '.join(missing)}")
    if non_jsonl := [n for n in names if by_name[n].format != "jsonl"]:
        raise EngineError(f"仅支持 jsonl 成员追加合并:{', '.join(non_jsonl)}")

    started = time.time()

    named_rows: list[tuple[str, list[dict[str, Any]]]] = []
    for n in names:
        path = await _materialize_member(session, by_name[n])
        rows = [
            json.loads(line)
            for line in path.open(encoding="utf-8")
            if line.strip()
        ]
        named_rows.append((n, rows))

    merged: list[dict[str, Any]] = []
    for _n, rows in named_rows:
        merged.extend(rows)

    # 落盘 + 上传:产物成员沿用第一个成员的文件名
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    primary_name = names[0]
    out_path = _get_member_output_path(dataset_id, new_vno, primary_name, "jsonl")
    data_bytes = "".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in merged
    ).encode("utf-8")
    out_path.write_bytes(data_bytes)
    storage_uri = await upload_jsonl_member(
        dataset_id, new_vno, primary_name, data_bytes
    )

    new_members_data: list[dict[str, Any]] = [
        {
            "table_name": primary_name,
            "storage_uri": storage_uri,
            "format": "jsonl",
            "rows": len(merged),
            "size": len(data_bytes),
            "schema_variant": by_name[primary_name].schema_variant,
        }
    ]
    from app.models.dataset_version_table import DatasetVersionTable
    from app.services.engine import carry_over_members

    if dataset_id == input_version.dataset_id:
        new_members_data += carry_over_members(members, set(names))

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=f"s3://{settings.storage_minio_upload_bucket}/{dataset_id}/v{new_vno}/",
        format="multi" if len(new_members_data) > 1 else "jsonl",
        rows=sum(m["rows"] or 0 for m in new_members_data),
        size=sum(m["size"] or 0 for m in new_members_data),
        origin="synthetic",
        produced_by_job_id=job_id,
        note=goal.note
        or f"追加合并产出({' + '.join(names)},来自 v{input_version.version_no})",
    )
    session.add(version)
    await session.flush()
    for m_data in new_members_data:
        session.add(
            DatasetVersionTable(
                id=_new_member_id(), dataset_version_id=version.id, **m_data
            )
        )
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    yaml_text = yaml.safe_dump(
        {"mode": "concat", "members": names},
        allow_unicode=True,
        sort_keys=False,
    )
    out_dir = out_path.parent
    log_lines = [
        f"concat 模式(追加合并):{' + '.join(names)}",
        *(f"输入 {n}: {len(rows)} 行" for n, rows in named_rows),
        f"输出 {primary_name}: {len(merged)} 行",
    ]
    log_path = out_dir / "run.log"
    log_path.write_text("\n".join(log_lines), encoding="utf-8")

    total_input = sum(len(rows) for _n, rows in named_rows)
    report = MakeReport(
        job_id=job_id,
        input_version_id=input_version.id,
        output_version_id=version.id,
        mode="concat",
        input_count=total_input,
        output_count=len(merged),
        # 合并没有"扩增比"概念(不造新数据),expansion_ratio 留空
        elapsed_seconds=round(time.time() - started, 2),
        operator_chain=[],
        warnings=[],
        raw={
            "goal": goal.model_dump(mode="json"),
            "input_rows": {n: len(rows) for n, rows in named_rows},
        },
    )
    (out_dir / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return version, yaml_text, str(log_path), report


async def run_make_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    member_configs: list[dict[str, Any]] | None = None,
    target_members: list[str] | None = None,
    goal: MakeGoal,
    output_dataset_id: str | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str, MakeReport]:
    """对输入版本跑合成算子链 → 写回 dataset 新版本。

    operators: 统一应用到所有成员的算子列表（旧版兼容）
    member_configs: 新版成员独立配置，格式 [{member_name, operators}, ...]
    target_members: 要处理的成员名列表；None=处理所有成员
    goal: 全局合成目标参数（不按成员区分）
    llm_snapshot(可复现凭证):透传给 build_config/_run_dj(merge/concat 模式不
    走 DJ,不涉及);None 时行为不变。
    产物 origin='synthetic',返回 (新版本, yaml 文本, 日志路径, 报告)。失败抛 EngineError。
    """
    # merge/concat 模式:纯 Python 处理,不走下方 DJ 算子链
    if goal.mode == "merge":
        return await _run_merge_job(
            session,
            job_id=job_id,
            input_version=input_version,
            goal=goal,
            output_dataset_id=output_dataset_id,
        )
    if goal.mode == "concat":
        return await _run_concat_job(
            session,
            job_id=job_id,
            input_version=input_version,
            goal=goal,
            output_dataset_id=output_dataset_id,
        )

    dataset_id = output_dataset_id or input_version.dataset_id
    target_ds = await session.get(Dataset, dataset_id)
    if target_ds is None:
        raise EngineError(f"输出数据集不存在:{dataset_id}")

    # 1. 查询版本成员
    from app.services.engine import _get_version_members

    members = await _get_version_members(session, input_version.id)

    # 如果版本无成员表（旧版本），回退到原逻辑
    if not members:
        return await _run_make_job_legacy(
            session,
            job_id=job_id,
            input_version=input_version,
            operators=operators,
            goal=goal,
            output_dataset_id=output_dataset_id,
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

        # 输出路径
        out_format = member.format if member.format in ("parquet", "jsonl") else "jsonl"
        output_path = _get_member_output_path(
            dataset_id, new_vno, member.table_name, out_format
        )
        yaml_path = out_dir / f"{member.table_name}_job.yaml"

        # 构建 DJ 配置（合成用 dj-process）
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
            member_input_count = len(parquet_bytes_to_records(input_path.read_bytes()))
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
                f"成员 {member.table_name} 合成失败(dj-process 退出码 {code})\n{tail}"
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
    #    (跨数据集输出时不结转:输出集的版本只承载合成产物)
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
        storage_uri=f"s3://{settings.storage_minio_upload_bucket}/{dataset_id}/v{new_vno}/",
        format="multi" if len(new_members_data) > 1 else new_members_data[0]["format"],
        rows=sum(m["rows"] or 0 for m in new_members_data),
        size=sum(m["size"] or 0 for m in new_members_data),
        origin="synthetic",
        produced_by_job_id=job_id,
        note=f"合成产出(来自 v{input_version.version_no})",
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
        warnings.append("合成后样本数为 0,可能 LLM 调用失败或 prompt 不匹配")
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

    report = MakeReport(
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


async def _run_make_job_legacy(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    goal: MakeGoal,
    output_dataset_id: str | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str, MakeReport]:
    """旧版单文件合成逻辑（无成员表的版本）。"""
    if not operators:
        raise EngineError("旧版单文件版本必须提供 operators 参数")

    dataset_id = output_dataset_id or input_version.dataset_id

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
            llm_snapshot=llm_snapshot,
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        input_count = sum(1 for line in Path(input_path).open(encoding="utf-8") if line.strip())
        code, log = await _run_dj(yaml_path, job_id=job_id, llm_snapshot=llm_snapshot)
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
