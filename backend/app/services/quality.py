"""质量评估引擎:filter 算子编排 → 子进程跑 dj-analyze → stats_uri 回写输入版本。

镜像 engine.run_process_job 的子进程模式。DJ Analyzer 对 Filter 算子只
compute_stats 不删行(export_original_dataset 默认 False,只导出 stats);
stats 文件名遵循 Exporter 约定:export_path 为 data.jsonl 时落
data_stats.jsonl;固定 work_dir + job_id 后分析图表落 work_dir/analysis/。

质量评估**不产新版本**:数据不被修改,产物只有 stats + analysis 图表,
落 datasets/<ds>/quality/<job_id>/ 下,stats_uri 直接回写输入版本/成员
(重复评估同一版本时后评覆盖前评)。报告按输入版本查看。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.services.engine import (
    _get_version_members,
    _kill_proc_tree,
    _materialize_member,
    _read_head_records,
    _running_procs,
    _semaphore,
    build_config,
    detect_text_key,
)
from app.services.external_store import materialized_version

# 质量评估专用:质量评估不会跑 wordcloud 的失败模式与小样本/长文本列组合有关
# (dj-analyze 的 wordcloud 在整段重复 + 频次全 1 时报 ValueError 退出)。
# 用户不该感知 text_keys 字段,但平台需要给 DJ 一个稳定不踩坑的 text_key。
# "短列"=按惯例是短文本的字段名(问题/标题/句子等),不论数据集大小。
# 找不到再回退到 detect_text_key 兜底(以保持兼容特殊数据集),仍可能踩坑的
# 情形建议上层用 jobs 重跑 / 选用别的数据集。
_QUALITY_TEXT_KEY_PREFERRED = (
    "text",
    "title",
    "sentence",
    "question",
    "prompt",
    "document",
    "passage",
)


def detect_quality_text_key(records: list[dict[str, Any]]) -> str | None:
    """质量评估专用的主文本字段探测:只在「按惯例是短列」的字段名里挑,避免
    选到 answer / body / content / description / raw 这类长列触发 wordcloud
    ValueError 退出码 1。找不到时回退到通用 detect_text_key。
    """
    if not records:
        return None
    present: set[str] = set()
    for r in records[:50]:
        if isinstance(r, dict):
            present.update(k.lstrip("﻿") for k in r.keys() if isinstance(k, str))
    for cand in _QUALITY_TEXT_KEY_PREFERRED:
        if cand in present:
            return cand
    return detect_text_key(records)


class QualityError(RuntimeError):
    """质量评估执行失败(dj-analyze 非零退出 / 无 stats 产物)。"""


async def _run_dj_analyze(
    yaml_path: Path, *, job_id: str | None = None
) -> tuple[int, str]:
    """异步起 dj-analyze 子进程,返回 (退出码, 合并日志)。

    传 job_id 时把子进程登记进 engine._running_procs(供 terminate_job 停止/暂停),
    与 engine._run_dj 一致;进程结束即注销。
    """
    proc = await asyncio.create_subprocess_exec(
        settings.dj_analyze_bin,
        "--config",
        str(yaml_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # 自成进程组:停止/暂停时可整组杀,与 engine._run_dj 一致
        start_new_session=True,
    )
    if job_id is not None:
        _running_procs[job_id] = proc
    try:
        out, _ = await proc.communicate()
    except asyncio.CancelledError:
        # 请求被取消时别留下孤儿 dj-analyze 进程
        _kill_proc_tree(proc)
        await proc.wait()
        raise
    finally:
        if job_id is not None:
            _running_procs.pop(job_id, None)
    # communicate() 返回后 returncode 必非 None;被信号杀死时为负数,不能 or 0
    assert proc.returncode is not None
    return proc.returncode, out.decode("utf-8", "replace")


async def run_quality_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    member_configs: list[dict[str, Any]] | None = None,
    target_members: list[str] | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str]:
    """质量评估任务:对输入版本的指定成员运行质量评估算子,不产新版本,
    stats_uri 回写输入版本对应成员。

    operators: 统一应用到所有成员的算子列表（旧版兼容）
    member_configs: 新版成员独立配置，格式 [{member_name, operators, text_keys?}, ...]
    target_members: 要处理的成员名列表；None=处理所有成员
    llm_snapshot(可复现凭证):透传给 build_config,None 时行为不变(dj-analyze
    子进程本就不经 _subprocess_env 注入 LLM 环境,只影响 needs_api 算子的
    api_model 参数注入)。
    返回 (输入版本, 生成的 yaml 文本, 运行日志路径)。失败抛 QualityError。
    """
    # 1. 查询版本成员
    members = await _get_version_members(session, input_version.id)

    # 如果版本无成员表（旧版本），回退到原逻辑
    if not members:
        return await _run_quality_job_legacy(
            session,
            job_id=job_id,
            input_version=input_version,
            operators=operators,
            llm_snapshot=llm_snapshot,
        )

    # 2. 确定处理模式（复制 engine.py 的逻辑）
    if member_configs:
        config_map = {cfg["member_name"]: cfg for cfg in member_configs}
        members_to_process = [m for m in members if m.table_name in config_map]
        if not members_to_process:
            raise QualityError("未找到 member_configs 中指定的成员")
    else:
        if not operators:
            raise QualityError("未提供 member_configs 时必须提供 operators 参数")
        if target_members:
            members_to_process = [m for m in members if m.table_name in target_members]
        else:
            members_to_process = members
        if not members_to_process:
            raise QualityError("未找到要处理的成员")
        config_map = {
            m.table_name: {"operators": operators, "text_keys": None}
            for m in members_to_process
        }

    # 3. 评估工作目录(不产新版本,产物不落版本目录)
    dataset_id = input_version.dataset_id
    out_dir = Path(settings.datasets_dir) / dataset_id / "quality" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 4. 对每个成员独立处理
    all_logs: list[str] = []
    all_yamls: list[str] = []

    for member in members_to_process:
        member_cfg = config_map[member.table_name]
        member_operators = member_cfg["operators"]
        member_text_keys = member_cfg.get("text_keys")
        member_job_id = f"{job_id}-{member.table_name}"

        # 物化成员文件
        input_path = await _materialize_member(session, member)

        # 每个成员独立的 work_dir(以 member_job_id 结尾),避免 DJ 的
        # resolve_job_directories 因 work_dir 不以 job_id 结尾而自动再拼一层
        # job_id 子目录,导致 analysis/ 与 stats 文件不同级(见 _analysis_dir
        # 依赖 stats_path.parent / "analysis" 的假设)。
        member_work_dir = out_dir / member_job_id
        member_work_dir.mkdir(parents=True, exist_ok=True)

        # export_path 仅作 stats 文件命名锚点:dj-analyze 默认
        # export_original_dataset=False,不导出数据本身,只落
        # <table>_stats.jsonl + analysis/ 图表
        out_format = member.format if member.format in ("parquet", "jsonl") else "jsonl"
        output_path = member_work_dir / f"{member.table_name}.{out_format}"
        stats_path = output_path.parent / f"{member.table_name}_stats.jsonl"
        yaml_path = member_work_dir / f"{member.table_name}_job.yaml"

        # 构建 DJ Analyzer 配置(text_key 走质量评估专用探测,避开 wordcloud 崩溃)
        detected_key = (
            None
            if member_text_keys
            else detect_quality_text_key(_read_head_records(input_path, 50))
        )
        cfg = build_config(
            project_name=member_job_id,
            input_path=str(input_path),
            output_path=str(output_path),
            operators=member_operators,
            text_key=detected_key,
            text_keys=member_text_keys,
            llm_snapshot=llm_snapshot,
        )
        # 固定 work_dir + job_id：work_dir 已以 job_id 结尾,DJ 不再追加,
        # 分析产物稳定落在 member_work_dir/analysis/(与 stats_path 同级)。
        cfg["work_dir"] = member_work_dir.as_posix()
        cfg["job_id"] = member_job_id
        yaml_content = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_content, encoding="utf-8")

        all_yamls.append(f"# Member: {member.table_name}\n{yaml_content}")

        # 运行 dj-analyze
        async with _semaphore:
            code, log = await _run_dj_analyze(yaml_path, job_id=member_job_id)

        operator_names = [op["name"] for op in member_operators]
        all_logs.append(
            f"=== {member.table_name} ===\n算子: {operator_names}\n{log}"
        )

        if code != 0 or not stats_path.exists():
            tail = "\n".join(log.strip().splitlines()[-8:])
            raise QualityError(
                f"成员 {member.table_name} 质量评估失败"
                f"(dj-analyze 退出码 {code})\n{tail}"
            )

        # 评估不改数据、不产新版本:stats_uri 直接回写输入成员
        # (重复评估同一成员时后评覆盖前评)
        member.stats_uri = str(stats_path)

    # 5. 落血缘边并提交成员 stats_uri 回写
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()

    # 6. 汇总日志和YAML
    combined_log = "\n\n".join(all_logs)
    log_path = out_dir / "run.log"
    log_path.write_text(combined_log, encoding="utf-8")

    combined_yaml = "\n\n---\n\n".join(all_yamls)

    return input_version, combined_yaml, str(log_path)


async def _run_quality_job_legacy(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    text_keys: list[str] | None = None,
    llm_snapshot: dict[str, str | None] | None = None,
) -> tuple[DatasetVersion, str, str]:
    """旧版单文件质量评估逻辑（无成员表的版本）。"""
    if not operators:
        raise QualityError("旧版单文件版本必须提供 operators 参数")

    out_dir = (
        Path(settings.datasets_dir) / input_version.dataset_id / "quality" / job_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    export_path = out_dir / "data.jsonl"
    stats_path = out_dir / "data_stats.jsonl"
    yaml_path = out_dir / "job.yaml"
    log_path = out_dir / "run.log"

    async with materialized_version(input_version, session) as input_path:
        # text_keys 用户显式指定优先;留空则走质量评估专用探测(短列白名单),
        # 避免选到 answer / body / content 这类长列触发 dj-analyze wordcloud
        # 在小样本上 ValueError 退出。
        detected_key = None if text_keys else detect_quality_text_key(
            _read_head_records(input_path, 50)
        )
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(export_path),
            operators=operators,
            text_key=detected_key,
            text_keys=text_keys,
            llm_snapshot=llm_snapshot,
        )
        cfg["work_dir"] = out_dir.as_posix()
        cfg["job_id"] = job_id
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        code, log = await _run_dj_analyze(yaml_path, job_id=job_id)
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not stats_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise QualityError(f"dj-analyze 退出码 {code}\n{tail}")

    # 评估不产新版本:stats_uri 直接回写输入版本(后评覆盖前评)
    input_version.stats_uri = str(stats_path)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()

    return input_version, yaml_text, str(log_path)
