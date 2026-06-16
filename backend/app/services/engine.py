"""加工引擎:把算子编排生成 data-juicer YAML → 子进程跑 dj-process → 产出新版本。

设计见 docs/plan/02(集成)与 03(模型)。后端(py3.12)通过子进程调用
data-juicer venv(py3.11)的 dj-process,进程隔离、规避版本冲突。
多 job 并发由信号量限流;单 job 内并行由 np 控制。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.services.external_store import materialized_version

# 多 job 并发上限
_semaphore = asyncio.Semaphore(settings.engine_concurrency)


class EngineError(RuntimeError):
    """加工执行失败(dj-process 非零退出 / 无产物)。"""


def _new_version_id() -> str:
    return f"dsv-{secrets.token_hex(3)}"


def build_config(
    *,
    project_name: str,
    input_path: str,
    output_path: str,
    operators: list[dict[str, Any]],
) -> dict[str, Any]:
    """把算子编排序列化为 data-juicer 合法配置(dict)。

    operators: [{name, params}]; 无参算子 process 项值为 None(DJ 接受)。
    """
    process: list[dict[str, Any]] = []
    for op in operators:
        params = op.get("params") or {}
        process.append({op["name"]: (params or None)})
    # 路径统一正斜杠:DJ 用 POSIX shlex 解析 dataset_path,Windows 反斜杠
    # 会被当转义符吞掉,路径残缺后被误判成 huggingface 数据集
    return {
        "project_name": project_name,
        "dataset_path": Path(input_path).as_posix(),
        "np": settings.engine_np,
        "export_path": Path(output_path).as_posix(),
        "process": process,
    }


async def _run_dj(yaml_path: Path) -> tuple[int, str]:
    """异步起 dj-process 子进程,返回 (退出码, 合并日志)。"""
    proc = await asyncio.create_subprocess_exec(
        settings.dj_process_bin,
        "--config",
        str(yaml_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


def _read_jsonl_head(path: Path, limit: int) -> list[dict[str, Any]]:
    """读 jsonl 文件前 limit 个非空行,逐行 json.loads。limit<=0 时读全部。"""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def _column_union(*row_lists: list[dict[str, Any]]) -> list[str]:
    """按出现顺序求多组样本的键并集(dict.fromkeys 保序去重)。"""
    keys: dict[str, None] = {}
    for rows in row_lists:
        for row in rows:
            keys.update(dict.fromkeys(row.keys()))
    return list(keys)


async def run_preview(
    session: AsyncSession,
    *,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
    sample_size: int = 20,
) -> dict[str, Any]:
    """在输入版本的前 sample_size 行上试跑算子流水线,返回加工前后样本。

    全程在临时目录内完成,不建 DatasetVersion、不写 DB。失败抛 EngineError。
    返回 {before, after, beforeCount, afterCount, columns}。

    输入经 materialized_version 解析:受管版本直接用本地路径,hosted 版本按需
    从 S3 拉取并规范化为临时 jsonl(用完即清理)。
    """
    # 经解析器拿本地可读路径(hosted 在此下载;managed 透传本地路径)
    async with materialized_version(input_version, session) as src_path:
        # 输入数据文件缺失 → 抛 EngineError(让上层转 400,不让 FileNotFound 冒成 500)
        if not src_path.exists():
            raise EngineError(f"输入版本数据文件不存在:{input_version.storage_uri}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out_path = tmp_dir / "data.jsonl"
            yaml_path = tmp_dir / "job.yaml"
            # 试跑样例必须与 src 同目录:多模态 manifest 的相对媒体路径由 DJ rel2abs
            # 按 jsonl 所在目录解析,媒体文件就在 src 旁(物化时下载),另起目录会找不到。
            sample_path = (
                src_path.parent / f"preview-sample-{secrets.token_hex(4)}.jsonl"
            )

            # 读输入版本前 sample_size 个非空行:既落盘成试跑输入,也作 before 展示
            before = _read_jsonl_head(src_path, sample_size)
            sample_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n" for row in before
                ),
                encoding="utf-8",
            )

            try:
                cfg = build_config(
                    project_name="preview",
                    input_path=str(sample_path),
                    output_path=str(out_path),
                    operators=operators,
                )
                yaml_path.write_text(
                    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )

                async with _semaphore:
                    code, log = await _run_dj(yaml_path)

                if code != 0 or not out_path.exists():
                    tail = "\n".join(log.strip().splitlines()[-8:])
                    raise EngineError(f"dj-process 退出码 {code}\n{tail}")

                # 产出总行数(全量统计),after 仅取前 sample_size 条用于展示
                after_count = sum(
                    1 for line in out_path.open(encoding="utf-8") if line.strip()
                )
                after = _read_jsonl_head(out_path, sample_size)
                columns = _column_union(before, after)
            finally:
                sample_path.unlink(missing_ok=True)

    return {
        "before": before,
        "after": after,
        "beforeCount": len(before),
        "afterCount": after_count,
        "columns": columns,
    }


async def run_process_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
) -> tuple[DatasetVersion, str, str]:
    """对一个输入版本跑算子流水线 → 在同一数据集下产出新版本。

    返回 (新版本, 生成的 yaml 文本, 运行日志路径)。失败抛 EngineError。
    """
    dataset_id = input_version.dataset_id
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

    # 输入经解析器拿本地路径:hosted 按需从 S3 拉取并规范化(临时),managed 透传。
    # 产出仍写受管存储(origin=managed),源不动;血缘 JobInput 指向 hosted 输入版本。
    async with materialized_version(input_version, session) as input_path:
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(out_path),
            operators=operators,
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        async with _semaphore:
            code, log = await _run_dj(yaml_path)
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not out_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise EngineError(f"dj-process 退出码 {code}\n{tail}")

    rows = sum(1 for line in out_path.open(encoding="utf-8") if line.strip())
    stats_path = out_dir / "data_stats.jsonl"
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=str(out_path),
        stats_uri=str(stats_path) if stats_path.exists() else None,
        format="jsonl",
        rows=rows,
        size=out_path.stat().st_size,
        origin="managed",
        produced_by_job_id=job_id,
        note=f"加工产出(来自 v{input_version.version_no})",
    )
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)
    return version, yaml_text, str(log_path)
