"""数据集生命周期:过期打删除标记 + 级联隐藏 + 回收站恢复(#19 收口)。

语义:
- 过期口径与 /datasets/expiring 一致:``valid_until.date() < today``
  (有效期当天仍可用,次日起算过期)。
- 打标不删数据:datasets.deleted_at/deleted_reason 落标记,普通接口一律
  过滤不可见;仅超管经回收站可见/恢复。
- 级联:输入或产出涉及该数据集的 job、绑定它的采集任务(ingest_tasks.dataset_id)
  一并打标,``deleted_by_dataset_id`` 记录归因——恢复时只解除因它标记的,
  共享任务(同时关联其他仍在回收站的数据集)经恢复后的重扫会被重新归因打标。
- 恢复即续期:清标后 valid_until 置为 now+1 自然月,否则下次扫描立即再次打标。

触发路径(三者结合,不依赖调度器在线):
- lifespan 启动补扫(main.py,best-effort);
- 调度器每日扫描(scheduler 在线时,作业 id ``lifecycle:expire``);
- 数据集列表/详情查询时对已过期未打标的即时补打(仅本体,级联留给扫描)。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory
from app.models.dataset import Dataset, _default_valid_until
from app.models.dataset_version import DatasetVersion
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.job_input import JobInput

_logger = logging.getLogger(__name__)

# 调度器作业 id(与 ingest:* 前缀区分,reconcile 只清理 ingest:* 孤儿)
EXPIRE_JOB_ID = "lifecycle:expire"


def _now() -> datetime:
    """naive-UTC now,与 Dataset.valid_until 及 job_runner 口径一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _expiry_cutoff(now: datetime) -> datetime:
    """过期判定线:valid_until < 今日零点 即过期(有效期当天仍可用)。"""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _related_job_ids(session: AsyncSession, dataset_id: str) -> set[str]:
    """该数据集关联的 job:产出它任一版本的 + 消费它任一版本做输入的。

    消费侧除 job_inputs 血缘边外,还按 spec 里的输入版本反查——失败/取消的
    任务不写血缘边(见 jobs._build_input 的回退逻辑),只靠边会漏掉它们。
    """
    version_ids = select(DatasetVersion.id).where(
        DatasetVersion.dataset_id == dataset_id
    )
    produced = await session.scalars(
        select(DatasetVersion.produced_by_job_id).where(
            DatasetVersion.dataset_id == dataset_id,
            DatasetVersion.produced_by_job_id.is_not(None),
        )
    )
    consumed = await session.scalars(
        select(JobInput.job_id).where(
            JobInput.dataset_version_id.in_(version_ids)
        )
    )
    # spec 蛇形键为主(model_dump 默认),兼容 camelCase 旧数据
    spec_matched = await session.scalars(
        select(Job.id).where(
            or_(
                Job.spec.op("->>")("dataset_version_id").in_(version_ids),
                Job.spec.op("->>")("datasetVersionId").in_(version_ids),
            )
        )
    )
    return set(produced) | set(consumed) | set(spec_matched)


async def _cascade_mark(
    session: AsyncSession, dataset_id: str, now: datetime
) -> int:
    """把该数据集关联的 job / 采集任务打上级联标记(已打标的不动)。"""
    marked = 0
    job_ids = await _related_job_ids(session, dataset_id)
    if job_ids:
        result = await session.execute(
            update(Job)
            .where(Job.id.in_(job_ids), Job.deleted_at.is_(None))
            .values(deleted_at=now, deleted_by_dataset_id=dataset_id)
        )
        marked += result.rowcount or 0
    result = await session.execute(
        update(IngestTask)
        .where(
            IngestTask.dataset_id == dataset_id,
            IngestTask.deleted_at.is_(None),
        )
        .values(deleted_at=now, deleted_by_dataset_id=dataset_id)
    )
    marked += result.rowcount or 0
    return marked


async def mark_expired_datasets(session: AsyncSession) -> int:
    """扫描过期未打标的数据集:打删除标记并级联;返回打标的数据集数。

    调用方负责 commit。启动补扫 / 每日调度共用。
    """
    now = _now()
    expired = (
        await session.scalars(
            select(Dataset).where(
                Dataset.deleted_at.is_(None),
                Dataset.valid_until.is_not(None),
                Dataset.valid_until < _expiry_cutoff(now),
            )
        )
    ).all()
    for ds in expired:
        ds.deleted_at = now
        ds.deleted_reason = "expired"
        cascaded = await _cascade_mark(session, ds.id, now)
        _logger.info(
            "数据集 %s(%s) 过期打删除标记,级联标记关联任务 %d 个",
            ds.id,
            ds.name,
            cascaded,
        )
    return len(expired)


async def restore_dataset(session: AsyncSession, dataset: Dataset) -> int:
    """回收站恢复:清标 + 续期(+1 自然月) + 级联恢复归因它的任务。

    共享任务防误恢复:恢复后对仍在回收站的数据集重跑级联,把"同时也关联
    其他已删数据集"的任务重新归因打标。返回净恢复的任务数(恢复数-重标数)。
    调用方负责 commit。
    """
    now = _now()
    dataset.deleted_at = None
    dataset.deleted_reason = None
    dataset.valid_until = _default_valid_until()

    restored = 0
    for model in (Job, IngestTask):
        result = await session.execute(
            update(model)
            .where(model.deleted_by_dataset_id == dataset.id)
            .values(deleted_at=None, deleted_by_dataset_id=None)
        )
        restored += result.rowcount or 0

    # 重扫仍在回收站的数据集:共享任务重新归因打标
    still_deleted = (
        await session.scalars(
            select(Dataset.id).where(Dataset.deleted_at.is_not(None))
        )
    ).all()
    remarked = 0
    for ds_id in still_deleted:
        remarked += await _cascade_mark(session, ds_id, now)
    return restored - remarked


async def lazy_mark_expired(session: AsyncSession) -> int:
    """查询路径的惰性补打:过期未打标的数据集就地打标(仅本体,级联留给扫描)。

    数据集列表/详情查询前调用,保证"过期即不可见"不依赖调度器实时性。
    set-based UPDATE,无行命中时零成本;返回打标数,>0 时调用方需 commit。
    """
    now = _now()
    result = await session.execute(
        update(Dataset)
        .where(
            Dataset.deleted_at.is_(None),
            Dataset.valid_until.is_not(None),
            Dataset.valid_until < _expiry_cutoff(now),
        )
        .values(deleted_at=now, deleted_reason="expired")
    )
    return result.rowcount or 0


async def run_expire_scan() -> None:
    """调度器每日扫描入口(请求上下文外,自建 session 并 commit)。"""
    async with async_session_factory() as session:
        count = await mark_expired_datasets(session)
        await session.commit()
        if count:
            _logger.info("过期扫描完成:打标数据集 %d 个", count)
