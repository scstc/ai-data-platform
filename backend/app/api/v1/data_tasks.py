"""数据任务统一控制台:跨类型(数据治理 + 数据评估)聚合的任务列表。

定位为运维控制台(见 docs/superpowers/specs/2026-06-23-data-task-manager-design.md):
只提供「跨类型统一列表 + 检索」,任务的暂停/继续/停止/重跑/删除等写操作走通用
``/jobs/{id}/*`` 端点(已支持 pending/running/paused/cancelled 全状态)。新建任务
仍回各类型 editor,本端点不负责创建。

受管类型 = 异步可管控的 7 类:process/clean/distillation/synthesis/augmentation/
quality/review(ingest/annotate 不入控制台)。列表项复用 jobs._build_input/_build_output
挂上输入/产出版本概要,供前端在详情抽屉里做多版本按文件预览。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import require_perm
from app.api.v1.jobs import SessionDep, _build_input, _build_output
from app.models.job import Job
from app.schemas.common import PageResponse
from app.schemas.job import JobRead

router = APIRouter(tags=["data-tasks"])

# 异步可管控的任务类型(治理 + 评估);ingest/annotate 等不入统一控制台
_TASK_TYPES: tuple[str, ...] = (
    "process",
    "clean",
    "distillation",
    "synthesis",
    "augmentation",
    "quality",
    "review",
)


@router.get(
    "/data-tasks",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("ops:datatask:list"))],
)
async def list_data_tasks(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    types: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
) -> PageResponse[JobRead]:
    """跨类型统一列出数据任务(治理+评估),按创建时间倒序,带输入/产出版本概要。

    - types:逗号分隔的类型白名单(缺省=全部受管类型);非法类型静默忽略。
    - state:单值状态过滤(pending/running/paused/success/failed/cancelled)。
    - keyword:任务名模糊匹配(name ilike)。
    """
    requested = {t.strip() for t in types.split(",") if t.strip()} if types else None
    sel_types = requested & set(_TASK_TYPES) if requested else set(_TASK_TYPES)
    conds = [Job.type.in_(sel_types)] if sel_types else []
    if not sel_types:
        # 全是非法类型 → 返回空集(用永假条件),而非全表
        conds = [Job.id == ""]
    if state:
        conds.append(Job.state == state)
    if keyword:
        conds.append(Job.name.ilike(f"%{keyword}%"))

    count_stmt = select(func.count()).select_from(Job).where(*conds)
    list_stmt = select(Job).where(*conds)
    total = await session.scalar(count_stmt) or 0
    rows = (
        await session.scalars(
            list_stmt.order_by(Job.created_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data: list[JobRead] = []
    for r in rows:
        read = JobRead.model_validate(r)
        read.can_rerun = bool(r.spec)
        read.input = await _build_input(session, r.id)
        read.output = await _build_output(session, r.id)  # 评估类无产物版本 → None
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)
