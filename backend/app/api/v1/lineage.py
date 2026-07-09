"""血缘图入口路由(治理整改 P1-② 阶段2 / P2 全景森林):entity-agnostic anchor
端点 + 全景森林端点。

`GET /datasets/{id}/lineage`(datasets.py)以"数据集"为发起点;`GET /lineage`
补六种 anchor 发起点——kind=job/member(P1-②)、source/lake_object/lake_snapshot/
dataset_version(P2,放开 anchor 让前端可从任意节点聚焦)。`GET /lineage/panorama`
（P2）不带 anchor,返回全局血缘森林(数据源→湖→集→任务),供血缘页默认展示、
不再强制从数据集入口发起。均只读,鉴权姿态与 dataset_lineage 一致(历来无鉴权)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.data_lake import DataLakeObject, DataLakeSnapshot
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.datasource import DataSource
from app.models.job import Job
from app.models.job_input import JobInput
from app.services.lineage_service import (
    build_lineage,
    build_panorama_lineage,
    describe_job,
    member_anchor_id,
    snapshot_anchor_id,
    snapshot_merge_descendants,
    source_anchor_id,
)

router = APIRouter(tags=["lineage"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/lineage")
async def lineage_by_anchor(
    session: SessionDep,
    kind: str,
    job_id: Annotated[str | None, Query(alias="jobId")] = None,
    version_id: Annotated[str | None, Query(alias="versionId")] = None,
    table_name: Annotated[str | None, Query(alias="tableName")] = None,
    source_id: Annotated[str | None, Query(alias="sourceId")] = None,
    object_id: Annotated[str | None, Query(alias="objectId")] = None,
    snapshot_id: Annotated[str | None, Query(alias="snapshotId")] = None,
) -> JSONResponse:
    """`kind=job`:`?jobId=` ——该任务的输入链(direction=up,不含下游消费者)。
    有产出版本时以产出版本为 anchor(自然重建 job 节点 + 其输入链);无产出版本的
    任务类型(如 quality/review,不产新版本)退化为直接以其输入版本为 anchor,
    另手工补 job 节点与输入边(`build_lineage` 的 BFS 只从"已在图里的版本"出发
    发现 job,没有版本锚点就摸不到这个 job 本身)。

    `kind=member`:`?versionId=&tableName=` ——单个表成员,只上溯湖/源层
    (不做版本↔任务 BFS,见 `build_lineage` docstring)。

    以下四种为治理整改 P2 新增(放开 anchor,支持前端点任意节点聚焦):

    `kind=source`:`?sourceId=` ——以数据源为根,direction=down 看它流向哪些
    湖/集/任务。数据源可能不直接被任何快照/版本引用(隔着一条 merge 链),故先
    用 `snapshot_merge_descendants` 正向摸出 merge 链下游的全部快照,再查这些
    快照被哪些数据集成员抽取(`DatasetVersionTable.source_snapshot_id`),连同
    "托管直连"(`DatasetVersion.source_datasource_id`)版本一并作为 anchor。

    `kind=lake_object`:`?objectId=` ——该湖对象名下全部快照版本为根(不只
    latest,理由同 `build_panorama_lineage` 播种策略)。

    `kind=lake_snapshot`:`?snapshotId=` ——单个快照为根;快照自身上游(merge/
    ingest)靠 `expand_snapshot` 内建行为覆盖,下游(被哪些数据集抽取)另查
    `DatasetVersionTable` 补足。

    `kind=dataset_version`:`?versionId=` ——单个版本为根,direction=both(与
    `dataset_lineage` 语义一致,只是锚点收窄到单版本而非该数据集全部版本)。
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

    if kind == "source":
        if not source_id:
            raise HTTPException(400, "kind=source 需 sourceId 参数")
        source = await session.get(DataSource, source_id)
        if source is None:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "数据源不存在"}
            )
        direct_snap_ids = set(
            (
                await session.scalars(
                    select(DataLakeSnapshot.id).where(
                        DataLakeSnapshot.datasource_id == source_id
                    )
                )
            ).all()
        )
        all_snap_ids = await snapshot_merge_descendants(session, direct_snap_ids)
        extracted_vids = (
            (
                await session.scalars(
                    select(DatasetVersionTable.dataset_version_id).where(
                        DatasetVersionTable.source_snapshot_id.in_(all_snap_ids)
                    )
                )
            ).all()
            if all_snap_ids
            else []
        )
        hosted_vids = (
            await session.scalars(
                select(DatasetVersion.id).where(
                    DatasetVersion.source_datasource_id == source_id
                )
            )
        ).all()
        anchors = (
            [source_anchor_id(source_id)]
            + [snapshot_anchor_id(sid) for sid in all_snap_ids]
            + list(dict.fromkeys([*extracted_vids, *hosted_vids]))
        )
        graph = await build_lineage(session, anchors, direction="down")
        return JSONResponse(content={"data": graph, "success": True})

    if kind == "lake_object":
        if not object_id:
            raise HTTPException(400, "kind=lake_object 需 objectId 参数")
        obj = await session.get(DataLakeObject, object_id)
        if obj is None:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "湖对象不存在"}
            )
        snap_ids = (
            await session.scalars(
                select(DataLakeSnapshot.id).where(
                    DataLakeSnapshot.object_id == object_id
                )
            )
        ).all()
        downstream_vids = (
            (
                await session.scalars(
                    select(DatasetVersionTable.dataset_version_id).where(
                        DatasetVersionTable.source_snapshot_id.in_(snap_ids)
                    )
                )
            ).all()
            if snap_ids
            else []
        )
        anchors = [snapshot_anchor_id(sid) for sid in snap_ids] + list(
            dict.fromkeys(downstream_vids)
        )
        graph = await build_lineage(session, anchors, direction="down")
        return JSONResponse(content={"data": graph, "success": True})

    if kind == "lake_snapshot":
        if not snapshot_id:
            raise HTTPException(400, "kind=lake_snapshot 需 snapshotId 参数")
        snap = await session.get(DataLakeSnapshot, snapshot_id)
        if snap is None:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "快照不存在"}
            )
        downstream_vids = (
            await session.scalars(
                select(DatasetVersionTable.dataset_version_id).where(
                    DatasetVersionTable.source_snapshot_id == snapshot_id
                )
            )
        ).all()
        anchors = [snapshot_anchor_id(snapshot_id)] + list(
            dict.fromkeys(downstream_vids)
        )
        graph = await build_lineage(session, anchors, direction="down")
        return JSONResponse(content={"data": graph, "success": True})

    if kind == "dataset_version":
        if not version_id:
            raise HTTPException(400, "kind=dataset_version 需 versionId 参数")
        version = await session.get(DatasetVersion, version_id)
        if version is None:
            return JSONResponse(
                status_code=404, content={"success": False, "message": "版本不存在"}
            )
        graph = await build_lineage(session, [version_id], direction="both")
        return JSONResponse(content={"data": graph, "success": True})

    raise HTTPException(400, f"不支持的 kind:{kind}")


@router.get("/lineage/panorama")
async def lineage_panorama(
    session: SessionDep,
    lake_id: Annotated[str | None, Query(alias="lakeId")] = None,
    kinds: Annotated[str | None, Query()] = None,
    since: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    """全景森林血缘(治理整改 P2):默认返回全局血缘森林,数据血缘页不再强制从
    数据集入口发起查询。`kinds` 逗号分隔节点类型白名单(呼应本仓库既有
    `category_ids`/`tags` 查询参数惯例,见 `datasets.py`),如
    `?kinds=datasource,lake_snapshot`;`since` 为 ISO 8601 时间字符串下限。

    响应 `data` 比 `GET /lineage` 多两个键:`truncated`(节点数超过内部上限时
    截断)、`totalEstimated`(截断前的真实节点数)——详见
    `build_panorama_lineage` docstring。
    """
    kind_list = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            raise HTTPException(400, f"since 格式非法(需 ISO 8601):{since}") from exc

    graph = await build_panorama_lineage(
        session, lake_id=lake_id, kinds=kind_list, since=since_dt
    )
    return JSONResponse(content={"data": graph, "success": True})
