"""血缘图入口路由(治理整改 P1-② 阶段2 / P2 全景森林):entity-agnostic anchor
端点 + 全景森林端点。

`GET /datasets/{id}/lineage`(datasets.py)以"数据集"为发起点;`GET /lineage`
补六种 anchor 发起点——kind=job/member(P1-②)、source/lake_object/lake_snapshot/
dataset_version(P2,放开 anchor 让前端可从任意节点聚焦)。`GET /lineage/panorama`
（P2）不带 anchor,返回全局血缘森林(数据源→湖→集→任务),供血缘页默认展示、
不再强制从数据集入口发起。均只读,鉴权姿态与 dataset_lineage 一致(历来无鉴权)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.datasource import DataSource
from app.models.job import Job
from app.models.job_input import JobInput
from app.services.lineage_service import (
    build_focus_lineage,
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


# ---------------------------------------------------------------------------
# 焦点探索(OpenMetadata 式,治理整改血缘追溯重构):`GET /lineage/focus` 以任意
# 实体为中心双向展开有限跳数 +「+N」边界计数;`GET /lineage/neighbors` 单节点
# 单方向展开一跳,供前端合并进已展示的焦点图。anchor 解析复用 `lineage_by_anchor`
# 已验证的 6 类 kind 逻辑(不改动该函数本身,新写一份——见函数级 docstring)。
# ---------------------------------------------------------------------------


class _NotFound(Exception):
    """焦点/邻居端点 404 情形的内部信号:`_resolve_focus_target` 与
    `lineage_by_anchor` 一样按实体不存在返回 `{"success": False, "message": ...}`
    (而非 FastAPI 默认的 `HTTPException` `{"detail": ...}` 形状),保持响应体
    风格一致;用异常而非提前 return 是因为 `_resolve_focus_target` 要在多个
    kind 分支里复用同一套 400/404 早退逻辑。"""

    def __init__(self, message: str) -> None:
        self.message = message


@dataclass
class _FocusTarget:
    """`_resolve_focus_target` 的解析结果:分别喂给 `build_focus_lineage` 的
    up/down 两次 `build_lineage` 调用的 anchor 列表 + 焦点节点 id 集合(多数
    kind 为单元素,kind=lake_object 是该对象下全部快照 id)。`extra_nodes`/
    `extra_edges` 仅 kind=job 使用——其余 5 类 kind 的焦点节点本身经既有 anchor
    机制(member/snapshot/source 伪 anchor 均不受 `direction`/`max_depth`
    约束,version 锚点的节点在深度 0 时也总会被加入)总能在任意 up/down 深度下
    出现,唯独 job 节点要靠 BFS 从其输出/输入版本"发现"、深度 0 时发现不了,
    故手工补上,保证焦点节点在任意 up/down 组合(含 0)下都可见。
    """

    up_anchors: list[str]
    down_anchors: list[str]
    focus_ids: set[str]
    extra_nodes: list[dict[str, Any]] = field(default_factory=list)
    extra_edges: list[dict[str, str]] = field(default_factory=list)


async def _resolve_focus_target(
    session: AsyncSession,
    kind: str,
    *,
    job_id: str | None,
    version_id: str | None,
    table_name: str | None,
    source_id: str | None,
    object_id: str | None,
    snapshot_id: str | None,
    lake_id: str | None,
) -> _FocusTarget:
    """按 `kind` 把请求参数解析成 `_FocusTarget`。缺参 `HTTPException(400)`、
    实体不存在 `_NotFound`(由调用方转 404 JSON)。逻辑对齐
    `lineage_by_anchor` 各分支已验证的 anchor 构造(source 走
    `snapshot_merge_descendants` 摸 merge 链下游、lake_object/lake_snapshot
    补下游消费版本等),仅将其拆成 up/down 两份而非单一 `direction`。"""
    if kind == "job":
        if not job_id:
            raise HTTPException(400, "kind=job 需 jobId 参数")
        job = await session.get(Job, job_id)
        if job is None:
            raise _NotFound("任务不存在")
        out_vids = list(
            (
                await session.scalars(
                    select(DatasetVersion.id).where(
                        DatasetVersion.produced_by_job_id == job_id
                    )
                )
            ).all()
        )
        extra_nodes = [describe_job(job)]
        extra_edges: list[dict[str, str]] = []
        if out_vids:
            for vid in out_vids:
                extra_edges.append({"from": job_id, "to": vid, "kind": "output"})
            return _FocusTarget(
                out_vids, out_vids, {job_id}, extra_nodes, extra_edges
            )
        in_vids = list(
            (
                await session.scalars(
                    select(JobInput.dataset_version_id).where(
                        JobInput.job_id == job_id
                    )
                )
            ).all()
        )
        for vid in in_vids:
            extra_edges.append({"from": vid, "to": job_id, "kind": "input"})
        return _FocusTarget(in_vids, [], {job_id}, extra_nodes, extra_edges)

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
            raise _NotFound("成员不存在")
        mid = member_anchor_id(version_id, table_name)
        # 成员无下游(build_lineage 的 member_anchors 处理不产生出边,详见
        # lineage_service docstring),down_anchors 留空。
        return _FocusTarget([mid], [], {mid})

    if kind == "source":
        if not source_id:
            raise HTTPException(400, "kind=source 需 sourceId 参数")
        source = await session.get(DataSource, source_id)
        if source is None:
            raise _NotFound("数据源不存在")
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
        sid_anchor = source_anchor_id(source_id)
        down_anchors = (
            [sid_anchor]
            + [snapshot_anchor_id(sid) for sid in all_snap_ids]
            + list(dict.fromkeys([*extracted_vids, *hosted_vids]))
        )
        # 数据源是根,无上游;up_anchors 只放数据源伪 anchor 自身(source_anchors
        # 处理不受 direction/max_depth 约束,始终只产出该节点自身)。focus_ids 用
        # 实体真实 id(source_id)而非伪 anchor 字符串——add_datasource_node 落图
        # 的节点 id 是裸 source_id,伪 anchor 只是 build_lineage 内部 anchor 列表
        # 的前缀区分手段,从不出现在返回节点里。
        return _FocusTarget([sid_anchor], down_anchors, {source_id})

    if kind == "lake":
        if not lake_id:
            raise HTTPException(400, "kind=lake 需 lakeId 参数")
        lake = await session.get(DataLake, lake_id)
        if lake is None:
            raise _NotFound("数据湖不存在")
        snap_ids = list(
            (
                await session.scalars(
                    select(DataLakeSnapshot.id).where(
                        DataLakeSnapshot.lake_id == lake_id
                    )
                )
            ).all()
        )
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
        snap_anchors = [snapshot_anchor_id(sid) for sid in snap_ids]
        down_anchors = snap_anchors + list(dict.fromkeys(downstream_vids))
        # 无独立 "lake" 节点(build_lineage 从不产出该 kind),该湖名下全部快照
        # 节点一并标 isFocus——「聚焦一个数据湖」= 看其所有快照及下游数据集。
        return _FocusTarget(snap_anchors, down_anchors, set(snap_ids))

    if kind == "lake_object":
        if not object_id:
            raise HTTPException(400, "kind=lake_object 需 objectId 参数")
        obj = await session.get(DataLakeObject, object_id)
        if obj is None:
            raise _NotFound("湖对象不存在")
        snap_ids = list(
            (
                await session.scalars(
                    select(DataLakeSnapshot.id).where(
                        DataLakeSnapshot.object_id == object_id
                    )
                )
            ).all()
        )
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
        snap_anchors = [snapshot_anchor_id(sid) for sid in snap_ids]
        down_anchors = snap_anchors + list(dict.fromkeys(downstream_vids))
        # 无独立 "lake_object" 节点(build_lineage 从不产出该 kind),该对象下
        # 全部快照节点一并标 isFocus。
        return _FocusTarget(snap_anchors, down_anchors, set(snap_ids))

    if kind == "lake_snapshot":
        if not snapshot_id:
            raise HTTPException(400, "kind=lake_snapshot 需 snapshotId 参数")
        snap = await session.get(DataLakeSnapshot, snapshot_id)
        if snap is None:
            raise _NotFound("快照不存在")
        downstream_vids = list(
            (
                await session.scalars(
                    select(DatasetVersionTable.dataset_version_id).where(
                        DatasetVersionTable.source_snapshot_id == snapshot_id
                    )
                )
            ).all()
        )
        sid_anchor = snapshot_anchor_id(snapshot_id)
        down_anchors = [sid_anchor] + downstream_vids
        return _FocusTarget([sid_anchor], down_anchors, {snapshot_id})

    if kind == "dataset_version":
        if not version_id:
            raise HTTPException(400, "kind=dataset_version 需 versionId 参数")
        version = await session.get(DatasetVersion, version_id)
        if version is None:
            raise _NotFound("版本不存在")
        return _FocusTarget([version_id], [version_id], {version_id})

    raise HTTPException(400, f"不支持的 kind:{kind}")


@router.get("/lineage/focus")
async def lineage_focus(
    session: SessionDep,
    kind: str,
    job_id: Annotated[str | None, Query(alias="jobId")] = None,
    version_id: Annotated[str | None, Query(alias="versionId")] = None,
    table_name: Annotated[str | None, Query(alias="tableName")] = None,
    source_id: Annotated[str | None, Query(alias="sourceId")] = None,
    object_id: Annotated[str | None, Query(alias="objectId")] = None,
    snapshot_id: Annotated[str | None, Query(alias="snapshotId")] = None,
    lake_id: Annotated[str | None, Query(alias="lakeId")] = None,
    up: Annotated[int, Query(ge=0, le=3)] = 2,
    down: Annotated[int, Query(ge=0, le=3)] = 2,
    members: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """焦点探索(OpenMetadata 式):以任意实体(7 类 kind——`GET /lineage` 的 6 类
    + focus 专属的 `lake`「整个数据湖」)为中心,上/下游各展开 `up`/`down` 跳
    (默认 2,上限 3)后合并去重。焦点节点(kind=lake_object 时为该对象下、
    kind=lake 时为该湖下的全部快照节点)标 `isFocus=True`;每个入图
    节点再挂整数 `moreUp`/`moreDown`——它在完整血缘森林里还有多少条本图未覆盖
    的直接上/下游邻居(0=无更多),供前端渲染"+N"展开按钮。`members=true` 时
    额外展开成员一等节点(透传 `build_lineage` 的 `expand_members`)。
    """
    try:
        target = await _resolve_focus_target(
            session,
            kind,
            job_id=job_id,
            version_id=version_id,
            table_name=table_name,
            source_id=source_id,
            object_id=object_id,
            snapshot_id=snapshot_id,
            lake_id=lake_id,
        )
    except _NotFound as exc:
        return JSONResponse(
            status_code=404, content={"success": False, "message": exc.message}
        )

    graph = await build_focus_lineage(
        session,
        up_anchors=target.up_anchors,
        down_anchors=target.down_anchors,
        focus_ids=target.focus_ids,
        up_depth=up,
        down_depth=down,
        expand_members=members,
        extra_nodes=target.extra_nodes,
        extra_edges=target.extra_edges,
    )
    return JSONResponse(content={"data": graph, "success": True})


@router.get("/lineage/neighbors")
async def lineage_neighbors(
    session: SessionDep,
    kind: str,
    direction: str,
    job_id: Annotated[str | None, Query(alias="jobId")] = None,
    version_id: Annotated[str | None, Query(alias="versionId")] = None,
    table_name: Annotated[str | None, Query(alias="tableName")] = None,
    source_id: Annotated[str | None, Query(alias="sourceId")] = None,
    object_id: Annotated[str | None, Query(alias="objectId")] = None,
    snapshot_id: Annotated[str | None, Query(alias="snapshotId")] = None,
    lake_id: Annotated[str | None, Query(alias="lakeId")] = None,
    members: Annotated[bool, Query()] = False,
) -> JSONResponse:
    """单节点邻居增量:只返回该实体紧邻一层的上游(`direction=up`)或下游
    (`direction=down`)节点 + 边(各自带 `moreUp`/`moreDown`),供前端点某节点的
    "+N" 按钮后合并进已展示的焦点图,不必重新拉整个焦点图。anchor 解析与
    `GET /lineage/focus` 共用 `_resolve_focus_target`。
    """
    if direction not in ("up", "down"):
        raise HTTPException(400, "direction 需为 up 或 down")
    try:
        target = await _resolve_focus_target(
            session,
            kind,
            job_id=job_id,
            version_id=version_id,
            table_name=table_name,
            source_id=source_id,
            object_id=object_id,
            snapshot_id=snapshot_id,
            lake_id=lake_id,
        )
    except _NotFound as exc:
        return JSONResponse(
            status_code=404, content={"success": False, "message": exc.message}
        )

    if direction == "up":
        graph = await build_focus_lineage(
            session,
            up_anchors=target.up_anchors,
            down_anchors=[],
            focus_ids=target.focus_ids,
            up_depth=1,
            down_depth=0,
            expand_members=members,
            extra_nodes=target.extra_nodes,
            extra_edges=target.extra_edges,
        )
    else:
        graph = await build_focus_lineage(
            session,
            up_anchors=[],
            down_anchors=target.down_anchors,
            focus_ids=target.focus_ids,
            up_depth=0,
            down_depth=1,
            expand_members=members,
            extra_nodes=target.extra_nodes,
            extra_edges=target.extra_edges,
        )
    return JSONResponse(content={"data": graph, "success": True})
