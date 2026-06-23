"""质量评估引擎:filter 算子编排 → 子进程跑 dj-analyze → 回写版本 stats_uri。

镜像 engine.run_process_job 的子进程模式。DJ Analyzer 对 Filter 算子只
compute_stats 不删行(export_original_dataset 默认 False,只导出 stats);
stats 文件名遵循 Exporter 约定:export_path 为 data.jsonl 时落
data_stats.jsonl;固定 work_dir + job_id 后分析图表落 work_dir/analysis/。
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
from app.services.engine import _kill_proc_tree, _running_procs, build_config
from app.services.external_store import materialized_version


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
    operators: list[dict[str, Any]],
) -> tuple[str, str]:
    """对输入版本逐条计算质量 stats(不删行、不产新版本)。

    成功后把 stats 文件路径写到输入版本的 stats_uri,并记 job_input 血缘边。
    返回 (生成的 yaml 文本, 运行日志路径)。失败抛 QualityError。
    """
    out_dir = (
        Path(settings.datasets_dir)
        / input_version.dataset_id
        / "quality"
        / job_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    # export_ds=False,export_path 仅决定 stats 文件名(data_stats.jsonl)
    export_path = out_dir / "data.jsonl"
    stats_path = out_dir / "data_stats.jsonl"
    yaml_path = out_dir / "job.yaml"
    log_path = out_dir / "run.log"

    # 输入经解析器拿本地路径:hosted 按需从 S3 拉取并规范化(临时),managed 透传。
    # stats 回写到 hosted 输入版本的 stats_uri,源不动。
    async with materialized_version(input_version, session) as input_path:
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(export_path),
            operators=operators,
        )
        # 固定 work_dir 与 job_id:work_dir 以 job_id 结尾时 DJ 不再追加时间戳
        # 目录,分析产物稳定落在 out_dir/analysis/
        cfg["work_dir"] = out_dir.as_posix()
        cfg["job_id"] = job_id
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        # 并发信号量由调用方(job_runner._run_job)统一持有;此处只负责跑子进程
        # (可被 terminate_job 停止/暂停——子进程登记进 _running_procs)
        code, log = await _run_dj_analyze(yaml_path, job_id=job_id)
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not stats_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise QualityError(f"dj-analyze 退出码 {code}\n{tail}")

    input_version.stats_uri = str(stats_path)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    return yaml_text, str(log_path)
