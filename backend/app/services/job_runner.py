"""任务后台执行编排:把任务放到后台 asyncio 任务里跑,支持暂停/继续/停止/超时/重启回收。

为什么独立成模块:执行从「创建请求里同步跑完」改为「后台跑」后,需要一处统一管理
后台任务生命周期(spawn / 暂停 / 停止意图 / drain)与重启时的孤儿回收,且后台任务
必须用**独立 DB 会话**(请求会话在响应返回后即关闭)。子进程注册表与「杀进程」动作
放在 engine.py(贴近子进程创建处),本模块只管编排与状态机。

状态机:
    pending(已建,排队等信号量)→ running(子进程已起)→ success | failed | cancelled
    pending/running --pause--> paused --resume(按 spec 从头重跑)--> pending
dj-process 子进程无原生暂停,故 pause=杀进程、resume=按 spec 重跑(不保留进度)。

分派方式:按 ``job.type`` 选 runner(process/clean/distillation/synthesis/augmentation/
quality/review),入参 body 一律从 ``job.spec`` 重建,故 spawn 只需 job_id —— 统一了
新建、重跑(rerun 由各类型 router 走 _start_* 建新记录)、继续(resume 复用原记录)。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory
from app.core.ids import uuid7_hex
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.schemas.job import JobCreate
from app.services import engine
from app.services.augment import run_augment_job
from app.services.capabilities import get_capabilities, get_dj_version
from app.services.construct import ConstructError, run_construct_job
from app.services.distillation import run_distillation_job
from app.services.engine import EngineError, run_process_job
from app.services.export_delivery import ExportError, run_export_job
from app.services.external_store import ExternalStoreError
from app.services.judge_runner import JudgeError, run_judge
from app.services.make import run_make_job
from app.services.quality import QualityError, run_quality_job
from app.services.review_runner import ReviewError, run_review

# 后台任务引用(防被 GC 回收)
_tasks: set[asyncio.Task] = set()
# job_id -> 后台协程:供 cancel_running_task 取消无子进程的运行中任务(如 review)
_task_by_job: dict[str, asyncio.Task] = {}
# 已请求停止的 job_id:让排队中的任务起跑前自动放弃、让被杀任务记 cancelled 而非 failed
_cancelled: set[str] = set()
# 已请求暂停的 job_id:同上,让被杀任务记 paused 而非 failed;resume 时清空
_paused: set[str] = set()


class _Cancelled(Exception):
    """内部信号:任务在排队 / 起跑前已被请求停止。"""


class _Paused(Exception):
    """内部信号:任务在排队 / 起跑前(或运行中被杀)已被请求暂停。"""


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _new_dataset_id() -> str:
    return f"dset-{uuid7_hex()}"


def request_cancel(job_id: str) -> None:
    """登记停止意图(由 stop 端点调用,配合 engine.terminate_job 杀子进程)。"""
    _cancelled.add(job_id)


def request_pause(job_id: str) -> None:
    """登记暂停意图(由 pause 端点调用,配合 engine.terminate_job 杀子进程)。"""
    _paused.add(job_id)


def cancel_running_task(job_id: str) -> bool:
    """取消某 job 正在跑的后台协程——供无子进程的任务(如 review 纯计算/LLM 扫描)
    暂停/停止:terminate_job 找不到子进程时改用此处取消协程。返回是否确有任务被取消。"""
    task = _task_by_job.get(job_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def body_from_spec(job: Job) -> Any:
    """按 ``job.type`` 把 ``job.spec`` 重建为对应 body schema;损坏抛 ValidationError。

    供 _run_job 在后台执行时重建入参,也供 resume 端点前置校验 spec 是否可继续。
    类型 schema 局部 import:避免本模块在 import 期与各 schema 形成环。
    """
    spec = job.spec or {}
    job_type = job.type
    if job_type in ("process", "clean"):
        return JobCreate.model_validate(spec)
    if job_type == "distillation":
        from app.schemas.distillation import DistillationJobCreate

        return DistillationJobCreate.model_validate(spec)
    if job_type == "synthesis":
        from app.schemas.make import MakeJobCreate

        return MakeJobCreate.model_validate(spec)
    if job_type == "augmentation":
        from app.schemas.augment import AugmentJobCreate

        return AugmentJobCreate.model_validate(spec)
    if job_type == "construct":
        from app.schemas.construct import ConstructJobCreate

        return ConstructJobCreate.model_validate(spec)
    if job_type == "judge":
        from app.schemas.eval import JudgeJobCreate

        return JudgeJobCreate.model_validate(spec)
    if job_type == "export":
        from app.schemas.export import ExportJobCreate

        return ExportJobCreate.model_validate(spec)
    if job_type == "quality":
        from app.schemas.job import QualityJobCreate

        body = QualityJobCreate.model_validate(spec)
        # 小样本 wordcloud 边界规避:dj-analyze 的 wordcloud 在 rows 极少 + 长文本
        # 重复字段时,token 频次全为 1,layout 失败直接 ValueError 退出码 1。
        # rows < 阈值时强制不指定 text_keys,让 DJ 自动探测主文本字段(走短列
        # 路径,绕开 wordcloud 崩溃)。重跑/继续也走这里,与 create_quality_job 拦截
        # 行为一致。需 session 拿到 input_version.rows,故延迟到 _run_job 里做。
        return body
    if job_type == "review":
        from app.schemas.review import ReviewJobCreate

        return ReviewJobCreate.model_validate(spec)
    raise ValueError(f"不支持后台执行的任务类型:{job_type}")


def spawn(job_id: str) -> None:
    """起一个后台任务执行该任务(立即返回,不等跑完)。

    入参 body 由 _run_job 从 ``job.spec`` 重建(按 job.type 分派),故此处只需 job_id ——
    统一了新建 / 重跑 / 继续:它们都只负责把 Job 行落到正确状态,执行路径只有这一条。
    """
    task = asyncio.create_task(_run_job(job_id))
    _tasks.add(task)
    _task_by_job[job_id] = task

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        _task_by_job.pop(job_id, None)

    task.add_done_callback(_done)


async def drain() -> None:
    """等所有在跑的后台任务结束(测试用,确保断言时终态已落定)。"""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def reconcile_orphans(session: AsyncSession) -> int:
    """启动回收:把残留 pending/running 的任务标记失败(重启已中断其子进程)。返回条数。

    paused 不回收——那是用户主动暂停的意图,重启后应保持 paused 等用户继续。
    """
    result = await session.execute(
        update(Job)
        .where(Job.state.in_(("pending", "running")))
        .values(state="failed", error="服务重启,任务中断", finished_at=_now())
    )
    await session.commit()
    return result.rowcount or 0


async def _run_job(job_id: str) -> None:
    """后台执行:重建入参 → 排队(信号量)→ running → 按 type 跑 → 落终态。

    用独立会话(请求会话已关闭)。被 terminate_job 杀掉的子进程会非零退出 →
    runner 抛 EngineError/QualityError/ReviewError,据 _cancelled/_paused 区分是
    「被停止(cancelled)」「被暂停(paused)」还是「真失败(failed)」。

    pause/resume 语义:dj-process 无原生暂停,暂停=杀子进程并置 paused、保留 spec;
    继续(resume)= 复用同一 Job 行重置 pending 后再次 spawn,按 spec 从头重跑。
    """
    async with async_session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None:
            _cancelled.discard(job_id)
            _paused.discard(job_id)
            return
        # 起跑前已被停止/暂停(端点已把 DB 标记)→ 直接收尾,不再跑
        if job_id in _cancelled:
            _cancelled.discard(job_id)
            _paused.discard(job_id)
            return
        if job_id in _paused:
            _paused.discard(job_id)
            return
        try:
            body = body_from_spec(job)
        except (ValidationError, ValueError) as exc:
            job.state = "failed"
            job.error = f"任务配置已损坏:{exc}"
            job.finished_at = _now()
            await session.commit()
            _cancelled.discard(job_id)
            _paused.discard(job_id)
            return
        input_version = await session.get(DatasetVersion, body.dataset_version_id)
        if input_version is None:
            job.state = "failed"
            job.error = "数据集版本不存在"
            job.finished_at = _now()
            await session.commit()
            _cancelled.discard(job_id)
            _paused.discard(job_id)
            return

        yaml_text: str | None = None
        log_path: str | None = None
        try:
            # 并发信号量(engine 持有,跨模块按需取以便测试可整体重建)。
            # 各 runner(含 quality/review)不再自行获取信号量,统一由此处持有,
            # 避免同一协程二次获取 asyncio.Semaphore(非重入)导致死锁。
            async with engine._semaphore:
                if job_id in _cancelled:  # 排队等信号量期间被停止
                    raise _Cancelled
                if job_id in _paused:  # 排队等信号量期间被暂停
                    raise _Paused
                job.state = "running"
                job.started_at = _now()
                # 可复现凭证(G18):记录执行环境;随本次最终 commit 落库(不另起 commit)。
                _caps = get_capabilities()
                job.dj_version = get_dj_version()
                job.image_tag = settings.image_tag
                job.executor_type = "ray" if _caps.ray else "single"
                await session.commit()

                # 统一提取配置：优先 member_configs，回退到 operators+target_members
                member_configs_arg: list[dict[str, Any]] | None = None
                operators_arg: list[dict[str, Any]] | None = None
                target_members_arg: list[str] | None = None

                member_configs = getattr(body, "member_configs", None)
                if member_configs:
                    # 新版：成员独立配置
                    member_configs_arg = [cfg.model_dump() for cfg in member_configs]
                else:
                    # 旧版：统一配置
                    operators = getattr(body, "operators", None)
                    if operators:
                        operators_arg = [o.model_dump() for o in operators]
                    target_members_arg = getattr(body, "target_members", None)

                if job.type == "distillation":
                    _v, yaml_text, log_path, _report = await run_distillation_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                    )
                elif job.type == "synthesis":
                    _v, yaml_text, log_path, _report = await run_make_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                    )
                elif job.type == "augmentation":
                    _v, yaml_text, log_path, _report = await run_augment_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                    )
                elif job.type == "quality":
                    _version, yaml_text, log_path = await run_quality_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        text_keys=getattr(body, "text_keys", None),
                    )
                elif job.type == "construct":
                    # 构造层:确定性列映射 → 训练 schema,无 operators
                    _v, yaml_text, log_path, _report = await run_construct_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                    )
                elif job.type == "judge":
                    # 裁判:对待评版本逐行 LLM-as-judge 打分,产 eval_results + 报告
                    # config 用 snake_case key(judge_runner 按 snake 读)
                    await run_judge(
                        session,
                        job=job,
                        version=input_version,
                        config=body.config.model_dump(),
                    )
                elif job.type == "export":
                    # 交付:治理后版本 → 训练三件套落 S3(不产新版本)
                    log_path, _report = await run_export_job(
                        session,
                        job_id=job_id,
                        version=input_version,
                        goal=body.goal,
                    )
                elif job.type == "review":
                    # review 无 yaml/日志产物;run_review 内部落命中 + 打标版本 + 回写报告
                    await run_review(
                        session,
                        job=job,
                        version=input_version,
                        config=body.config.model_dump(by_alias=True),
                    )
                else:  # process / clean
                    # process/clean 已支持 member_configs，使用统一提取的配置
                    _v, yaml_text, log_path = await run_process_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        text_keys=getattr(body, "text_keys", None),
                        use_ray=getattr(body, "use_ray", False),
                        media_keys={
                            "image_key": getattr(body, "image_key", None),
                            "audio_key": getattr(body, "audio_key", None),
                            "video_key": getattr(body, "video_key", None),
                        },
                        target_members=target_members_arg,
                        member_configs=member_configs_arg,
                    )
            job.state = "success"
            job.progress = 100
            if yaml_text is not None:
                # 落库展示版:剥掉内部中转路径等运行期键,用户只看算子配方
                job.config_yaml = engine.config_yaml_for_display(yaml_text)
            if log_path is not None:
                job.logs_uri = log_path
        except _Paused:
            job.state = "paused"
        except _Cancelled:
            job.state = "cancelled"
        except asyncio.CancelledError:
            # 无子进程的任务(review 等)被 cancel_running_task 取消:按意图落终态
            if job_id in _cancelled:
                job.state = "cancelled"
            elif job_id in _paused:
                job.state = "paused"
            else:
                job.state = "failed"
                job.error = "任务被取消"
        except (
            ConstructError,
            EngineError,
            ExportError,
            ExternalStoreError,
            JudgeError,
            QualityError,
            ReviewError,
        ) as exc:
            if job_id in _cancelled:
                job.state = "cancelled"
            elif job_id in _paused:
                job.state = "paused"
            else:
                job.state = "failed"
                job.error = str(exc)
        finally:
            _cancelled.discard(job_id)
            _paused.discard(job_id)
        job.finished_at = _now()
        # 终态通知:仅 success/failed 给创建者写一条(不通知 cancelled/paused——
        # 那是用户主动操作)。惰性引入避免与 services 包潜在导入环;emit 内部已
        # loud-swallow,通知失败绝不影响任务终态(随本事务一起 commit)。
        if job.state in ("success", "failed"):
            from app.services import notifications  # noqa: PLC0415

            notifications.emit(
                session,
                recipient=job.created_by,
                level="success" if job.state == "success" else "error",
                source_type="job",
                source_id=job.id,
                title=(
                    f"{job.name} 已完成"
                    if job.state == "success"
                    else f"{job.name} 失败"
                ),
                body=job.error if job.state == "failed" else None,
            )
        await session.commit()
