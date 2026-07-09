"""数据血缘图构建服务(治理整改 P1-②:血缘服务化 + entity-agnostic anchor)。

原逻辑内联在 `api/v1/datasets.py::dataset_lineage`(#11,历经 P0-②/P1-① 两轮改造:
isOriginal 判据、出湖抽取 Job 节点、入湖 Job 进图、成员级 sourceKind 透出),本阶段
整体搬迁至此、抽成 `build_lineage()`,供三处复用而不重复实现 BFS:

- `dataset_lineage`(瘦身为 wrapper,anchors=该数据集所有版本 id、direction=both、
  expand_members=False)——**响应形状与既有行为完全一致**,回归见
  `tests/test_lineage_lake.py`。
- `GET /lineage`(entity-agnostic anchor 端点,`api/v1/lineage.py`):kind=job 取
  该任务的输入/输出版本作 anchor;kind=member 走本模块的成员伪 id 直接上溯湖/源层。
- `export_delivery.collect_lineage`(导出用血缘摘要):`direction="up"` +
  `skip_lake_layer=True`,避免导出接口引入湖层查询。

节点 kind:version / job / lake_snapshot / datasource / member(仅 expand_members=True
或成员 anchor 时出现)。边 kind:input / output / extract / merge / ingest /
hosted_source / contains(member 挂载,expand_members=True 时)。

治理整改 P2(全景森林 + 放开 entity-agnostic anchor)新增两类伪 anchor,机制与
既定的 `member:` 前缀一致(纯 str 列表里塞前缀区分,不扩形参):

- `snapshot_anchor_id(sid)`:直接把该湖快照(及其自身 merge/ingest 上游)拉入图,
  不经任何版本↔任务 BFS——`kind=lake_snapshot`/`kind=lake_object`、以及
  `build_panorama_lineage` 播种"孤立/未被任何数据集抽取的快照"都靠它。
- `source_anchor_id(sid)`:仅确保该数据源节点入图,不做自身遍历——孤立数据源
  (从未产生快照、从未被托管直连)同样要"看得见"。

`direction` 新增 `"down"` 取值:跳过"产出该版本的任务→其输入版本"这段上游
挖掘(仍保留下游消费者查找)。`kind=source`/`kind=lake_snapshot`/`kind=lake_object`
解析出的版本 anchor 用此值——这些版本的上游语境已经由 snapshot anchor 自身覆盖
(`expand_snapshot` 内建的 merge 链回溯),再用 "both" 从版本侧整一遍 BFS 只会
牵出无关的其它输入版本,污染视图。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.job_input import JobInput
from app.schemas.common import format_version_label

# member/snapshot/source anchor 的伪 id 前缀:build_lineage 的 anchors 是纯 str
# 列表,四种起点(版本/成员/快照/数据源)靠前缀区分(不引入额外形参,匹配既定签名)。
_MEMBER_ANCHOR_PREFIX = "member:"
_SNAPSHOT_ANCHOR_PREFIX = "snapshot:"
_SOURCE_ANCHOR_PREFIX = "source:"


def member_anchor_id(version_id: str, table_name: str) -> str:
    """构造成员 anchor 的伪 id(`GET /lineage?kind=member` 用两个裸参数,不编码 id
    给调用方——本函数只在服务内部/测试里拼接,不对外暴露编码格式)。"""
    return f"{_MEMBER_ANCHOR_PREFIX}{version_id}:{table_name}"


def snapshot_anchor_id(snapshot_id: str) -> str:
    """构造湖快照 anchor 的伪 id:直接把该快照拉入图(经内部 `expand_snapshot`,
    含其自身 merge/ingest 上游),不依赖任何版本↔任务 BFS 摸到它——否则孤立/未被
    任何数据集抽取的快照永远进不了图。`kind=lake_snapshot`/`lake_object` 与
    `build_panorama_lineage` 播种用。"""
    return f"{_SNAPSHOT_ANCHOR_PREFIX}{snapshot_id}"


def source_anchor_id(source_id: str) -> str:
    """构造数据源 anchor 的伪 id:仅确保该数据源节点入图,不做自身的下游遍历
    (下游靠调用方把可达的快照/版本 id 一并作为 anchor 传入)。这样即便数据源
    尚无任何快照/托管版本,节点也仍会出现——孤立数据源同样需要在血缘图里
    "看得见"。`kind=source` 与 `build_panorama_lineage` 播种用。"""
    return f"{_SOURCE_ANCHOR_PREFIX}{source_id}"


@dataclass
class _LineagePreload:
    """`build_lineage` 的可选批量预载缓存(性能整改:全景端点 N+1 消除)。

    `build_panorama_lineage` 播种 ~200+ anchor 时,`build_lineage` 原逐节点
    `session.get()`/内联 `select()` 会打成百上千次串行往返(远程 PG 下单趟
    10-20ms 累计到秒级)。本类在调用前一次性 bulk `select` 拉全表建内存索引,
    `build_lineage` 各处按需查表内存字典而非再次打库。

    只有 `build_panorama_lineage` 构造并传入;其余三个调用方(`dataset_lineage`/
    `GET /lineage` anchor 端点/`export_delivery.collect_lineage`)锚点规模小,
    继续走不传 `preload`(为 None)的原逐项查询路径,行为**完全不变**。
    """

    versions: dict[str, DatasetVersion] = field(default_factory=dict)
    jobs: dict[str, Job] = field(default_factory=dict)
    datasets: dict[str, Dataset] = field(default_factory=dict)
    lakes: dict[str, DataLake] = field(default_factory=dict)
    snapshots: dict[str, DataLakeSnapshot] = field(default_factory=dict)
    objects: dict[str, DataLakeObject] = field(default_factory=dict)
    datasources: dict[str, DataSource] = field(default_factory=dict)
    ingest_tasks: dict[str, IngestTask] = field(default_factory=dict)
    # job_id → 该 job 消费的 JobInput 列表(对应原 `select(JobInput).where(job_id==)`)
    job_inputs_by_job: dict[str, list[JobInput]] = field(default_factory=dict)
    # dataset_version_id → 消费该版本的 JobInput 列表(对应原
    # `select(JobInput).where(dataset_version_id==)`)
    job_inputs_by_version: dict[str, list[JobInput]] = field(default_factory=dict)
    # produced_by_job_id → 该 job 产出的版本列表(对应原
    # `select(DatasetVersion).where(produced_by_job_id==)`)
    versions_by_produced_job: dict[str, list[DatasetVersion]] = field(
        default_factory=dict
    )
    # 至少有一条 JobInput 的 job_id 集合(job_consumes_version 判据)
    job_ids_with_inputs: set[str] = field(default_factory=set)
    # ---- 焦点探索(邻居计数)专用索引:仅 `_load_focus_preload` 填充,`build_lineage`
    # 本身不读这些;`build_panorama_lineage` 的 preload 留空(不影响其行为)。----
    # dataset_version_id → 该版本的表成员列表
    tables_by_version: dict[str, list[DatasetVersionTable]] = field(
        default_factory=dict
    )
    # source_snapshot_id → 引用该快照的表成员列表(快照下游"被谁抽取")
    tables_by_snapshot: dict[str, list[DatasetVersionTable]] = field(
        default_factory=dict
    )
    # datasource_id → 该源产出的快照 id 列表
    snapshots_by_datasource: dict[str, list[str]] = field(default_factory=dict)
    # object_id → 该湖对象名下快照 id 列表
    snapshots_by_object: dict[str, list[str]] = field(default_factory=dict)
    # 入湖 job_id → 该 job 产出的快照 id 列表(snap.job_id)
    snapshots_by_job: dict[str, list[str]] = field(default_factory=dict)
    # snapshot_id → 以它为 merge 输入的下游快照 id 列表(merge_inputs 反向索引)
    merge_children: dict[str, list[str]] = field(default_factory=dict)
    # datasource_id → 托管直连该源的版本 id 列表(source_datasource_id)
    versions_by_hosted_source: dict[str, list[str]] = field(default_factory=dict)


async def _load_panorama_preload(session: AsyncSession) -> _LineagePreload:
    """一次性 bulk 拉取 `build_lineage` 全景遍历会用到的全表,建内存索引。

    全表(非按 anchor 过滤)加载:`build_panorama_lineage` 本身就是"全局森林"
    语义,anchor 已覆盖几乎全部版本/快照/数据源,按需过滤 IN 子句收益有限反而
    多一趟查询规划;these 8 张表在治理场景下量级可控(百到千级)。
    """
    versions = (await session.scalars(select(DatasetVersion))).all()
    jobs = (await session.scalars(select(Job))).all()
    datasets = (await session.scalars(select(Dataset))).all()
    lakes = (await session.scalars(select(DataLake))).all()
    snapshots = (await session.scalars(select(DataLakeSnapshot))).all()
    objects_ = (await session.scalars(select(DataLakeObject))).all()
    datasources = (await session.scalars(select(DataSource))).all()
    ingest_tasks = (await session.scalars(select(IngestTask))).all()
    job_inputs = (await session.scalars(select(JobInput))).all()

    preload = _LineagePreload(
        versions={v.id: v for v in versions},
        jobs={j.id: j for j in jobs},
        datasets={d.id: d for d in datasets},
        lakes={lk.id: lk for lk in lakes},
        snapshots={s.id: s for s in snapshots},
        objects={o.id: o for o in objects_},
        datasources={d.id: d for d in datasources},
        ingest_tasks={t.id: t for t in ingest_tasks},
    )
    for ji in job_inputs:
        preload.job_inputs_by_job.setdefault(ji.job_id, []).append(ji)
        preload.job_inputs_by_version.setdefault(ji.dataset_version_id, []).append(ji)
        preload.job_ids_with_inputs.add(ji.job_id)
    for v in versions:
        if v.produced_by_job_id:
            preload.versions_by_produced_job.setdefault(
                v.produced_by_job_id, []
            ).append(v)
    return preload


async def _load_focus_preload(session: AsyncSession) -> _LineagePreload:
    """焦点探索(邻居计数)专用预载:在 `_load_panorama_preload` 全表基础上,额外
    建 `_LineagePreload` 焦点专用的 6 张索引(见其字段 docstring)。焦点/单节点
    邻居端点单次返回的子图虽小,但"某节点在完整森林里还有多少个未展开的邻居"
    这个问题(`moreUp`/`moreDown`)本身需要全表视角才能回答——不能只按已入图的
    那几个 id 做 IN 查询,故复用 `_load_panorama_preload` 的全表加载再叠加索引,
    不重复一遍表扫描。"""
    preload = await _load_panorama_preload(session)
    tables = (await session.scalars(select(DatasetVersionTable))).all()
    for t in tables:
        preload.tables_by_version.setdefault(t.dataset_version_id, []).append(t)
        if t.source_snapshot_id:
            preload.tables_by_snapshot.setdefault(t.source_snapshot_id, []).append(t)
    for sid, snap in preload.snapshots.items():
        if snap.datasource_id:
            preload.snapshots_by_datasource.setdefault(
                snap.datasource_id, []
            ).append(sid)
        if snap.object_id:
            preload.snapshots_by_object.setdefault(snap.object_id, []).append(sid)
        if snap.job_id:
            preload.snapshots_by_job.setdefault(snap.job_id, []).append(sid)
        for mi in snap.merge_inputs or []:
            parent_sid = mi.get("snapshot_id") if isinstance(mi, dict) else None
            if parent_sid:
                preload.merge_children.setdefault(parent_sid, []).append(sid)
    for vid, v in preload.versions.items():
        if v.source_datasource_id:
            preload.versions_by_hosted_source.setdefault(
                v.source_datasource_id, []
            ).append(vid)
    return preload


def describe_job(job: Job) -> dict[str, Any]:
    """任务节点表示:算子链(name+params,从 job.spec 取,含 member_configs/goal/
    config 等非标准 operators 结构的兼容层)+ 成员级算子链。

    纯函数(不依赖 session/图状态),故未随其余闭包收进 `build_lineage` 内部——
    `/lineage?kind=job` 端点在"任务无产出版本"的兜底路径下也直接复用它来手工
    拼一个 job 节点,不重复这段兼容逻辑。
    """
    spec = job.spec or {}
    ops = [
        {"name": o.get("name"), "params": o.get("params") or {}}
        for o in (spec.get("operators") or [])
        if isinstance(o, dict)
    ]
    # 成员级任务(member_configs):算子链嵌在各成员配置里,spec.operators 为空
    # → 聚合各成员 operators(按算子名去重)展示,否则血缘图算子栏恒空。
    if not ops and isinstance(spec.get("member_configs"), list):
        seen_op: set[str] = set()
        for mc in spec["member_configs"]:
            if not isinstance(mc, dict):
                continue
            for o in mc.get("operators") or []:
                if isinstance(o, dict) and o.get("name") not in seen_op:
                    seen_op.add(o.get("name"))
                    ops.append({"name": o.get("name"), "params": o.get("params") or {}})
    # synthesis merge / construct 等:核心配置在 spec.goal → 伪算子("任务配置")。
    if not ops and isinstance(spec.get("goal"), dict):
        parts = {
            k: v
            for k, v in spec["goal"].items()
            if isinstance(v, (str, int, float, bool))
        }
        if parts:
            ops = [{"name": "任务配置", "params": parts}]
    # review 等非算子任务:无 operators 但有 config → 把 config 原语字段作为
    # 伪算子("审核配置")吐出,让版本卡能看到 LLM/PII/抽样等设置。
    # 旧任务(spec 为空,早于 spec 存储特性)仍为空。
    if not ops and isinstance(spec.get("config"), dict):
        parts = {
            k: v
            for k, v in spec["config"].items()
            if isinstance(v, (str, int, float, bool))
        }
        if parts:
            ops = [{"name": "审核配置", "params": parts}]
    # 成员级血缘:各成员算子链独立列出,不跨成员去重/合并(与上面 operators 的
    # 压平兼容层不同),供前端展示"哪个成员经了哪些算子"。
    member_ops: list[dict[str, Any]] = []
    if isinstance(spec.get("member_configs"), list):
        for mc in spec["member_configs"]:
            if not isinstance(mc, dict):
                continue
            mname = mc.get("member_name")
            mops = [
                {"name": o.get("name"), "params": o.get("params") or {}}
                for o in (mc.get("operators") or [])
                if isinstance(o, dict)
            ]
            if mname and mops:
                member_ops.append({"memberName": mname, "operators": mops})
    return {
        "id": job.id,
        "kind": "job",
        "name": job.name,
        "jobType": job.type,
        "state": job.state,
        "operators": ops,
        "memberOperators": member_ops,
        "createdAt": job.created_at.isoformat(),
        # LLM 快照(P0-① 可复现凭证):任务执行时固化的 model/base_url(不含 key)。
        # 资产清单据此展示"这次用了哪个模型/端点";老任务无此键为 None。
        "llmSnapshot": (
            {"model": _llm.get("model"), "baseUrl": _llm.get("base_url")}
            if (_llm := spec.get("llm_snapshot"))
            else None
        ),
    }


async def build_lineage(
    session: AsyncSession,
    anchors: list[str],
    *,
    direction: str = "both",
    max_depth: int = 6,
    max_lake_depth: int = 4,
    expand_members: bool = False,
    skip_lake_layer: bool = False,
    focus_dataset_id: str | None = None,
    preload: _LineagePreload | None = None,
) -> dict[str, Any]:
    """以 `anchors`(版本 id,或 `member_anchor_id()` 拼出的成员伪 id)为起点 BFS
    构建血缘图,返回 `{"nodes": [...], "edges": [...]}`。

    - `direction`:"both"(默认,版本↔任务双向 BFS,dataset_lineage 用)/
      "up"(只沿"产出该版本的任务→其输入版本"上溯,不查"谁消费了该版本"——
      job anchor 用此值排除下游消费者;export collect_lineage 同理)/
      "down"(反过来,跳过"产出该版本的任务→其输入版本"这段上游挖掘,只查
      "谁消费了该版本"——kind=source/lake_snapshot/lake_object 解析出的版本
      anchor 用此值,这些版本的上游语境已经由 snapshot anchor 自身覆盖,不需要
      再从版本侧牵出无关输入)。
    - `max_depth`/`max_lake_depth`:版本↔任务 BFS / 湖 merge 链上溯深度上限。
    - `expand_members`:True 时额外为每个版本节点的表成员发 `member` 一等节点
      (id=`member_anchor_id(vid, table_name)`)+ `version→member`(contains)边;
      根版本的 extract 边同时改挂到具体 member 而非笼统的 version/job。
      默认 False,`dataset_lineage` 用此默认值以保持既有响应形状不变。
    - `skip_lake_layer`:True 时跳过整段湖/源层回溯(含成员级 sourceKind 透出),
      纯版本↔任务图——`export_delivery.collect_lineage` 用此值避免导出接口
      多余的湖层查询。
    - `focus_dataset_id`:非空时,该数据集下的版本节点 `isFocus=True`(供前端高亮
      发起血缘查询的数据集自身)。
    - `preload`:性能整改新增,`_LineagePreload`(见其 docstring)。非 None 时,
      本函数内部所有单点 `session.get()`/过滤 `select()` 一律改查该内存索引,
      不再逐项打库;为 None(默认)时行为与整改前完全一致。只有
      `build_panorama_lineage` 传入,其余调用方不受影响。
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    seen_edge: set[tuple[str, str]] = set()
    seen_v: set[str] = set()
    seen_j: set[str] = set()
    ds_cache: dict[str, Dataset | None] = {}

    async def get_dataset(did: str) -> Dataset | None:
        if did not in ds_cache:
            ds_cache[did] = (
                preload.datasets.get(did)
                if preload is not None
                else await session.get(Dataset, did)
            )
        return ds_cache[did]

    def add_edge(a: str, b: str, kind: str) -> None:
        if (a, b) not in seen_edge:
            seen_edge.add((a, b))
            edges.append({"from": a, "to": b, "kind": kind})

    # Job 是否消费了平台输入版本(JobInput 有无)的缓存;isOriginal 判据(治理整改
    # P0-②)——假设:JobInput 有无 ⇔ 是否消费了平台版本。出湖抽取(extract,读湖
    # 快照不写 JobInput)与从零合成(make/trainset 无输入版本)的产出 Job 都判定
    # "未消费" → 产出版本 isOriginal=True。旧判据是 produced_by_job_id is None,
    # 新判据是其严格超集,存量血缘不回归。合成版本因此从旧 isOriginal=False 翻为
    # True 是预期变化(在平台内确实是根);因其通常无 source_snapshot_id,下方
    # root_vids 循环查不到 sids 会落 hosted_source 兜底,不会误触发湖层展开。
    job_has_input: dict[str, bool] = {}
    # vid → produced_by_job_id,供 root_vids 循环判断该 job 是否 extract 类型时
    # 改挂 extract 边,不必重新查一遍版本。
    version_job_id: dict[str, str | None] = {}

    async def job_consumes_version(jid: str) -> bool:
        if jid not in job_has_input:
            if preload is not None:
                job_has_input[jid] = jid in preload.job_ids_with_inputs
            else:
                exists = (
                    await session.execute(
                        select(JobInput.job_id).where(JobInput.job_id == jid).limit(1)
                    )
                ).first()
                job_has_input[jid] = exists is not None
        return job_has_input[jid]

    # anchors 按前缀区分四种起点;成员/快照/数据源 anchor 都不进版本↔任务 BFS,
    # 只在下方湖/源层回溯里单独处理。
    version_anchors: list[str] = []
    member_anchors: list[tuple[str, str]] = []
    snapshot_anchors: list[str] = []
    source_anchors: list[str] = []
    for a in anchors:
        if a.startswith(_MEMBER_ANCHOR_PREFIX):
            vid, _, table_name = a[len(_MEMBER_ANCHOR_PREFIX) :].partition(":")
            member_anchors.append((vid, table_name))
        elif a.startswith(_SNAPSHOT_ANCHOR_PREFIX):
            snapshot_anchors.append(a[len(_SNAPSHOT_ANCHOR_PREFIX) :])
        elif a.startswith(_SOURCE_ANCHOR_PREFIX):
            source_anchors.append(a[len(_SOURCE_ANCHOR_PREFIX) :])
        else:
            version_anchors.append(a)

    dq: deque[tuple[str, int]] = deque((a, 0) for a in version_anchors)
    while dq:
        vid, depth = dq.popleft()
        if vid in seen_v:
            continue
        seen_v.add(vid)
        version = (
            preload.versions.get(vid)
            if preload is not None
            else await session.get(DatasetVersion, vid)
        )
        if version is None:
            continue
        jid = version.produced_by_job_id
        version_job_id[vid] = jid
        is_original = True if not jid else not await job_consumes_version(jid)
        ds = await get_dataset(version.dataset_id)
        nodes[vid] = {
            "id": vid,
            "kind": "version",
            "datasetId": version.dataset_id,
            "datasetName": ds.name if ds else version.dataset_id,
            "versionNo": version.version_no,
            "versionLabel": format_version_label(
                version.version_no, version.created_at
            ),
            "origin": version.origin,
            "rows": version.rows,
            "scanVerdict": version.scan_verdict,
            "publishStatus": version.publish_status,
            "isOriginal": is_original,
            "isFocus": version.dataset_id == focus_dataset_id,
            # 数据集声明的来源快照(治理整改 P1-②,复用上面已取的 ds,零新查询):
            # 供 export collect_lineage 从图里直接拼"数据来源"行,不再自己另查
            # Dataset。表成员形态的逐成员来源见下方 nodes[vid]["members"]。
            "sourceKind": ds.source_kind if ds else None,
            "sourceFormat": ds.source_format if ds else None,
            "createdAt": version.created_at.isoformat(),
        }
        if depth >= max_depth:
            continue
        # 上游:产出该版本的任务(及其输入版本)。direction="down" 时跳过——见
        # 上方 direction 参数说明。
        if direction != "down" and jid and jid not in seen_j:
            seen_j.add(jid)
            job = (
                preload.jobs.get(jid)
                if preload is not None
                else await session.get(Job, jid)
            )
            if job:
                nodes[jid] = describe_job(job)
                add_edge(jid, vid, "output")
                in_jis = (
                    preload.job_inputs_by_job.get(jid, [])
                    if preload is not None
                    else (
                        await session.scalars(
                            select(JobInput).where(JobInput.job_id == jid)
                        )
                    ).all()
                )
                for ji in in_jis:
                    add_edge(ji.dataset_version_id, jid, "input")
                    if ji.dataset_version_id not in seen_v:
                        dq.append((ji.dataset_version_id, depth + 1))
        # 下游:消费该版本的任务(及其产出版本)。direction="up" 时不找下游消费者
        # (job anchor / export 用此模式,只要输入链)。
        if direction == "up":
            continue
        down_jis = (
            preload.job_inputs_by_version.get(vid, [])
            if preload is not None
            else (
                await session.scalars(
                    select(JobInput).where(JobInput.dataset_version_id == vid)
                )
            ).all()
        )
        for ji in down_jis:
            jid2 = ji.job_id
            add_edge(vid, jid2, "input")
            if jid2 in seen_j:
                continue
            seen_j.add(jid2)
            job2 = (
                preload.jobs.get(jid2)
                if preload is not None
                else await session.get(Job, jid2)
            )
            if job2 is None:
                continue
            nodes[jid2] = describe_job(job2)
            out_vs = (
                preload.versions_by_produced_job.get(jid2, [])
                if preload is not None
                else (
                    await session.scalars(
                        select(DatasetVersion).where(
                            DatasetVersion.produced_by_job_id == jid2
                        )
                    )
                ).all()
            )
            for ov in out_vs:
                add_edge(jid2, ov.id, "output")
                if ov.id not in seen_v:
                    dq.append((ov.id, depth + 1))

    if (
        skip_lake_layer
        and not member_anchors
        and not snapshot_anchors
        and not source_anchors
    ):
        return {"nodes": list(nodes.values()), "edges": edges}

    # ---- 湖/源层回溯(治理整改):在版本↔任务图之上补齐上游两层 ----
    # ① 根版本(produced_by_job_id 为空)经成员 source_snapshot_id 回溯湖快照
    #    (extract 边);快照再经 merge_inputs(merge 边)与 datasource_id(ingest 边)
    #    上溯。只对根版本回溯:加工产出版本的成员会结转 source_snapshot_id,
    #    若也画边则每个下游版本都连快照,失真且成网。
    # ② 采集任务节点(jobType=ingest)经 ingest_task 回指数据源(ingest 边),
    #    覆盖绕湖直落的存量采集链路。
    # ③ 兜底:根版本无湖快照但有 source_datasource_id(hosted/api 推送)→
    #    数据源直连(hosted_source 边)。
    # 快照 → 已展开时的最小 ldepth。不能只记"访问过":同一快照被多个根版本经
    # 不同长度的 merge 链摸到时,首次访问的深度会锁死其上溯预算,后来预算更
    # 充裕(ldepth 更小)的路径会被去重短路,深链上游节点按遍历顺序非确定性丢失。
    snap_depth: dict[str, int] = {}
    lake_cache: dict[str, str] = {}
    task_cache: dict[str, str | None] = {}

    async def lake_name(lid: str) -> str:
        if lid not in lake_cache:
            lake = (
                preload.lakes.get(lid)
                if preload is not None
                else await session.get(DataLake, lid)
            )
            lake_cache[lid] = lake.name if lake else lid
        return lake_cache[lid]

    async def ingest_task_name(tid: str | None) -> str | None:
        if not tid:
            return None
        if tid not in task_cache:
            t = (
                preload.ingest_tasks.get(tid)
                if preload is not None
                else await session.get(IngestTask, tid)
            )
            task_cache[tid] = t.name if t else None
        return task_cache[tid]

    def source_summary(sm: dict | None) -> str | None:
        """source_metadata 差异化溯源摘要:表名/对象键/HDFS 路径/原始文件名。"""
        if not isinstance(sm, dict):
            return None
        if sm.get("db_table"):
            return f"表 {sm['db_table']}"
        if sm.get("obj_key"):
            return f"对象 {sm['obj_key']}"
        if sm.get("hdfs_path"):
            return f"HDFS {sm['hdfs_path']}"
        if sm.get("original_filename"):
            return f"文件 {sm['original_filename']}"
        return None

    async def add_datasource_node(ds_id: str) -> bool:
        if ds_id in nodes:
            return True
        ds = (
            preload.datasources.get(ds_id)
            if preload is not None
            else await session.get(DataSource, ds_id)
        )
        if ds is None:
            return False
        nodes[ds_id] = {
            "id": ds_id,
            "kind": "datasource",
            "name": ds.name,
            "sourceType": ds.type,
            "dbKind": ds.db_kind,
        }
        return True

    async def expand_snapshot(sid: str, ldepth: int) -> bool:
        """确保湖快照节点入图(含其 merge/数据源上游);返回快照是否存在。

        以更小 ldepth(更充裕预算)重入时重新展开 merge 上游,只补漏不重复
        (节点/边分别经 nodes 覆盖与 seen_edge 去重);环经 snap_depth 单调
        递减约束收敛(重入必须 ldepth 严格更小,环上至多重入 max_lake_depth 次)。
        """
        prev = snap_depth.get(sid)
        if prev is not None and prev <= ldepth:
            return sid in nodes
        snap_depth[sid] = ldepth
        snap = (
            preload.snapshots.get(sid)
            if preload is not None
            else await session.get(DataLakeSnapshot, sid)
        )
        if snap is None:
            return False
        obj = None
        if snap.object_id:
            obj = (
                preload.objects.get(snap.object_id)
                if preload is not None
                else await session.get(DataLakeObject, snap.object_id)
            )
        nodes[sid] = {
            "id": sid,
            "kind": "lake_snapshot",
            "name": obj.display_name if obj else snap.source_version,
            "lakeId": snap.lake_id,
            "lakeName": await lake_name(snap.lake_id),
            "objectId": snap.object_id,
            "versionNo": snap.version_no,
            "sourceVersion": snap.source_version,
            "dataCategory": snap.data_category,
            "storageFormat": snap.storage_format,
            "uploadChannel": snap.upload_channel,
            "rows": snap.rows,
            "sourceSummary": source_summary(snap.source_metadata),
            "ingestTaskName": await ingest_task_name(snap.ingest_task_id),
            "createdAt": snap.created_at.isoformat(),
        }
        if ldepth < max_lake_depth:
            for mi in snap.merge_inputs or []:
                msid = mi.get("snapshot_id") if isinstance(mi, dict) else None
                if msid and await expand_snapshot(msid, ldepth + 1):
                    add_edge(msid, sid, "merge")
        # 入湖 Job 进图(治理整改 P1-①):快照带 job_id(经采集任务入湖)时插入
        # job 节点,画 datasource→job→snapshot 两段 ingest 边;手动上传/无任务
        # 上下文的快照 job_id 为空,保留原 datasource→snapshot 直连兜底。
        job_linked = False
        if snap.job_id:
            if snap.job_id not in seen_j:
                seen_j.add(snap.job_id)
                ingest_job = (
                    preload.jobs.get(snap.job_id)
                    if preload is not None
                    else await session.get(Job, snap.job_id)
                )
                if ingest_job:
                    nodes[snap.job_id] = describe_job(ingest_job)
            if snap.job_id in nodes:
                job_linked = True
                if snap.datasource_id and await add_datasource_node(
                    snap.datasource_id
                ):
                    add_edge(snap.datasource_id, snap.job_id, "ingest")
                add_edge(snap.job_id, sid, "ingest")
        if (
            not job_linked
            and snap.datasource_id
            and await add_datasource_node(snap.datasource_id)
        ):
            add_edge(snap.datasource_id, sid, "ingest")
        return True

    def member_node(
        vid: str, m: DatasetVersionTable, source_name: str | None
    ) -> dict[str, Any]:
        return {
            "id": member_anchor_id(vid, m.table_name),
            "kind": "member",
            "versionId": vid,
            "tableName": m.table_name,
            "rows": m.rows,
            "sourceSnapshotId": m.source_snapshot_id,
            "sourceName": source_name,
            "sourceUploadChannel": m.source_upload_channel,
            "sourceKind": m.source_kind,
        }

    # ---- 快照 / 数据源 anchor:直接入图,不经版本↔任务 BFS(全景森林播种 +
    # kind=lake_snapshot/lake_object/source 用,详见 snapshot_anchor_id/
    # source_anchor_id docstring)----
    for sid in snapshot_anchors:
        await expand_snapshot(sid, 0)
    for src_id in source_anchors:
        await add_datasource_node(src_id)

    # ---- 成员 anchor(kind=member):只上溯湖/源层,不做版本↔任务 BFS ----
    for vid, table_name in member_anchors:
        m = (
            await session.scalars(
                select(DatasetVersionTable).where(
                    DatasetVersionTable.dataset_version_id == vid,
                    DatasetVersionTable.table_name == table_name,
                )
            )
        ).first()
        if m is None:
            continue
        source_name: str | None = None
        if m.source_snapshot_id and await expand_snapshot(m.source_snapshot_id, 0):
            snap_node = nodes[m.source_snapshot_id]
            source_name = (
                f"{snap_node['name']} @v{snap_node['versionNo']}"
                if snap_node.get("versionNo")
                else snap_node["name"]
            )
        mid = member_anchor_id(vid, table_name)
        nodes[mid] = member_node(vid, m, source_name)
        if m.source_snapshot_id and m.source_snapshot_id in nodes:
            add_edge(m.source_snapshot_id, mid, "extract")

    # 批量取全部已入图版本节点的表成员(root_vids 的 extract 边 + 末尾的成员级
    # 展示共用同一批数据,避免各查一遍造成本已有的 N+1)。
    version_ids = [n["id"] for n in nodes.values() if n["kind"] == "version"]
    members: list[DatasetVersionTable] = []
    snaps_by_id: dict[str, DataLakeSnapshot] = {}
    objs_by_id: dict[str, DataLakeObject] = {}
    if version_ids:
        members = (
            await session.scalars(
                select(DatasetVersionTable).where(
                    DatasetVersionTable.dataset_version_id.in_(version_ids)
                )
            )
        ).all()
        snap_ids = {m.source_snapshot_id for m in members if m.source_snapshot_id}
        if snap_ids:
            snaps_by_id = {
                s.id: s
                for s in (
                    await session.scalars(
                        select(DataLakeSnapshot).where(
                            DataLakeSnapshot.id.in_(snap_ids)
                        )
                    )
                ).all()
            }
            obj_ids = {s.object_id for s in snaps_by_id.values() if s.object_id}
            if obj_ids:
                objs_by_id = {
                    o.id: o
                    for o in (
                        await session.scalars(
                            select(DataLakeObject).where(
                                DataLakeObject.id.in_(obj_ids)
                            )
                        )
                    ).all()
                }
    by_version: dict[str, list[DatasetVersionTable]] = {}
    for m in members:
        by_version.setdefault(m.dataset_version_id, []).append(m)

    def snapshot_display_name(snap: DataLakeSnapshot) -> str | None:
        obj = objs_by_id.get(snap.object_id) if snap.object_id else None
        base = obj.display_name if obj else snap.source_version
        return f"{base} @v{snap.version_no}" if snap.version_no else base

    root_vids = [
        n["id"] for n in nodes.values() if n["kind"] == "version" and n["isOriginal"]
    ]
    for vid in root_vids:
        src_members = [m for m in by_version.get(vid, []) if m.source_snapshot_id]
        # 若产出该 root 版本的 job 是出湖抽取(type=extract,P0-②新建),extract
        # 边改挂 job 节点(snapshot→extractJob→version,job→version 的 output
        # 边已由主 BFS 循环画出);否则维持存量语义 snapshot→version 字节级不变
        # (expand_members=True 时进一步细化到 snapshot→member,见下)。
        jid = version_job_id.get(vid)
        job_entry = nodes.get(jid) if jid else None
        extract_job_target = (
            jid if job_entry and job_entry.get("jobType") == "extract" else None
        )
        linked = False
        for m in src_members:
            sid = m.source_snapshot_id
            if not await expand_snapshot(sid, 0):
                continue
            if extract_job_target:
                add_edge(sid, extract_job_target, "extract")
            elif expand_members:
                add_edge(sid, member_anchor_id(vid, m.table_name), "extract")
            else:
                add_edge(sid, vid, "extract")
            linked = True
        if not linked:
            version = (
                preload.versions.get(vid)
                if preload is not None
                else await session.get(DatasetVersion, vid)
            )
            ds_id = version.source_datasource_id if version else None
            if ds_id and await add_datasource_node(ds_id):
                add_edge(ds_id, vid, "hosted_source")

    for jn in [n for n in nodes.values() if n["kind"] == "job"]:
        if jn.get("jobType") != "ingest":
            continue
        job = (
            preload.jobs.get(jn["id"])
            if preload is not None
            else await session.get(Job, jn["id"])
        )
        if job is None or not job.ingest_task_id:
            continue
        task = (
            preload.ingest_tasks.get(job.ingest_task_id)
            if preload is not None
            else await session.get(IngestTask, job.ingest_task_id)
        )
        if task and task.datasource_id and await add_datasource_node(
            task.datasource_id
        ):
            add_edge(task.datasource_id, jn["id"], "ingest")

    # ---- 版本节点挂载成员级血缘:哪个文件来自哪个湖快照/走哪条渠道 ----
    for vid, ms in by_version.items():
        if vid not in nodes:
            continue
        sorted_ms = sorted(ms, key=lambda x: x.table_name)
        nodes[vid]["members"] = [
            {
                "tableName": m.table_name,
                "rows": m.rows,
                "sourceSnapshotId": m.source_snapshot_id,
                "sourceName": (
                    snapshot_display_name(snaps_by_id[m.source_snapshot_id])
                    if m.source_snapshot_id in snaps_by_id
                    else None
                ),
                "sourceUploadChannel": m.source_upload_channel,
                "sourceKind": m.source_kind,
            }
            for m in sorted_ms
        ]
        # member 一等节点(治理整改 P1-② 阶段3):expand_members=True 时才发,
        # 默认 False 保持 dataset_lineage 既有响应形状不变。
        if expand_members:
            for m in sorted_ms:
                mid = member_anchor_id(vid, m.table_name)
                if mid in nodes:
                    continue
                source_name = (
                    snapshot_display_name(snaps_by_id[m.source_snapshot_id])
                    if m.source_snapshot_id in snaps_by_id
                    else None
                )
                nodes[mid] = member_node(vid, m, source_name)
                add_edge(vid, mid, "contains")

    return {"nodes": list(nodes.values()), "edges": edges}


def _is_original_version(preload: _LineagePreload, vid: str) -> bool:
    """`build_lineage` 内 `is_original` 判据(见其局部变量注释)的无 session 版本,
    纯读 `preload` 内存索引——`_neighbor_ids` 判断"根版本是否该经湖/源层出边"
    时复用同一判据,不重新发明。"""
    v = preload.versions.get(vid)
    if v is None:
        return True
    jid = v.produced_by_job_id
    if not jid:
        return True
    return jid not in preload.job_ids_with_inputs


def _neighbor_ids(
    preload: _LineagePreload, node_id: str, kind: str
) -> tuple[set[str], set[str]]:
    """给定节点在**完整**血缘森林里的直接上/下游邻居 id 全集(与 `build_lineage`
    实际画边的语义对齐,但不受其 `direction`/`max_depth` 约束、也不做深层递归——
    只求一跳),供焦点探索的边界 `moreUp`/`moreDown` 计数与 `/lineage/neighbors`
    单节点展开使用。全部基于 `preload`(`_load_focus_preload`)内存索引,不打库。
    """
    up: set[str] = set()
    down: set[str] = set()
    if kind == "version":
        v = preload.versions.get(node_id)
        if v is None:
            return up, down
        if v.produced_by_job_id:
            up.add(v.produced_by_job_id)
        for ji in preload.job_inputs_by_version.get(node_id, []):
            down.add(ji.job_id)
        # 非根版本(有产出任务且该任务消费了输入)不经湖/源层回溯,对齐
        # build_lineage 的 root_vids 过滤(见其 docstring)。
        if _is_original_version(preload, node_id):
            producer = (
                preload.jobs.get(v.produced_by_job_id)
                if v.produced_by_job_id
                else None
            )
            # extract 类产出任务:extract 边改挂到 job 节点(见 build_lineage
            # root_vids 循环),不再算作 version 自身的上游邻居。
            if not (producer is not None and producer.type == "extract"):
                snap_ids = {
                    m.source_snapshot_id
                    for m in preload.tables_by_version.get(node_id, [])
                    if m.source_snapshot_id
                }
                if snap_ids:
                    up |= snap_ids
                elif v.source_datasource_id:
                    up.add(v.source_datasource_id)
    elif kind == "job":
        for ji in preload.job_inputs_by_job.get(node_id, []):
            up.add(ji.dataset_version_id)
        out_vs = preload.versions_by_produced_job.get(node_id, [])
        for ov in out_vs:
            down.add(ov.id)
        job = preload.jobs.get(node_id)
        if job is not None:
            # datasource→job 的 ingest 边只在该 job 产出的快照自身带 datasource_id
            # 时才画(expand_snapshot 按 snap.datasource_id,不按 job.ingest_task_id
            # 回指)。故上游数据源须由快照派生;若改用 ingest_task_id,会把"快照无
            # datasource_id、图中根本不与源相连"的采集任务也误算成有上游源邻居。
            for sid in preload.snapshots_by_job.get(node_id, []):
                snap = preload.snapshots.get(sid)
                if snap is not None and snap.datasource_id:
                    up.add(snap.datasource_id)
            if job.type == "extract":
                for ov in out_vs:
                    if _is_original_version(preload, ov.id):
                        for m in preload.tables_by_version.get(ov.id, []):
                            if m.source_snapshot_id:
                                up.add(m.source_snapshot_id)
        down |= set(preload.snapshots_by_job.get(node_id, []))
    elif kind == "lake_snapshot":
        snap = preload.snapshots.get(node_id)
        if snap is not None:
            for mi in snap.merge_inputs or []:
                psid = mi.get("snapshot_id") if isinstance(mi, dict) else None
                if psid:
                    up.add(psid)
            if snap.job_id:
                up.add(snap.job_id)
            elif snap.datasource_id:
                up.add(snap.datasource_id)
        down |= set(preload.merge_children.get(node_id, []))
        for m in preload.tables_by_snapshot.get(node_id, []):
            down.add(m.dataset_version_id)
    elif kind == "datasource":
        # 下游邻居完全由"快照自身 datasource_id"驱动(expand_snapshot 据此画 ingest
        # 边):快照有 job_id → 边落到入湖 job 节点、邻居是该 job;否则数据源直连
        # 快照、邻居是快照。不能按 ingest_task 回指(ingest_jobs_by_datasource)算——
        # 那批采集任务的快照多无 datasource_id、图中根本不与本源相连,计入会让
        # moreDown 永久虚高、"+N"点开无物。
        for sid in preload.snapshots_by_datasource.get(node_id, []):
            snap = preload.snapshots.get(sid)
            down.add(snap.job_id if snap is not None and snap.job_id else sid)
        down |= set(preload.versions_by_hosted_source.get(node_id, []))
    elif kind == "member":
        vid, _, table_name = node_id[len(_MEMBER_ANCHOR_PREFIX) :].partition(":")
        for m in preload.tables_by_version.get(vid, []):
            if m.table_name == table_name and m.source_snapshot_id:
                up.add(m.source_snapshot_id)
    return up, down


async def build_focus_lineage(
    session: AsyncSession,
    *,
    up_anchors: list[str],
    down_anchors: list[str],
    focus_ids: set[str],
    up_depth: int,
    down_depth: int,
    expand_members: bool = False,
    extra_nodes: list[dict[str, Any]] | None = None,
    extra_edges: list[dict[str, str]] | None = None,
    preload: _LineagePreload | None = None,
) -> dict[str, Any]:
    """焦点探索(OpenMetadata 式):以 `up_anchors`/`down_anchors`(由调用方按
    6 类 kind 各自解析,详见 `api/v1/lineage.py::_resolve_focus_target`)分别跑
    `direction="up"`/`"down"` 的 `build_lineage`,结果去重合并;`focus_ids` 命中
    的节点标 `isFocus=True`;每个入图节点再挂 `moreUp`/`moreDown`——它在完整血缘
    森林里还有多少条本图未覆盖的直接上/下游邻居(`_neighbor_ids`,基于 `preload`
    内存索引算,不逐节点打库),供前端渲染"+N"展开按钮。

    `up_anchors`/`down_anchors` 任一为空列表时跳过对应方向的 `build_lineage`
    调用(`/lineage/neighbors` 单方向展开用此省一次遍历)。

    `extra_nodes`/`extra_edges`:少数 anchor 无法靠 `build_lineage` 的 BFS 摸到
    焦点节点本身时(如 kind=job 且 up/down 深度为 0——BFS 只从版本出发,深度 0
    不触发"发现产出任务"这一步)手工补的节点/边,原样并入结果图,保证焦点节点
    在任意深度下都可见。
    """
    if preload is None:
        preload = await _load_focus_preload(session)

    graph_up = (
        await build_lineage(
            session,
            up_anchors,
            direction="up",
            max_depth=up_depth,
            expand_members=expand_members,
            preload=preload,
        )
        if up_anchors
        else {"nodes": [], "edges": []}
    )
    graph_down = (
        await build_lineage(
            session,
            down_anchors,
            direction="down",
            max_depth=down_depth,
            expand_members=expand_members,
            preload=preload,
        )
        if down_anchors
        else {"nodes": [], "edges": []}
    )

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    seen_edge: set[tuple[str, str]] = set()

    def merge_edge(e: dict[str, str]) -> None:
        key = (e["from"], e["to"])
        if key not in seen_edge:
            seen_edge.add(key)
            edges.append(e)

    for g in (graph_up, graph_down):
        for n in g["nodes"]:
            nodes.setdefault(n["id"], n)
        for e in g["edges"]:
            merge_edge(e)
    for n in extra_nodes or []:
        nodes.setdefault(n["id"], n)
    for e in extra_edges or []:
        merge_edge(e)

    for fid in focus_ids:
        if fid in nodes:
            nodes[fid]["isFocus"] = True

    node_ids = set(nodes.keys())
    for nid, n in nodes.items():
        up_n, down_n = _neighbor_ids(preload, nid, n["kind"])
        n["moreUp"] = len(up_n - node_ids)
        n["moreDown"] = len(down_n - node_ids)

    return {"nodes": list(nodes.values()), "edges": edges}


async def snapshot_merge_descendants(
    session: AsyncSession, seed_ids: set[str], *, max_hops: int = 4
) -> set[str]:
    """种子快照 id 集合沿 `merge_inputs` 正向(下游)扩展 `max_hops` 跳,返回并集
    (含种子自身)。

    `kind=source` anchor 用:数据源直连的快照(`datasource_id=该源`)可能只是某条
    merge 链的上游一环,真正被数据集抽取(`DatasetVersionTable.source_snapshot_id`
    引用)的是下游合并出的快照——不正向走一遍 merge 链就摸不到它,"该源流向哪个
    数据集"这条链路会断在湖层。

    实现:一次性取全表 `(id, merge_inputs)`,内存建反向索引(谁的 merge_inputs
    指向该 snapshot_id)后做层级 BFS——换掉否则要么写递归 SQL、要么逐层 N+1
    查询。快照量级可控(治理场景下万级以内)时这笔全表扫描代价可接受;量级失控
    需改造为递归 CTE(暂未遇到,不预先做)。
    """
    rows = (
        await session.execute(
            select(DataLakeSnapshot.id, DataLakeSnapshot.merge_inputs)
        )
    ).all()
    children: dict[str, list[str]] = {}
    for sid, merge_inputs in rows:
        for mi in merge_inputs or []:
            parent_sid = mi.get("snapshot_id") if isinstance(mi, dict) else None
            if parent_sid:
                children.setdefault(parent_sid, []).append(sid)

    result = set(seed_ids)
    frontier = set(seed_ids)
    for _ in range(max_hops):
        nxt = {c for sid in frontier for c in children.get(sid, [])} - result
        if not nxt:
            break
        result |= nxt
        frontier = nxt
    return result


async def build_panorama_lineage(
    session: AsyncSession,
    *,
    lake_id: str | None = None,
    kinds: list[str] | None = None,
    since: datetime | None = None,
    max_nodes: int = 600,
) -> dict[str, Any]:
    """全景森林血缘(治理整改 P2):默认展示全局血缘森林(数据源→湖→集→任务),
    数据血缘页不再强制从某个数据集入口发起查询,而是先看全局森林再点节点聚焦。

    播种(seed)策略——全部 `DatasetVersion.id`(常规版本↔任务 BFS 起点)+ 全部
    `DataLakeSnapshot.id`(经 `snapshot_anchor_id` 直接入图)+ 全部 `DataSource.id`
    (经 `source_anchor_id` 直接入图)。三类缺一都会漏掉"零度节点"——BFS 只能从
    已连通的锚点摸到邻居,摸不到没有任何边指向/指出的孤立节点:
    - 只播种版本:未被任何数据集抽取的湖快照、从未产生快照/托管版本的数据源都
      不可达,森林里会凭空少一截。
    - 只播种"每个湖对象的 latest 快照":会漏掉"某历史快照是当前某条 merge 链
      的中间节点,但其 latest 后继未必属于同一条链"的场景(`expand_snapshot` 的
      merge 上游语义允许任意版本互相引用,不能假设只有 latest 有意义);且治理
      审计要看完整轨迹而非只看最新状态。故本函数全量播种全部快照版本,不裁剪
      到 latest(如未来性能压力大到必须裁剪,需另行评估,不在本次范围内静默做)。

    过滤(在收尾阶段做,不在播种阶段做——播种要拿到完整森林,过滤只裁剪展示;
    过滤后统一裁剪悬空边,任何一端被过滤掉的边一并丢弃,不留半条边):
    - `lake_id`:先在**完整**森林上,从该湖的快照节点出发沿 `edges`(from→to)
      做一次前向可达性 BFS,只留快照节点自身 + 可达的下游节点(集/任务等)——
      必须在完整图上算可达性,否则会被后续 kinds/since 过滤提前切断路径,漏掉
      本该可达的下游。
    - `kinds`:节点类型白名单,直接按 `node["kind"]` 过滤(在 lake_id 可达性算完
      之后应用,不影响可达性判定)。
    - `since`:节点 `createdAt` 早于该时间的丢弃;`datasource` 节点没有
      `createdAt`(它是长期存在的实体,不是一次性事件),不受 since 过滤。

    超量保护(fail-loud,不悄悄丢数据):过滤后的最终节点数超过 `max_nodes` 时
    按稳定顺序(播种查询按 id 排序,结果可复现)截断到 `max_nodes`,返回
    `truncated=True` + `totalEstimated`(截断前的真实节点数),由前端提示用户
    缩小过滤范围——不是让血缘图无声不全。

    返回 `{"nodes": [...], "edges": [...], "truncated": bool, "totalEstimated": int}`
    (比 `build_lineage` 多出 `truncated`/`totalEstimated` 两键)。
    """
    version_ids = list(
        (
            await session.scalars(
                select(DatasetVersion.id).order_by(DatasetVersion.id)
            )
        ).all()
    )
    snapshot_ids = list(
        (
            await session.scalars(
                select(DataLakeSnapshot.id).order_by(DataLakeSnapshot.id)
            )
        ).all()
    )
    source_ids = list(
        (await session.scalars(select(DataSource.id).order_by(DataSource.id))).all()
    )

    anchors = (
        version_ids
        + [snapshot_anchor_id(sid) for sid in snapshot_ids]
        + [source_anchor_id(did) for did in source_ids]
    )
    # 性能整改:全景播种 ~200+ anchor 会让 build_lineage 内部逐节点
    # session.get()/内联 select() 打成百上千次串行往返(远程 PG 下秒级)——
    # 先批量预载全表建内存索引,build_lineage 收到 preload 后改查内存,
    # 消除 N+1(见 _LineagePreload docstring)。
    preload = await _load_panorama_preload(session)
    graph = await build_lineage(session, anchors, direction="both", preload=preload)
    nodes_by_id: dict[str, dict[str, Any]] = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]

    if lake_id is not None:
        lake_snapshot_ids = {
            nid
            for nid, n in nodes_by_id.items()
            if n["kind"] == "lake_snapshot" and n.get("lakeId") == lake_id
        }
        adj: dict[str, list[str]] = {}
        for e in edges:
            adj.setdefault(e["from"], []).append(e["to"])
        keep = set(lake_snapshot_ids)
        frontier: deque[str] = deque(lake_snapshot_ids)
        while frontier:
            cur = frontier.popleft()
            for nxt in adj.get(cur, []):
                if nxt not in keep:
                    keep.add(nxt)
                    frontier.append(nxt)
        nodes_by_id = {nid: n for nid, n in nodes_by_id.items() if nid in keep}

    if kinds is not None:
        kind_set = set(kinds)
        nodes_by_id = {
            nid: n for nid, n in nodes_by_id.items() if n["kind"] in kind_set
        }

    if since is not None:

        def _keep_by_time(n: dict[str, Any]) -> bool:
            created = n.get("createdAt")
            if created is None:
                return True
            return datetime.fromisoformat(created) >= since

        nodes_by_id = {nid: n for nid, n in nodes_by_id.items() if _keep_by_time(n)}

    edges = [e for e in edges if e["from"] in nodes_by_id and e["to"] in nodes_by_id]

    nodes_list = list(nodes_by_id.values())
    total = len(nodes_list)
    truncated = total > max_nodes
    if truncated:
        nodes_list = nodes_list[:max_nodes]
        kept_ids = {n["id"] for n in nodes_list}
        edges = [e for e in edges if e["from"] in kept_ids and e["to"] in kept_ids]

    return {
        "nodes": nodes_list,
        "edges": edges,
        "truncated": truncated,
        "totalEstimated": total,
    }
