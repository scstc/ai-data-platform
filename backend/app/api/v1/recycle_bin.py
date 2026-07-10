"""回收站路由(仅超管):过期打删除标记的数据集列表 + 恢复。

- GET  /recycle-bin/datasets            分页列出已打标数据集(含级联任务数)
- POST /recycle-bin/datasets/{id}/restore 恢复:清标 + 续期 +1 自然月 +
  级联恢复归因它的任务(共享任务重扫防误恢复,见 dataset_lifecycle)

门控 require_admin(越权防护在后端;前端按钮另有 system:recycle:* 权限码)。
审计由 audit_middleware 统一记录。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.schemas.common import CamelModel, PageResponse
from app.services import dataset_lifecycle

router = APIRouter(tags=["recycle-bin"], dependencies=[Depends(require_admin)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class RecycledDatasetRead(CamelModel):
    """回收站条目:数据集元信息 + 打标信息 + 级联任务计数。"""

    id: str
    name: str
    owner: str
    creator: str
    valid_until: datetime | None = None
    deleted_at: datetime
    deleted_reason: str | None = None
    # 因该数据集被级联隐藏的加工任务 / 采集任务数
    cascaded_jobs: int = 0
    cascaded_ingest_tasks: int = 0


@router.get(
    "/recycle-bin/datasets",
    response_model=PageResponse[RecycledDatasetRead],
)
async def list_recycled_datasets(
    session: SessionDep,
    current: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, alias="pageSize"),
    name: str | None = Query(None),
) -> PageResponse[RecycledDatasetRead]:
    """分页列出已打删除标记的数据集,按打标时间倒序;name 模糊过滤。

    列出前先补扫一次(与数据集列表同口径),保证刚过期的立刻进回收站。
    """
    if await dataset_lifecycle.mark_expired_datasets(session):
        await session.commit()
    conds = [Dataset.deleted_at.is_not(None)]
    if name:
        conds.append(Dataset.name.ilike(f"%{name}%"))
    total = await session.scalar(
        select(func.count()).select_from(Dataset).where(*conds)
    )
    rows = (
        await session.scalars(
            select(Dataset)
            .where(*conds)
            .order_by(Dataset.deleted_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    # 批量取本页级联任务计数(避免 N+1)
    page_ids = [r.id for r in rows]
    job_counts: dict[str, int] = {}
    task_counts: dict[str, int] = {}
    if page_ids:
        job_counts = dict(
            (
                await session.execute(
                    select(Job.deleted_by_dataset_id, func.count())
                    .where(Job.deleted_by_dataset_id.in_(page_ids))
                    .group_by(Job.deleted_by_dataset_id)
                )
            ).all()
        )
        task_counts = dict(
            (
                await session.execute(
                    select(IngestTask.deleted_by_dataset_id, func.count())
                    .where(IngestTask.deleted_by_dataset_id.in_(page_ids))
                    .group_by(IngestTask.deleted_by_dataset_id)
                )
            ).all()
        )
    data = []
    for r in rows:
        item = RecycledDatasetRead.model_validate(r)
        item.cascaded_jobs = job_counts.get(r.id, 0)
        item.cascaded_ingest_tasks = task_counts.get(r.id, 0)
        data.append(item)
    return PageResponse[RecycledDatasetRead](data=data, total=total or 0)


@router.post("/recycle-bin/datasets/{dataset_id}/restore")
async def restore_recycled_dataset(
    dataset_id: str,
    session: SessionDep,
) -> JSONResponse:
    """恢复数据集:清删除标记 + 有效期续到 now+1 自然月 + 级联恢复归因任务。"""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or dataset.deleted_at is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "回收站中不存在该数据集"},
        )
    restored_tasks = await dataset_lifecycle.restore_dataset(session, dataset)
    await session.commit()
    return JSONResponse(
        content={
            "success": True,
            "data": {
                "id": dataset.id,
                "name": dataset.name,
                "validUntil": dataset.valid_until.isoformat()
                if dataset.valid_until
                else None,
                "restoredTasks": restored_tasks,
            },
        }
    )
