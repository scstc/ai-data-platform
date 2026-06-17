"""S3 平铺文件采集连接器(§4.5 / §4.9,数据接入重构)。

支持 AWS S3 / MinIO / OSS / OBS 等 S3 兼容对象存储(通过 config.endpoint 区分);
同一连接器,不同 endpoint 即不同厂商。

采集模式(extract.mode = 'path'):
- paths: list[str] —— 显式对象键列表(可带 s3:// URI 前缀,也可裸 key)。
- glob : str       —— Shell 风格通配符(fnmatch,匹配桶内全量对象的 key);
                      内部先列对象(list_objects),再用 fnmatch 过滤。
两种模式可同时存在:先合并 paths 指定的键,再追加 glob 匹配的键,去重。
每个对象独立落地为一个 Dataset + DatasetVersion(§4.9「每对象一个数据集」)。

错误处理原则(Rule 12):
- S3 配置/网络错误 (ExternalStoreError) → IngestError,不 500。
- 不支持格式 (UnsupportedFormatError) → 记入警告日志并跳过,继续处理其余对象。
- probe/list_tables 阶段的 ExternalStoreError → 直接上报,不崩。
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.services.connectors.base import ConnectorNotReady, IngestError
from app.services.external_store import (
    ExternalStoreError,
    download_to_temp,
    list_objects,
    test_connection,
)
from app.services.landing import (
    UnsupportedFormatError,
    land_records,
    normalize_to_records,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion
    from app.models.datasource import DataSource
    from app.models.ingest_task import IngestTask

logger = logging.getLogger(__name__)


def _bucket_from_config(config: dict[str, Any]) -> str:
    """从数据源配置取桶名;缺失则抛 IngestError。"""
    bucket = str(config.get("bucket") or "").strip()
    if not bucket:
        raise IngestError("S3 数据源配置缺少 bucket 字段")
    return bucket


def _keys_from_extract(
    extract: dict[str, Any] | None,
    all_objects: list[dict[str, Any]],
) -> list[str]:
    """由 extract spec 与桶内全量对象列表计算本次采集的 key 列表(已去重、保序)。

    - extract.paths : 显式键(可含 s3://bucket/key 或裸 key / s3:///key 等)。
    - extract.glob  : fnmatch 通配(与 all_objects 中每个 obj["key"] 匹配)。
    两者均可为空;都为空时抛 IngestError(未配置采集对象)。
    """
    extract = extract or {}
    mode = extract.get("mode")

    # path 模式才进此函数;table/sql 模式由 _build_queries 处理,不走 S3。
    # 上层(run_ingest)已判断 mode == 'path';这里做防御性二次检查。
    if mode not in (None, "path"):
        raise IngestError(
            f"S3 连接器不支持 extract.mode='{mode}',请使用 mode='path'"
        )

    seen: dict[str, None] = {}  # 保序去重(Python 3.7+ dict 有序)

    # --- paths 列表 ---
    raw_paths: list[str] = extract.get("paths") or []
    for raw in raw_paths:
        raw = raw.strip()
        if not raw:
            continue
        # 支持 s3://bucket/key 或 s3:///key(key 含前导 /) 或裸 key
        if raw.startswith("s3://"):
            # 去掉 scheme + netloc(bucket),取 path 部分
            without_scheme = raw[len("s3://"):]
            slash = without_scheme.find("/")
            if slash != -1:
                key = without_scheme[slash + 1:].lstrip("/")
            else:
                key = without_scheme
        else:
            key = raw.lstrip("/")
        if key:
            seen[key] = None

    # --- glob 通配 ---
    glob_pattern: str = str(extract.get("glob") or "").strip()
    if glob_pattern:
        for obj in all_objects:
            obj_key: str = obj.get("key") or ""
            if obj_key and fnmatch.fnmatch(obj_key, glob_pattern):
                seen[obj_key] = None

    if not seen:
        raise IngestError(
            "S3 采集对象为空:"
            "请在 extract.paths 填写对象键列表,或在 extract.glob 填写通配符"
        )

    return list(seen)


class S3Connector:
    """S3(及 OSS/OBS 等 S3 兼容)平铺文件采集连接器。

    实现 base.Connector Protocol(§4.2)。
    """

    # ------------------------------------------------------------------
    # probe —— 测连接
    # ------------------------------------------------------------------

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        """委托 external_store.test_connection:真连 S3,探活并计时。"""
        try:
            return await test_connection(config)
        except ExternalStoreError as exc:
            return False, 0, f"S3 连接失败:{exc}"
        except Exception as exc:  # noqa: BLE001
            return False, 0, f"S3 连接失败(未知错误):{exc}"

    # ------------------------------------------------------------------
    # list_tables —— 列对象
    # ------------------------------------------------------------------

    async def list_tables(self, config: dict) -> list:
        """列桶内对象(受 _LIST_LIMIT=1000 上限约束)。

        返回 list[dict[str, Any]] — 每项 {key, size, lastModified}。
        config 须含 bucket;prefix 可选(默认列全桶)。
        S3 错误 → ConnectorNotReady(友好文案,不崩、不 500)。
        """
        try:
            bucket = _bucket_from_config(config)
        except IngestError as exc:
            raise ConnectorNotReady(str(exc)) from exc

        prefix: str = str(config.get("prefix") or "").strip()
        try:
            return await list_objects(config, bucket, prefix)
        except ExternalStoreError as exc:
            raise ConnectorNotReady(f"S3 列对象失败:{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ConnectorNotReady(f"S3 列对象失败(未知错误):{exc}") from exc

    # ------------------------------------------------------------------
    # run_ingest —— 真实采集
    # ------------------------------------------------------------------

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """S3 平铺文件采集:下载 → normalize → land_records,每对象一个数据集。

        extract.mode 须为 'path'(或 None);配置 paths/glob 指定对象范围。
        data_type 由数据源 config.dataType(前端传入)或留 None;
        semantic_type 由 land_records 内的 infer_semantic_from_data_type 自动推断。
        """
        config: dict[str, Any] = datasource.config or {}
        extract: dict[str, Any] = task.extract or {}

        # 校验 mode
        mode = extract.get("mode")
        if mode not in (None, "path"):
            raise IngestError(
                f"S3 连接器不支持 extract.mode='{mode}',请使用 mode='path'"
            )

        # 取桶名
        try:
            bucket = _bucket_from_config(config)
        except IngestError:
            raise

        # 取数据源层面的 data_type(接入功能键,不修改,仅透传)
        data_type: str | None = config.get("dataType") or None

        # 先列桶内全量对象(供 glob 过滤使用;paths 模式也需验证对象存在感知)
        prefix: str = str(config.get("prefix") or "").strip()
        try:
            all_objects = await list_objects(config, bucket, prefix)
        except ExternalStoreError as exc:
            raise IngestError(f"S3 采集预备阶段列对象失败:{exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise IngestError(f"S3 采集预备阶段列对象失败(未知错误):{exc}") from exc

        # 由 extract spec 计算本次要采集的 key 列表
        keys = _keys_from_extract(extract, all_objects)

        results: list[tuple[Dataset, DatasetVersion]] = []
        skipped = 0

        for key in keys:
            tmp_path: Path | None = None
            try:
                # 1. 下载到临时文件
                try:
                    tmp_path = await download_to_temp(config, bucket, key)
                except ExternalStoreError as exc:
                    raise IngestError(f"S3 对象下载失败 {bucket}/{key}:{exc}") from exc
                except Exception as exc:  # noqa: BLE001
                    raise IngestError(
                        f"S3 对象下载失败(未知错误) {bucket}/{key}:{exc}"
                    ) from exc

                # 2. 按扩展名解析为记录列表
                ext = tmp_path.suffix.lstrip(".").lower() or "txt"
                content = tmp_path.read_bytes()

                try:
                    records = normalize_to_records(content, ext)
                except UnsupportedFormatError:
                    logger.warning(
                        "S3 采集跳过不支持格式 %s/%s (ext=%s),继续处理其余对象",
                        bucket,
                        key,
                        ext,
                    )
                    skipped += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "S3 采集解析失败 %s/%s (ext=%s):%s,跳过",
                        bucket,
                        key,
                        ext,
                        exc,
                    )
                    skipped += 1
                    continue

                # 3. 落地(每对象 → 一个 Dataset + DatasetVersion)
                # dataset_name = 对象文件名(去扩展名)
                dataset_name = Path(key).stem or key
                pair = await land_records(
                    session,
                    records,
                    dataset_name=dataset_name,
                    data_type=data_type,
                    # semantic_type 不传 → land_records 内按 data_type 推断(零回归)
                    description=f"S3 采集:{datasource.name} / {key}",
                    note=f"S3 采集 job={job_id} bucket={bucket} key={key}",
                    produced_by_job_id=job_id,
                )
                results.append(pair)

            finally:
                # 无论成功失败都清理临时文件
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)

        if skipped:
            logger.info(
                "S3 采集完成:成功 %d 个对象,跳过 %d 个(格式不支持或解析失败)",
                len(results),
                skipped,
            )

        return results
