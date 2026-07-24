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
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
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
from app.services.llm_config import snapshot_active_llm_config
from app.services.make import run_make_job
from app.services.quality import QualityError, run_quality_job
from app.services.review_runner import ReviewError, run_review
from app.services.trainset import run_trainset_job

logger = logging.getLogger(__name__)

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
    if job_type == "trainset":
        from app.schemas.trainset import TrainsetJobCreate

        return TrainsetJobCreate.model_validate(spec)
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


async def _mark_queued(job_id: str) -> None:
    """worker 模式入队:把 pending 任务的 queued_at 置为当前时间(仅记排队时刻)。

    以 ``state=='pending'`` + ``queued_at IS NULL`` 为条件,故 worker 抢先认领
    (state 已变)时本更新自然 no-op,不会覆盖运行态。best-effort:失败仅告警,
    queued_at 缺失不影响 worker 认领(认领按 created_at 兜底排序)。
    """
    try:
        async with async_session_factory() as session:
            await session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.state == "pending",
                    Job.queued_at.is_(None),
                )
                .values(queued_at=datetime.now(UTC))
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("job %s 入队标记 queued_at 失败(已忽略)", job_id, exc_info=True)


def spawn(job_id: str) -> None:
    """起一个后台任务执行该任务(立即返回,不等跑完)。

    入参 body 由 _run_job 从 ``job.spec`` 重建(按 job.type 分派),故此处只需 job_id ——
    统一了新建 / 重跑 / 继续:它们都只负责把 Job 行落到正确状态,执行路径只有这一条。

    执行模式(settings.job_execution_mode):
    - inline(默认):本进程起 asyncio 协程执行,行为与历史一致。
    - worker:不进程内执行,只异步标记 queued_at 入队,交独立 worker 进程
      (python -m app.worker)认领执行。job 行已由调用方 commit 为 pending。
    """
    if settings.job_execution_mode == "worker":
        task = asyncio.create_task(_mark_queued(job_id))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return

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
    顺带清扫 datasets_dir/.staging 下的孤儿目录(见 `_cleanup_staging_orphans`)。
    """
    result = await session.execute(
        update(Job)
        .where(Job.state.in_(("pending", "running")))
        .values(state="failed", error="服务重启,任务中断", finished_at=_now())
    )
    await session.commit()
    _cleanup_staging_orphans()
    return result.rowcount or 0


def _cleanup_staging_orphans() -> None:
    """清空 datasets_dir/.staging 下的残留目录(供 reconcile_orphans 在启动时调用)。

    staging 目录(`engine._new_staging_dir`)只在单次引擎执行期间存在,函数返回
    前必 `shutil.rmtree` 清理;重启意味着上次进程内所有引擎子进程均已中断,
    残留目录只可能是崩溃 / kill -9 留下的半成品,不可能还有任务在用,直接
    全清。best-effort:单个目录清理失败只记 warning,不影响其余目录 / 启动流程。
    """
    staging_root = Path(settings.datasets_dir) / ".staging"
    if not staging_root.is_dir():
        return
    cleaned = 0
    for child in staging_root.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            cleaned += 1
        except OSError:
            logger.warning("清理孤儿 staging 目录失败:%s", child)
    logger.info("启动回收:清理 %d 个孤儿 staging 目录(%s)", cleaned, staging_root)


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
                # 可复现凭证:LLM 端点/模型固化进 spec(判据用「键不存在」而非
                # 值空——None 也是要锁定的语义,老任务重跑一次即回填)。
                if "llm_snapshot" not in (job.spec or {}):
                    job.spec = {
                        **(job.spec or {}),
                        "llm_snapshot": snapshot_active_llm_config(),
                    }
                await session.commit()
                llm_snapshot = (job.spec or {}).get("llm_snapshot")

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

                # LLM 生成类(distillation/synthesis/augmentation/trainset)会被
                # 赋值,统一做空产出兜底;其他 type 保持 None
                gen_report = None
                if job.type == "distillation":
                    _v, yaml_text, log_path, gen_report = await run_distillation_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                        llm_snapshot=llm_snapshot,
                    )
                elif job.type == "synthesis":
                    _v, yaml_text, log_path, gen_report = await run_make_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                        llm_snapshot=llm_snapshot,
                    )
                elif job.type == "augmentation":
                    _v, yaml_text, log_path, gen_report = await run_augment_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                        llm_snapshot=llm_snapshot,
                    )
                elif job.type == "trainset":
                    _v, yaml_text, log_path, gen_report = await run_trainset_job(
                        session,
                        job_id=job_id,
                        input_version=input_version,
                        operators=operators_arg,
                        member_configs=member_configs_arg,
                        target_members=target_members_arg,
                        goal=body.goal,
                        output_dataset_id=body.output_dataset_id,
                        text_keys=getattr(body, "text_keys", None),
                        llm_snapshot=llm_snapshot,
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
                        llm_snapshot=llm_snapshot,
                        produce_version=getattr(body, "produce_version", False),
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
                        llm_snapshot=llm_snapshot,
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
                    # review 无 yaml/日志产物;run_review 内部落命中 + 打标版本
                    # + 回写报告
                    await run_review(
                        session,
                        job=job,
                        version=input_version,
                        config=body.config.model_dump(by_alias=True),
                        target_members=target_members_arg,
                        llm_snapshot=llm_snapshot,
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
                        llm_snapshot=llm_snapshot,
                    )
            job.state = "success"
            job.progress = 100
            if yaml_text is not None:
                # 落库展示版:剥掉内部中转路径等运行期键,用户只看算子配方
                job.config_yaml = engine.config_yaml_for_display(yaml_text)
            if log_path is not None:
                job.logs_uri = log_path
            # LLM 生成类任务空产物兜底:执行引擎不抛错但产出 0 行 = LLM 全失败
            # (如 key 缺失/额度耗尽逐样本被 DJ 跳过),不能算成功,否则下游会拿到
            # 空版本当正常数据用。output_count 只计生成产物,不含原样结转成员。
            if gen_report is not None and gen_report.output_count == 0:
                job.state = "failed"
                job.error = (
                    "生成产出为 0 行,"
                    + (
                        "; ".join(gen_report.warnings)
                        if gen_report.warnings
                        else "可能 LLM 调用失败或 prompt 不匹配"
                    )
                )
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
        except Exception as exc:
            # 兜底:未预期异常若不落终态,协程死亡后任务将永远停在 running
            logger.exception("job %s 未预期异常", job_id)
            job.state = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
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
        try:
            await session.commit()
        except Exception:
            # 兜底:上面的业务异常若已导致本会话事务 aborted(某 runner 内部
            # flush 冲突/异常未 rollback 直接冒泡),即便已把终态写进 job 对象,
            # 这次 commit 仍会失败(如 PendingRollbackError)——终态因此丢失,
            # 任务会永远停在 running。这里保留已算出的终态(不用本次 commit
            # 失败的异常覆盖原始业务错误),rollback 当前失效会话后换一个全新
            # 会话重写,确保 failed/success 等终态必落库。
            logger.exception(
                "job %s 终态提交失败(状态 %s),换新会话重写终态", job_id, job.state
            )
            final_state = job.state
            final_error = job.error
            final_finished_at = job.finished_at
            await session.rollback()
            try:
                async with async_session_factory() as fresh_session:
                    fresh_job = await fresh_session.get(Job, job_id)
                    if fresh_job is not None:
                        fresh_job.state = final_state
                        fresh_job.error = final_error
                        fresh_job.finished_at = final_finished_at
                        await fresh_session.commit()
            except Exception:
                # 兜底的兜底:新会话仍提交失败(如 DB 本身不可用),已无更多手段——
                # 记 critical 明确暴露"任务将残留 running",不再吞掉。
                logger.critical(
                    "job %s 终态兜底重写仍失败,任务将残留 running 态", job_id
                )
