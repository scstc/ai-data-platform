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
"""

from __future__ import annotations

from collections import deque
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

# member anchor 的伪 id 前缀:build_lineage 的 anchors 是纯 str 列表,版本/成员两种
# 起点靠此前缀区分(不引入额外形参,匹配既定签名)。
_MEMBER_ANCHOR_PREFIX = "member:"


def member_anchor_id(version_id: str, table_name: str) -> str:
    """构造成员 anchor 的伪 id(`GET /lineage?kind=member` 用两个裸参数,不编码 id
    给调用方——本函数只在服务内部/测试里拼接,不对外暴露编码格式)。"""
    return f"{_MEMBER_ANCHOR_PREFIX}{version_id}:{table_name}"


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
) -> dict[str, Any]:
    """以 `anchors`(版本 id,或 `member_anchor_id()` 拼出的成员伪 id)为起点 BFS
    构建血缘图,返回 `{"nodes": [...], "edges": [...]}`。

    - `direction`:"both"(默认,版本↔任务双向 BFS,dataset_lineage 用)/
      "up"(只沿"产出该版本的任务→其输入版本"上溯,不查"谁消费了该版本"——
      job anchor 用此值排除下游消费者;export collect_lineage 同理)。
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
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    seen_edge: set[tuple[str, str]] = set()
    seen_v: set[str] = set()
    seen_j: set[str] = set()
    ds_cache: dict[str, Dataset | None] = {}

    async def get_dataset(did: str) -> Dataset | None:
        if did not in ds_cache:
            ds_cache[did] = await session.get(Dataset, did)
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
            exists = (
                await session.execute(
                    select(JobInput.job_id).where(JobInput.job_id == jid).limit(1)
                )
            ).first()
            job_has_input[jid] = exists is not None
        return job_has_input[jid]

    # anchors 按前缀区分版本 / 成员两种起点;成员 anchor 不进版本↔任务 BFS,只在
    # 下方湖/源层回溯里单独处理(只上溯湖/源层)。
    version_anchors: list[str] = []
    member_anchors: list[tuple[str, str]] = []
    for a in anchors:
        if a.startswith(_MEMBER_ANCHOR_PREFIX):
            vid, _, table_name = a[len(_MEMBER_ANCHOR_PREFIX) :].partition(":")
            member_anchors.append((vid, table_name))
        else:
            version_anchors.append(a)

    dq: deque[tuple[str, int]] = deque((a, 0) for a in version_anchors)
    while dq:
        vid, depth = dq.popleft()
        if vid in seen_v:
            continue
        seen_v.add(vid)
        version = await session.get(DatasetVersion, vid)
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
        # 上游:产出该版本的任务(及其输入版本)
        if jid and jid not in seen_j:
            seen_j.add(jid)
            job = await session.get(Job, jid)
            if job:
                nodes[jid] = describe_job(job)
                add_edge(jid, vid, "output")
                in_jis = (
                    await session.scalars(
                        select(JobInput).where(JobInput.job_id == jid)
                    )
                ).all()
                for ji in in_jis:
                    add_edge(ji.dataset_version_id, jid, "input")
                    if ji.dataset_version_id not in seen_v:
                        dq.append((ji.dataset_version_id, depth + 1))
        # 下游:消费该版本的任务(及其产出版本)。direction="up" 时不找下游消费者
        # (job anchor / export 用此模式,只要输入链)。
        if direction == "up":
            continue
        down_jis = (
            await session.scalars(
                select(JobInput).where(JobInput.dataset_version_id == vid)
            )
        ).all()
        for ji in down_jis:
            jid2 = ji.job_id
            add_edge(vid, jid2, "input")
            if jid2 in seen_j:
                continue
            seen_j.add(jid2)
            job2 = await session.get(Job, jid2)
            if job2 is None:
                continue
            nodes[jid2] = describe_job(job2)
            out_vs = (
                await session.scalars(
                    select(DatasetVersion).where(
                        DatasetVersion.produced_by_job_id == jid2
                    )
                )
            ).all()
            for ov in out_vs:
                add_edge(jid2, ov.id, "output")
                if ov.id not in seen_v:
                    dq.append((ov.id, depth + 1))

    if skip_lake_layer and not member_anchors:
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
            lake = await session.get(DataLake, lid)
            lake_cache[lid] = lake.name if lake else lid
        return lake_cache[lid]

    async def ingest_task_name(tid: str | None) -> str | None:
        if not tid:
            return None
        if tid not in task_cache:
            t = await session.get(IngestTask, tid)
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
        ds = await session.get(DataSource, ds_id)
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
        snap = await session.get(DataLakeSnapshot, sid)
        if snap is None:
            return False
        obj = (
            await session.get(DataLakeObject, snap.object_id)
            if snap.object_id
            else None
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
                ingest_job = await session.get(Job, snap.job_id)
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
            version = await session.get(DatasetVersion, vid)
            ds_id = version.source_datasource_id if version else None
            if ds_id and await add_datasource_node(ds_id):
                add_edge(ds_id, vid, "hosted_source")

    for jn in [n for n in nodes.values() if n["kind"] == "job"]:
        if jn.get("jobType") != "ingest":
            continue
        job = await session.get(Job, jn["id"])
        if job is None or not job.ingest_task_id:
            continue
        task = await session.get(IngestTask, job.ingest_task_id)
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
