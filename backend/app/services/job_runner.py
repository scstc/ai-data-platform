"""加工任务后台执行编排:把任务放到后台 asyncio 任务里跑,支持停止 / 超时 / 重启回收。

为什么独立成模块:执行从「创建请求里同步跑完」改为「后台跑」后,需要一处统一管理
后台任务生命周期(spawn / 取消意图 / drain)与重启时的孤儿回收,且后台任务必须用
**独立 DB 会话**(请求会话在响应返回后即关闭)。子进程注册表与「杀进程」动作放在
engine.py(贴近子进程创建处),本模块只管编排与状态机。

状态机:pending(已建,排队等信号量)→ running(子进程已起)→ success | failed | cancelled。
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.schemas.job import JobCreate
from app.services import engine
from app.services.engine import EngineError, run_process_job
from app.services.external_store import ExternalStoreError

# 后台任务引用(防被 GC 回收)
_tasks: set[asyncio.Task] = set()
# 已请求停止的 job_id:让排队中的任务起跑前自动放弃、让被杀任务记 cancelled 而非 failed
_cancelled: set[str] = set()


class _Cancelled(Exception):
    """内部信号:任务在排队 / 起跑前已被请求停止。"""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _new_dataset_id() -> str:
    return f"dset-{secrets.token_hex(3)}"


def request_cancel(job_id: str) -> None:
    """登记停止意图(由 stop 端点调用,配合 engine.terminate_job 杀子进程)。"""
    _cancelled.add(job_id)


def spawn(job_id: str, body: JobCreate) -> None:
    """起一个后台任务执行该加工任务(立即返回,不等跑完)。"""
    task = asyncio.create_task(_run_job(job_id, body))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """等所有在跑的后台任务结束(测试用,确保断言时终态已落定)。"""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def reconcile_orphans(session: AsyncSession) -> int:
    """启动回收:把残留 pending/running 的任务标记失败(重启已中断其子进程)。返回条数。

    全部执行路径里只有加工任务异步后台跑;采集/质量/审核都是请求内同步跑完,正常不会
    残留 running。进程一旦重启,内存里的后台任务与子进程注册表全失,故凡是 pending/
    running 的都是孤儿,统一收口为 failed。
    """
    result = await session.execute(
        update(Job)
        .where(Job.state.in_(("pending", "running")))
        .values(state="failed", error="服务重启,任务中断", finished_at=_now())
    )
    await session.commit()
    return result.rowcount or 0


async def _run_job(job_id: str, body: JobCreate) -> None:
    """后台执行:排队(信号量)→ running → 跑算子流水线 → 落终态。

    用独立会话(请求会话已关闭)。被 terminate_job 杀掉的子进程会非零退出 →
    run_process_job 抛 EngineError,据 _cancelled 区分是「被停止(cancelled)」还是
    「真失败(failed)」。
    """
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            _cancelled.discard(job_id)
            return
        # 起跑前已被停止(stop 端点已把 DB 标 cancelled)→ 直接收尾,不再跑
        if job_id in _cancelled:
            _cancelled.discard(job_id)
            return
        input_version = await session.get(DatasetVersion, body.dataset_version_id)
        if input_version is None:
            job.state = "failed"
            job.error = "数据集版本不存在"
            job.finished_at = _now()
            await session.commit()
            _cancelled.discard(job_id)
            return

        # 另存为新数据集:在此构建(不入库),由 run_process_job 加工成功后随产物落库
        output_dataset = None
        if body.output_mode == "new_dataset":
            src = await session.get(Dataset, input_version.dataset_id)
            output_dataset = Dataset(
                id=_new_dataset_id(),
                name=(body.output_dataset_name or "").strip(),
                data_type=src.data_type if src else None,
                category_id=src.category_id if src else None,
                owner="admin",
                creator="admin",
            )

        try:
            # 并发信号量(engine 持有,跨模块按需取以便测试可整体重建)
            async with engine._semaphore:
                if job_id in _cancelled:  # 排队等信号量期间被停止
                    raise _Cancelled
                job.state = "running"
                job.started_at = _now()
                await session.commit()
                _version, yaml_text, log_path = await run_process_job(
                    session,
                    job_id=job_id,
                    input_version=input_version,
                    operators=[o.model_dump() for o in body.operators],
                    output_dataset=output_dataset,
                )
            job.state = "success"
            job.progress = 100
            job.config_yaml = yaml_text
            job.logs_uri = log_path
        except _Cancelled:
            job.state = "cancelled"
        except (EngineError, ExternalStoreError) as exc:
            if job_id in _cancelled:
                job.state = "cancelled"
            else:
                job.state = "failed"
                job.error = str(exc)
        finally:
            _cancelled.discard(job_id)
        job.finished_at = _now()
        await session.commit()
