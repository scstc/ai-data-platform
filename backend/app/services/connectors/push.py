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
3. **幂等键(内存版)**:相同 ``idempotency_key`` 在 TTL 内重复调用返回首次版本 id 而
   不重复落地。本期内存版 + 标注,生产化需 DB 持久去重(§11 后续增强)。
4. **诚实失败**:落盘/commit 失败抛 LandingError;Session 回滚让调用方处理(不伪成功)。
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import uuid7_hex
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
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
# 内存幂等键缓存(TTL = 10 min)
# 结构: {idempotency_key: (version_id, expire_ts)}
# ---------------------------------------------------------------------------
_IDEMPOTENCY_TTL = 600  # 秒
_idempotency_cache: dict[str, tuple[str, float]] = {}


def _check_idempotency(key: str | None) -> str | None:
    """返回缓存的 version_id(幂等命中),或 None(需正常落地)。"""
    if not key:
        return None
    cached = _idempotency_cache.get(key)
    if cached is None:
        return None
    version_id, expire_ts = cached
    if time.monotonic() > expire_ts:
        del _idempotency_cache[key]
        return None
    return version_id


def _set_idempotency(key: str | None, version_id: str) -> None:
    """缓存幂等键 → version_id,过期后失效。"""
    if not key:
        return
    _idempotency_cache[key] = (version_id, time.monotonic() + _IDEMPOTENCY_TTL)


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
) -> DatasetVersion:
    """API 推送入站核心:归并到同一数据集并产新版本。

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
    # --- 1. 幂等键前置检查 ---
    if idempotency_key:
        cached_vid = _check_idempotency(idempotency_key)
        if cached_vid is not None:
            # 幂等命中:加载并返回已有版本
            existing = await session.get(DatasetVersion, cached_vid)
            if existing is not None:
                return existing

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
        # 血缘:推送版本回指来源 api 数据源(整改前恒为 None,数据集层面来源断链)
        source_datasource_id=datasource.id,
        produced_by_job_id=None,
        note=f"api 推送 #{version_no}",
    )
    session.add(version)

    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise LandingError(f"api 推送 commit 失败:{exc}") from exc

    await session.refresh(dataset)
    await session.refresh(version)

    # --- 6. 缓存幂等键 ---
    _set_idempotency(idempotency_key, version.id)

    return version


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
