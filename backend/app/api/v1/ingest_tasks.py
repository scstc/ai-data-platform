"""采集任务路由：/ingest-tasks 系列端点与进度状态机。

状态机要点（rerun 同步执行，返回即终态；不再凭 GET 轮询伪造进度/成功）：
- create        → status=pending、progress=0、logs=["[INFO] 任务已创建"]，
                  datasource_name 从数据源表冗余（数据源不存在 → 404）。
- GET detail    → 只读：如实返回当前状态/进度，附产物列表（不修改任何字段）。
- rerun         → PG+采集对象：真实拉取，success/failed；
                  PG 未配采集对象 / 非 PG 源：如实 failed（不产出数据集）。
- stop          → 转 failed、追加"[WARN] 任务被手动停止"。
- delete        → 删除记录。

单对象响应统一 {data:{...}, success:true}；未命中 404 + {success:false, message:str}。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.categories import build_category_name_map
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.schemas.common import CamelModel, PageResponse, format_version_label
from app.schemas.ingest_task import (
    IngestExtract,
    IngestRunRead,
    IngestTaskCreate,
    IngestTaskRead,
    IngestTaskUpdate,
)
from app.services import operator_catalog as oc
from app.services.connectors import resolve
from app.services.connectors.base import ConnectorNotReady, IngestError
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["ingest-tasks"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _invalid_operator_reason(extract: IngestExtract | None) -> str | None:
    """校验 extract.operators 在当前环境均可运行;返回首个不可用原因,全可用→None。

    采集落地为结构化记录(非媒体),media 类算子也拒绝(默认 media_ok=False)。
    """
    if extract is None or not extract.operators:
        return None
    llm_configured = bool(get_active_llm_config().api_key)
    for step in extract.operators:
        reason = oc.runnable_reason(step.name, llm_configured=llm_configured)
        if reason:
            return reason
    return None

PROGRESS_DONE = 100


class IngestTaskItemResponse(CamelModel):
    """单个采集任务响应：{data:{...}, success:true}。"""

    data: IngestTaskRead
    success: bool = True


def _new_task_id() -> str:
    """生成 "task-" + 6 位 hex 主键。"""
    return f"task-{secrets.token_hex(3)}"


def _new_job_id() -> str:
    """生成 "job-" + 6 位 hex 主键(每次运行一条 type=ingest 的 job)。"""
    return f"job-{secrets.token_hex(3)}"


def _now() -> datetime:
    """当前 UTC 时间（naive，与 last_run_at 列 TIMESTAMP WITHOUT TIME ZONE 对齐）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _not_found() -> JSONResponse:
    """统一 404：{success:false, message:str}。"""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"success": False, "message": "任务不存在"},
    )


def _item(
    task: IngestTask,
    output: list[dict] | None = None,
    category_name: str | None = None,
) -> dict:
    """把 ORM 任务序列化为 camelCase 单对象响应体（output 仅详情接口填充）。"""
    read = IngestTaskRead.model_validate(task)
    if output:
        read.output = output
    if category_name is not None:
        read.category_name = category_name
    return IngestTaskItemResponse(data=read).model_dump(by_alias=True, mode="json")


async def _category_name(session: AsyncSession, task: IngestTask) -> str | None:
    """取任务分类名(分类可空时返回 None)。"""
    if not task.category_id:
        return None
    names = await build_category_name_map(session, [task.category_id])
    return names.get(task.category_id)


async def _build_output(session: AsyncSession, task_id: str) -> list[dict]:
    """查该任务的全部产物数据集(经各次运行 job 的 produced_by_job_id 反查)。"""
    stmt = (
        select(DatasetVersion, Dataset)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .join(Job, Job.id == DatasetVersion.produced_by_job_id)
        .where(Job.ingest_task_id == task_id)
        .order_by(DatasetVersion.created_at)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "datasetId": dataset.id,
            "datasetName": dataset.name,
            "versionId": version.id,
            "versionNo": version.version_no,
            "versionLabel": format_version_label(
                version.version_no, version.created_at
            ),
            "rows": version.rows,
        }
        for version, dataset in rows
    ]


@router.get("/ingest-tasks", response_model=PageResponse[IngestTaskRead])
async def list_ingest_tasks(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, alias="pageSize")] = 10,
    name: Annotated[str | None, Query()] = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    category_id: Annotated[str | None, Query(alias="categoryId")] = None,
) -> PageResponse[IngestTaskRead]:
    """分页列出采集任务，支持 name 模糊、status 精确、categoryId 精确筛选。"""
    stmt = select(IngestTask)
    count_stmt = select(func.count()).select_from(IngestTask)
    if name:
        stmt = stmt.where(IngestTask.name.ilike(f"%{name}%"))
        count_stmt = count_stmt.where(IngestTask.name.ilike(f"%{name}%"))
    if status_:
        stmt = stmt.where(IngestTask.status == status_)
        count_stmt = count_stmt.where(IngestTask.status == status_)
    if category_id:
        stmt = stmt.where(IngestTask.category_id == category_id)
        count_stmt = count_stmt.where(IngestTask.category_id == category_id)

    total = await session.scalar(count_stmt) or 0
    offset = (current - 1) * page_size
    stmt = stmt.order_by(IngestTask.created_at.desc()).offset(offset).limit(page_size)
    rows = (await session.scalars(stmt)).all()
    # 批量取分类名(避免 N+1),回填 categoryName
    cat_names = await build_category_name_map(
        session, [r.category_id for r in rows]
    )
    data = []
    for r in rows:
        item = IngestTaskRead.model_validate(r)
        if r.category_id:
            item.category_name = cat_names.get(r.category_id)
        data.append(item)
    return PageResponse[IngestTaskRead](data=data, total=total)


@router.post("/ingest-tasks")
async def create_ingest_task(
    payload: IngestTaskCreate,
    session: SessionDep,
) -> Response:
    """新建采集任务：校验数据源存在并冗余其名称，初始 pending/0。"""
    datasource = await session.get(DataSource, payload.datasource_id)
    if datasource is None:
        return _not_found()

    if reason := _invalid_operator_reason(payload.extract):
        return JSONResponse(
            status_code=400, content={"success": False, "message": reason}
        )

    task = IngestTask(
        id=_new_task_id(),
        name=payload.name,
        datasource_id=payload.datasource_id,
        datasource_name=datasource.name,
        schedule=payload.schedule.model_dump(),
        extract=payload.extract.model_dump() if payload.extract else None,
        status="pending",
        progress=0,
        logs=["[INFO] 任务已创建"],
        category_id=payload.category_id,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.put("/ingest-tasks/{task_id}")
async def update_ingest_task(
    task_id: str,
    body: IngestTaskUpdate,
    session: SessionDep,
) -> Response:
    """编辑采集任务:仅更新显式传入的字段(名称/数据源/调度/采集对象)。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    if body.extract is not None and (
        reason := _invalid_operator_reason(body.extract)
    ):
        return JSONResponse(
            status_code=400, content={"success": False, "message": reason}
        )

    if body.name is not None:
        task.name = body.name
    if body.schedule is not None:
        task.schedule = body.schedule.model_dump()
    if body.extract is not None:
        task.extract = body.extract.model_dump()
    if body.datasource_id is not None and body.datasource_id != task.datasource_id:
        datasource = await session.get(DataSource, body.datasource_id)
        if datasource is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "数据源不存在"},
            )
        task.datasource_id = body.datasource_id
        task.datasource_name = datasource.name
    if body.category_id is not None:
        task.category_id = body.category_id

    await session.commit()
    await session.refresh(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.get("/ingest-tasks/{task_id}")
async def get_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """获取任务详情。状态如实反映运行结果(rerun 为同步执行,返回即终态),
    不再凭轮询伪造进度/成功——避免「任务成功却没有产出数据集」的假象。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    output = await _build_output(session, task.id)
    return JSONResponse(
        content=_item(
            task, output, category_name=await _category_name(session, task)
        )
    )


@router.post("/ingest-tasks/{task_id}/rerun")
async def rerun_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """运行/重跑任务。

    经连接器注册表派发(§4.8):resolve(ds.type, ds.db_kind) →
    - PG 族 / goldendb / S3 + 已配采集对象 → 真实拉取并落地 DatasetVersion(同步);
    - 未配采集对象 / 不支持类型 / 未装驱动 / 无集群 → 诚实 failed(不伪造成功)。
    Job(type=ingest)创建逻辑不变。
    """
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    datasource = await session.get(DataSource, task.datasource_id)
    connector = (
        resolve(datasource.type, datasource.db_kind)
        if datasource is not None
        else None
    )

    task.progress = 0
    task.last_run_at = _now()

    # --- 诚实早退(不创建 job):无数据源 / 不支持类型 / api 推送 / 未配采集对象 ---
    if datasource is None or connector is None:
        kind = "?" if datasource is None else (
            f"{datasource.type}/{datasource.db_kind}"
            if datasource.db_kind
            else datasource.type
        )
        task.status = "failed"
        task.logs = [
            *task.logs,
            f"[ERROR] 数据源类型「{kind}」暂不支持自动采集,未产出任何数据集",
        ]
    elif datasource.type == "api":
        # API 推送数据通过 POST /ingest/push/{token} 端点入站,不走采集任务 rerun
        task.status = "failed"
        task.logs = [
            *task.logs,
            "[ERROR] API 推送数据源不支持采集任务运行;"
            "请由外部系统调用推送地址(config.url)入站",
        ]
    elif not task.extract:
        # 诚实失败:已配连接器但没配采集对象 → 不可能产出数据集,不假装成功
        task.status = "failed"
        task.logs = [
            *task.logs,
            "[ERROR] 未配置采集对象,请先在任务中选择表/对象或填写 SQL 后再运行",
        ]
    else:
        # --- 正常路径:建 job → 连接器真实拉取 ---
        task.status = "running"
        started = task.last_run_at or _now()
        task.logs = [*task.logs, f"[INFO] 开始采集({datasource.name} 真实拉取)"]
        # 每次运行一条 type=ingest 的 job(收编后 ingest_runs 的替代)
        job = Job(
            id=_new_job_id(),
            name=task.name,
            type="ingest",
            ingest_task_id=task.id,
            state="running",
            progress=0,
            created_by="admin",
            started_at=started,
        )
        session.add(job)
        await session.commit()
        try:
            results = await connector.run_ingest(
                session, task, datasource, job_id=job.id
            )
            total_rows = sum(v.rows or 0 for _, v in results)
            task.status = "success"
            task.progress = PROGRESS_DONE
            task.logs = [
                *task.logs,
                f"[INFO] 采集 {len(results)} 项,共 {total_rows} 条 → 产出 "
                f"{len(results)} 个数据集:"
                + "、".join(f"{ds.name}({v.rows or 0}行)" for ds, v in results),
                "[INFO] 任务完成",
            ]
            job.state = "success"
            job.progress = PROGRESS_DONE
        except (IngestError, ConnectorNotReady) as exc:
            task.status = "failed"
            task.logs = [*task.logs, f"[ERROR] 采集失败:{exc}"]
            job.state = "failed"
            job.error = str(exc)
        job.finished_at = _now()
        task.run_count += 1

    await session.commit()
    await session.refresh(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.get(
    "/ingest-tasks/{task_id}/runs", response_model=PageResponse[IngestRunRead]
)
async def list_ingest_runs(
    task_id: str,
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, alias="pageSize")] = 20,
) -> PageResponse[IngestRunRead]:
    """某采集任务的运行记录(jobs 表 type=ingest),按开始时间倒序。

    wire 形态与收编前的 ingest_runs 保持一致;rows/outputs 由产物版本
    (produced_by_job_id)反查推导。
    """
    where = Job.ingest_task_id == task_id
    total = (
        await session.scalar(
            select(func.count()).select_from(Job).where(where)
        )
        or 0
    )
    jobs = (
        await session.scalars(
            select(Job)
            .where(where)
            .order_by(Job.started_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    # 一次查出本页 job 的全部产物,按 job 分组
    outputs_by_job: dict[str, list[dict]] = {}
    if jobs:
        stmt = (
            select(DatasetVersion, Dataset)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(
                DatasetVersion.produced_by_job_id.in_([j.id for j in jobs])
            )
            .order_by(DatasetVersion.created_at)
        )
        for version, dataset in (await session.execute(stmt)).all():
            outputs_by_job.setdefault(version.produced_by_job_id, []).append(
                {
                    "datasetId": dataset.id,
                    "datasetName": dataset.name,
                    "versionId": version.id,
                    "versionNo": version.version_no,
                    "versionLabel": format_version_label(
                        version.version_no, version.created_at
                    ),
                    "rows": version.rows,
                }
            )

    data = []
    for job in jobs:
        outputs = outputs_by_job.get(job.id, [])
        data.append(
            IngestRunRead(
                id=job.id,
                task_id=task_id,
                status=job.state,
                rows=sum(o["rows"] or 0 for o in outputs),
                dataset_count=len(outputs),
                outputs=outputs or None,
                error=job.error,
                started_at=job.started_at or job.created_at,
                finished_at=job.finished_at,
            )
        )
    return PageResponse[IngestRunRead](data=data, total=total)


@router.post("/ingest-tasks/{task_id}/stop")
async def stop_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """停止：转 failed 并追加手动停止日志。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    task.status = "failed"
    task.last_run_at = _now()
    task.logs = [*task.logs, "[WARN] 任务被手动停止"]
    await session.commit()
    await session.refresh(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.delete("/ingest-tasks/{task_id}")
async def delete_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """删除采集任务。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    await session.delete(task)
    await session.commit()
    return JSONResponse(content={"success": True})
