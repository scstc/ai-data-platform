"""API 推送入站落地辅助(数据接入重构 §4.7)。

``PushConnector`` 实现 ``Connector`` Protocol 供注册表 REGISTRY 注册:
- ``probe``   : 推送型连接器本地永远就绪,返回 (True, 0, "api 推送连接器就绪")。
- ``list_tables``: 推送无"表列表"语义,返回空列表。
- ``run_ingest``  : 推送不走采集任务拉取路径,抛 ConnectorNotReady(入站由端点调
                  ``land_push_records``,不经采集任务 rerun)。

``land_push_records(session, datasource, records, *, semantic_type, idempotency_key)``
是供 ``POST /api/v1/ingest/push/{token}`` 端点调用的核心落地函数,实现:

1. **语义归一**:优先用入参 ``semantic_type``,其次读 ``datasource.config.semanticType``。
2. **归并到同一数据集**:
   - 首次推送:``datasource.config`` 无 ``boundDatasetId`` → 创建新 Dataset,将其 id
     写回 ``config["boundDatasetId"]``(ORM 脏检测,无需手动 UPDATE)。
   - 后续推送:``datasource.config["boundDatasetId"]`` 已有值 → 加载该 Dataset,产出
     新 DatasetVersion(version_no = 当前最大值 + 1),note = "api 推送 #N"。
3. **幂等键(DB 持久版)**:相同 ``idempotency_key`` 在有效期内重复调用返回首次版本
   id 而不重复落地。落地记录写 ``push_idempotency`` 表(迁移 0069),与版本落地在
   同一事务提交;过期行(``expires_at``)惰性清理后该 key 可被后续请求复用。
4. **诚实失败**:落盘/commit 失败抛 LandingError;Session 回滚让调用方处理(不伪成功)。
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import uuid7_hex
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.push_idempotency import PushIdempotency
from app.services.connectors.base import ConnectorNotReady
from app.services.landing import LandingError
from app.services.semantic_registry import (
    apply_semantic_spec,
    coerce_semantic_type,
    infer_semantic_from_data_type,
)

if TYPE_CHECKING:
    from app.models.ingest_task import IngestTask

# ---------------------------------------------------------------------------
# 幂等键持久去重(push_idempotency 表,迁移 0069;TTL = 10 min)
# 进程内 dict 的旧实现有两个缺陷:多 worker 不共享、进程重启即失忆。改为 DB 表:
# 命中未过期即幂等返回,过期行惰性删除后 key 可复用。
# ---------------------------------------------------------------------------
_IDEMPOTENCY_TTL = 600  # 秒


async def _check_idempotency(
    session: AsyncSession, key: str | None
) -> str | None:
    """命中未过期的幂等记录 → 返回其 ``dataset_version_id``;否则 None。

    过期记录惰性删除(flush 让本次请求可用同 key 重新落地并刷新记录)。
    ``dataset_version_id`` 为空(仅登记未落版本)时返回 None,视作未命中。
    """
    if not key:
        return None
    row = await session.get(PushIdempotency, key)
    if row is None:
        return None
    if row.expires_at <= datetime.now(UTC):
        # 过期:惰性清理,让本次请求正常重新落地并在末尾刷新记录
        await session.delete(row)
        await session.flush()
        return None
    return row.dataset_version_id


async def _record_idempotency(
    session: AsyncSession,
    key: str | None,
    *,
    owner_id: str,
    version_id: str,
) -> None:
    """登记幂等键 → 版本(随落地在同一事务提交,原子)。

    ``push_idempotency.task_id`` 非空:推送入站无采集任务上下文,以推送数据源
    id 作为 owner 填入(该列为普通字符串、无外键,语义即「哪个推送源的 key」)。
    过期后由 ``_check_idempotency`` 惰性删除,同 key 可被后续请求复用。
    """
    if not key:
        return
    now = datetime.now(UTC)
    session.add(
        PushIdempotency(
            key=key,
            task_id=owner_id,
            dataset_version_id=version_id,
            created_at=now,
            expires_at=now + timedelta(seconds=_IDEMPOTENCY_TTL),
        )
    )


def _new_dataset_id() -> str:
    return f"dset-{uuid7_hex()}"


def _new_version_id() -> str:
    return f"dsv-{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# 核心落地函数
# ---------------------------------------------------------------------------


async def land_push_records(
    session: AsyncSession,
    datasource: DataSource,
    records: list[dict],
    *,
    semantic_type: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[DatasetVersion, bool]:
    """API 推送入站核心:归并到同一数据集并产新版本。

    返回 (version, deduped):deduped=True 表示幂等键命中,返回的是已落地的
    历史版本,本次**未**重新落地(端点据此在响应中明示,避免调用方误判)。

    Parameters
    ----------
    session:
        AsyncSession(由端点注入)。
    datasource:
        按 token 查到的 DataSource(type='api')。
    records:
        已解析的推送记录列表(每条为 dict)。
    semantic_type:
        端点 body 里显式指定的语义类型(优先于 datasource.config.semanticType)。
    idempotency_key:
        可选幂等键(同 key 在 TTL 内重复调用返回首版结果,不重复落地)。

    Returns
    -------
    DatasetVersion 新版本 ORM 对象(已 flush/commit,id 可读)。

    Raises
    ------
    LandingError
        落盘或 DB commit 失败(调用方转 500)。
    """
    # --- 1. 幂等键前置检查(DB 持久去重,迁移 0069) ---
    if idempotency_key:
        cached_vid = await _check_idempotency(session, idempotency_key)
        if cached_vid is not None:
            # 幂等命中:加载并返回已有版本
            existing = await session.get(DatasetVersion, cached_vid)
            if existing is not None:
                return existing, True

    # --- 2. 语义类型解析(入参 > config.semanticType > data_type 推断) ---
    cfg: dict[str, Any] = dict(datasource.config or {})
    effective_st_str: str | None = (
        semantic_type
        or cfg.get("semanticType")
        or cfg.get("semantic_type")
    )
    explicit = coerce_semantic_type(effective_st_str)
    if explicit is not None:
        records, _report = apply_semantic_spec(records, explicit, strict=False)
        effective_semantic: str | None = explicit.value
    else:
        dt = cfg.get("dataType") or cfg.get("data_type")
        inferred = infer_semantic_from_data_type(dt)
        effective_semantic = inferred.value if inferred else None

    # --- 2.5 入湖归档(可选):config.lakeId 绑定目标湖时,推送记录先以 parquet
    # 快照归档到湖(ODS 原始层,upload_channel="api"),同一推送源多次推送落到
    # 同一 DataLakeObject 的递增版本;归档失败整体失败(诚实失败,不产生
    # "仓有湖无"的静默缺口)。快照 commit 先于数据集落地——若后续落地失败,
    # 快照作为不可变归档保留,重推产生新快照版本。
    lake_snapshot_id: str | None = None
    lake_id = cfg.get("lakeId") or cfg.get("lake_id")
    if lake_id:
        from app.models.data_lake import DataLake  # noqa: PLC0415
        from app.services.data_lake import (  # noqa: PLC0415
            ingest_to_lake_parquet,
        )

        lake = await session.get(DataLake, lake_id)
        if lake is None:
            raise LandingError(
                f"api 推送数据源绑定的数据湖 {lake_id} 不存在,拒绝落地"
            )
        try:
            snapshot = await ingest_to_lake_parquet(
                session,
                lake_id=lake_id,
                data=records,
                source_type="api",
                source_metadata={
                    "original_filename": f"{datasource.name or datasource.id}.jsonl",
                    "push_datasource_id": datasource.id,
                },
                upload_channel="api",
                data_category="tabular",
                datasource_id=datasource.id,
            )
        except Exception as exc:  # noqa: BLE001 湖桶/MinIO/版本冲突统一转 LandingError
            raise LandingError(f"api 推送入湖归档失败:{exc}") from exc
        lake_snapshot_id = snapshot.id

    # --- 3. 归并到同一数据集 ---
    bound_id: str | None = cfg.get("boundDatasetId")

    if bound_id:
        # 已绑定:加载现有 dataset。数据集优先流程(Task 11):绑定 id 指向不存在的
        # 数据集 → fail loud,不再隐式重建(避免脏 config 静默创建意外数据集)。
        dataset = await session.get(Dataset, bound_id)
        if dataset is None:
            raise LandingError(
                f"api 推送数据源绑定的数据集 {bound_id} 不存在,拒绝落地"
            )

    if not bound_id:
        # 首次推送:创建新 Dataset 并写回 boundDatasetId
        dataset = Dataset(
            id=_new_dataset_id(),
            name=datasource.name or "API 推送数据集",
            description=f"api 数据源「{datasource.name}」的推送数据集",
            data_type=cfg.get("dataType") or cfg.get("data_type"),
            semantic_type=effective_semantic,
            # 三轴:来源=API 推送;推送即结构化记录,格式记 jsonl
            source_kind="api_push",
            source_format="jsonl",
            owner=datasource.creator,
            creator=datasource.creator,
        )
        session.add(dataset)
        # 写回 boundDatasetId —— SQLAlchemy JSONB 需整体替换才触发脏检测
        new_cfg = dict(cfg)
        new_cfg["boundDatasetId"] = dataset.id
        datasource.config = new_cfg  # type: ignore[assignment]
        version_no = 1
    else:
        # 后续推送:自增版本号
        result = await session.execute(
            select(func.max(DatasetVersion.version_no)).where(
                DatasetVersion.dataset_id == dataset.id  # type: ignore[union-attr]
            )
        )
        max_no: int | None = result.scalar()
        version_no = (max_no or 0) + 1
        # 更新数据集级 semantic_type(如本次推送指定了更精确的语义)
        if effective_semantic and dataset.semantic_type != effective_semantic:  # type: ignore[union-attr]
            dataset.semantic_type = effective_semantic  # type: ignore[union-attr]

    # --- 4. 写 jsonl 文件 ---
    out_dir = Path(settings.datasets_dir) / dataset.id / f"v{version_no}"  # type: ignore[union-attr]
    out_path = out_dir / "data.jsonl"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fp:
            for rec in records:
                fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        out_path.unlink(missing_ok=True)
        await session.rollback()
        raise LandingError(f"api 推送落盘失败:{exc}") from exc

    # --- 5. 建 DatasetVersion ---
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset.id,  # type: ignore[union-attr]
        version_no=version_no,
        storage_uri=str(out_path),
        format="jsonl",
        rows=len(records),
        size=out_path.stat().st_size,
        origin="managed",
        semantic_type=effective_semantic,
        # 血缘:推送版本回指来源 api 数据源(整改前恒为 None,数据集层面来源断链);
        # 绑定了湖归档时同步回指本次入湖快照
        source_datasource_id=datasource.id,
        source_snapshot_ids=[lake_snapshot_id] if lake_snapshot_id else None,
        produced_by_job_id=None,
        note=f"api 推送 #{version_no}",
    )
    session.add(version)

    # --- 6. 登记幂等键(随落地在同一事务提交,原子) ---
    await _record_idempotency(
        session, idempotency_key, owner_id=datasource.id, version_id=version.id
    )

    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise LandingError(f"api 推送 commit 失败:{exc}") from exc

    await session.refresh(dataset)
    await session.refresh(version)

    return version, False


# ---------------------------------------------------------------------------
# Connector Protocol 实现
# ---------------------------------------------------------------------------


class PushConnector:
    """API 推送入站连接器(注册在 REGISTRY[("api", None)])。

    推送型连接器的"接入"通过端点 POST /api/v1/ingest/push/{token} 发起,
    不走采集任务 rerun 路径,故 ``run_ingest`` 抛 ConnectorNotReady。
    ``probe`` 返回就绪(推送端点本地永远可用)。
    ``list_tables`` 返回空列表(推送无"表"语义)。
    """

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        """推送连接器结构就绪,返回成功。"""
        return (True, 0, "api 推送连接器就绪,通过 POST /api/v1/ingest/push/{token} 入站")  # noqa: E501

    async def list_tables(self, config: dict) -> list:
        """推送连接器无"表列表"语义,返回空列表。"""
        return []

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """推送连接器不支持采集任务拉取,入站通过端点调 land_push_records。"""
        raise ConnectorNotReady(
            "api 推送连接器不支持采集任务 rerun;数据通过 "
            "POST /api/v1/ingest/push/{token} 端点入站"
        )
