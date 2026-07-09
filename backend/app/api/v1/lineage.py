"""血缘图入口路由(治理整改 P1-② 阶段2):entity-agnostic anchor 端点。

`GET /datasets/{id}/lineage`(datasets.py)以"数据集"为发起点;本端点补另外两种
发起点——kind=job(该任务的输入链)、kind=member(某个表成员,只上溯湖/源层)。
其余 kind(lake_snapshot/source...)暂无消费方,留待需要时再加(Rule 2,避免造
无人调用的入口)。均只读,鉴权姿态与 dataset_lineage 一致(该端点历来无鉴权)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.job import Job
from app.models.job_input import JobInput
from app.services.lineage_service import build_lineage, describe_job, member_anchor_id

router = APIRouter(tags=["lineage"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/lineage")
async def lineage_by_anchor(
    session: SessionDep,
    kind: str,
    job_id: Annotated[str | None, Query(alias="jobId")] = None,
    version_id: Annotated[str | None, Query(alias="versionId")] = None,
    table_name: Annotated[str | None, Query(alias="tableName")] = None,
) -> JSONResponse:
    """`kind=job`:`?jobId=` ——该任务的输入链(direction=up,不含下游消费者)。
    有产出版本时以产出版本为 anchor(自然重建 job 节点 + 其输入链);无产出版本的
    任务类型(如 quality/review,不产新版本)退化为直接以其输入版本为 anchor,
    另手工补 job 节点与输入边(`build_lineage` 的 BFS 只从"已在图里的版本"出发
    发现 job,没有版本锚点就摸不到这个 job 本身)。

    `kind=member`:`?versionId=&tableName=` ——单个表成员,只上溯湖/源层
    (不做版本↔任务 BFS,见 `build_lineage` docstring)。
    """
    if kind == "job":
        if not job_id:
            raise HTTPException(400, "kind=job 需 jobId 参数")
        job = await session.get(Job, job_id)
        if job is None:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "任务不存在"}
            )
        out_vids = (
            await session.scalars(
                select(DatasetVersion.id).where(
                    DatasetVersion.produced_by_job_id == job_id
                )
            )
        ).all()
        if out_vids:
            graph = await build_lineage(session, list(out_vids), direction="up")
        else:
            in_vids = (
                await session.scalars(
                    select(JobInput.dataset_version_id).where(
                        JobInput.job_id == job_id
                    )
                )
            ).all()
            graph = await build_lineage(session, list(in_vids), direction="up")
            graph["nodes"].append(describe_job(job))
            for vid in in_vids:
                graph["edges"].append({"from": vid, "to": job_id, "kind": "input"})
        return JSONResponse(content={"data": graph, "success": True})

    if kind == "member":
        if not version_id or not table_name:
            raise HTTPException(400, "kind=member 需 versionId + tableName 参数")
        exists = (
            await session.execute(
                select(DatasetVersionTable.id).where(
                    DatasetVersionTable.dataset_version_id == version_id,
                    DatasetVersionTable.table_name == table_name,
                )
            )
        ).first()
        if exists is None:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "成员不存在"}
            )
        graph = await build_lineage(
            session, [member_anchor_id(version_id, table_name)]
        )
        return JSONResponse(content={"data": graph, "success": True})

    raise HTTPException(400, f"不支持的 kind:{kind}")
