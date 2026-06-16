"""加工引擎:把算子编排生成 data-juicer YAML → 子进程跑 dj-process → 产出新版本。

设计见 docs/plan/02(集成)与 03(模型)。后端(py3.12)通过子进程调用
data-juicer venv(py3.11)的 dj-process,进程隔离、规避版本冲突。
多 job 并发由信号量限流;单 job 内并行由 np 控制。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.services.external_store import (
    materialized_version,
    persist_manifest_output,
)
from app.services.landing import MANIFEST_FORMAT

# 多 job 并发上限
_semaphore = asyncio.Semaphore(settings.engine_concurrency)

# 运行中子进程注册表(单 worker 进程内有效):job_id -> dj-process 子进程。
# 由 _run_dj 在进程起止时维护,供 terminate_job 停止任务。
_running_procs: dict[str, asyncio.subprocess.Process] = {}


def _kill_proc_tree(proc: asyncio.subprocess.Process) -> None:
    """整组杀:连同 dj-process fork 出的子孙(如 uv/pip 装依赖)一起 SIGKILL。

    子进程以 start_new_session=True 自成进程组(pgid==pid),故对进程组发信号即可。
    只杀 dj 自己会留孤儿继续跑、还堵住 stdout 管道让 communicate() 卡死(漏临时目录 +
    占住并发槽)。进程已退出 / 取不到组 → 退回单进程杀,异常吞掉(已死即达成目的)。
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def terminate_job(job_id: str) -> bool:
    """杀掉某 job 正在跑的 dj-process 子进程(连同其子孙);返回是否确有进程被杀。"""
    proc = _running_procs.get(job_id)
    if proc is None:
        return False
    _kill_proc_tree(proc)
    return True


# 多模态引擎(torch)就绪缓存:媒体/manifest 加工需 torch,无则提前拦截而非运行时拉装
_multimodal_ready: bool | None = None


async def multimodal_ready() -> bool:
    """DJ venv 是否装了 torch(媒体/manifest 加工所需)。结果缓存,避免每次起子进程探测。

    纯文本加工不需 torch、永不调用本检查;只有 manifest 输入才据此门控,
    没装 torch 的部署直接 400,绝不让 DJ 运行时 ``uv pip install torch`` 卡死。
    """
    global _multimodal_ready
    if _multimodal_ready is not None:
        return _multimodal_ready
    dj_python = str(Path(settings.dj_process_bin).with_name("python"))
    try:
        proc = await asyncio.create_subprocess_exec(
            dj_python,
            "-c",
            "import torch",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except OSError:
        return False  # 探测本身失败(如 python 不存在)不缓存,下次再试
    _multimodal_ready = proc.returncode == 0
    return _multimodal_ready


class EngineError(RuntimeError):
    """加工执行失败(dj-process 非零退出 / 无产物 / 超时)。"""


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


async def _run_dj(yaml_path: Path, *, job_id: str | None = None) -> tuple[int, str]:
    """异步起 dj-process 子进程,返回 (退出码, 合并日志)。

    传 job_id 时把子进程登记进 _running_procs(供 terminate_job 停止);进程结束即注销。
    超过 settings.engine_job_timeout 秒(>0 时)则杀进程并抛 EngineError。
    """
    proc = await asyncio.create_subprocess_exec(
        settings.dj_process_bin,
        "--config",
        str(yaml_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # 自成进程组:停止/超时时可整组杀,连带 dj fork 出的子孙(uv/pip 等)
        start_new_session=True,
    )
    if job_id is not None:
        _running_procs[job_id] = proc
    timeout = settings.engine_job_timeout if settings.engine_job_timeout > 0 else None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_proc_tree(proc)
        await proc.wait()
        raise EngineError(
            f"加工超时(超过 {settings.engine_job_timeout}s),已终止"
        ) from None
    finally:
        if job_id is not None:
            _running_procs.pop(job_id, None)
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
    output_dataset: Dataset | None = None,
) -> tuple[DatasetVersion, str, str]:
    """对一个输入版本跑算子流水线 → 产出新版本。

    output_dataset 为 None(默认)时写回输入数据集,产出新版本(沿用历史行为);
    传入一个未持久化的 Dataset 时另存为新数据集,产物为其 v1——该 Dataset
    仅在加工成功时随产物一起入库,失败不会留下空数据集。

    返回 (新版本, 生成的 yaml 文本, 运行日志路径)。失败抛 EngineError。
    """
    dataset_id = output_dataset.id if output_dataset is not None else input_version.dataset_id
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

        # 并发信号量由调用方(job_runner)持有;此处只负责跑子进程(可被 terminate_job 停止)
        code, log = await _run_dj(yaml_path, job_id=job_id)

        # manifest 输入:产物里的媒体引用指向物化临时目录(用完即清),趁临时文件还在,
        # 把媒体回传平台 MinIO、清单改写为对象引用 → 产物仍是自包含的 manifest 版本。
        manifest_out: tuple[str, int, int] | None = None
        if code == 0 and out_path.exists() and input_version.format == MANIFEST_FORMAT:
            manifest_out = await persist_manifest_output(
                jsonl_path=out_path, dataset_id=dataset_id, version_no=new_vno
            )
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not out_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise EngineError(f"dj-process 退出码 {code}\n{tail}")

    if manifest_out is not None:
        # 媒体加工产出:storage_uri 指向平台 MinIO 上的清单,与媒体批量接入版本同形
        storage_uri, rows, size = manifest_out
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=new_vno,
            storage_uri=storage_uri,
            format=MANIFEST_FORMAT,
            rows=rows,
            size=size,
            origin="managed",
            produced_by_job_id=job_id,
            note=f"加工产出(来自 v{input_version.version_no})",
        )
    else:
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
    if output_dataset is not None:
        session.add(output_dataset)
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)
    return version, yaml_text, str(log_path)
