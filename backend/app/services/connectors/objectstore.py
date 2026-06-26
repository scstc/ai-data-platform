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
import io
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.services.connectors.base import ConnectorNotReady, IngestError
from app.services.external_store import (
    MAX_MANIFEST_MEMBERS,
    MAX_MATERIALIZE_BYTES,
    ExternalStoreError,
    download_to_temp,
    list_objects,
    platform_config,
    remove_prefix,
    test_connection,
    upload_object,
)
from app.services.landing import (
    BINARY_FORMATS,
    MANIFEST_FORMAT,
    UnsupportedFormatError,
    _new_dataset_id,
    _new_version_id,
    land_records,
    media_kind,
    normalize_to_records,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.datasource import DataSource
    from app.models.ingest_task import IngestTask

logger = logging.getLogger(__name__)

# 模态名 → DJ 多模态 jsonl 字段 + 特殊 token(与 datasets.upload-media 契约一致)
_MEDIA_FIELD = {"image": "images", "audio": "audios", "video": "videos"}
_MEDIA_TOKEN = {
    "image": "<__dj__image>",
    "audio": "<__dj__audio>",
    "video": "<__dj__video>",
}


def _ext(key: str) -> str:
    """取对象键扩展名(小写、不含点);无扩展名返回空串。"""
    suffix = Path(key).suffix
    return suffix[1:].lower() if suffix else ""


def _media_manifest_row(member_key: str, name: str, size: int, fmt: str) -> dict:
    """构造一行媒体清单(DJ 多模态 jsonl 契约 + 平台旁路 __member + type 模态标注)。

    - 多模态字段(images/audios/videos)按模态落 [member_key] 数组,喂 dj rel2abs;
    - text 填该模态的 dj 特殊 token;
    - **type 显式标注该文件模态**(image/audio/video)——满足「桶内多种文件混装时,
      逐行 json 用 type 说明该文件类型」;
    - __member 为平台旁路元信息(成员列表 / 预览 / 物化回传用,dj 物化时剥除)。
    """
    kind = media_kind(fmt)
    field = _MEDIA_FIELD[kind]
    return {
        field: [member_key],
        "type": kind,
        "text": _MEDIA_TOKEN[kind],
        "__member": {
            "bucket": settings.storage_minio_upload_bucket,
            "key": member_key,
            "name": name,
            "size": size,
            "format": fmt,
        },
    }


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

    S3 对象键不含前导 /:paths 与 glob 中的前导 / 都会被剥掉,与 paths 分支
    lstrip("/") 一致——避免用户按 HDFS/POSIX 习惯写了 /raw/*.csv 或 /*.*
    却匹配为空(线上回归:glob /*.* 在非空桶里命中 0,误报「采集对象为空」)。
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
    paths_typed = False  # 是否有非空白 path 输入(供失败诊断区分「没配」vs「配了没命中」)
    for raw in raw_paths:
        raw = raw.strip()
        if not raw:
            continue
        paths_typed = True
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
    glob_typed: str = str(extract.get("glob") or "").strip()
    # 剥前导 /:S3 键不含前导 /,与 paths 分支行为一致(见函数 docstring)。
    glob_pattern: str = glob_typed.lstrip("/")
    if glob_pattern:
        for obj in all_objects:
            obj_key: str = obj.get("key") or ""
            if obj_key and fnmatch.fnmatch(obj_key, glob_pattern):
                seen[obj_key] = None

    if not seen:
        if not paths_typed and not glob_typed:
            raise IngestError(
                "S3 采集对象为空:"
                "请在 extract.paths 填写对象键列表,或在 extract.glob 填写通配符"
            )
        # 配了采集对象却没命中——给可定位的诊断(显式 glob 值 + 桶内对象数 +
        # 前导 / 提示),而非笼统的「请填写通配符」(Rule 12:诚实、可调试)。
        raise IngestError(
            "S3 采集未匹配到任何对象:"
            f"glob={glob_typed!r}(去前导 / 后={glob_pattern!r}),"
            f"桶内共 {len(all_objects)} 个对象;"
            "S3 对象键不含前导 /,匹配全量请用 *.* 或 *"
        )

    return list(seen)


# ---------------------------------------------------------------------------
# 增量过滤 + 水位推进(切片 C / Task 5)
# ---------------------------------------------------------------------------


def _filter_keys_by_watermark(
    keys: list[str],
    all_objects: list[dict[str, Any]],
    incremental: dict[str, Any] | None,
    watermark: dict[str, Any] | None,
) -> list[str]:
    """按增量水位过滤已匹配的 keys,只保留「比水位新」的子集(保序)。

    - ``incremental.by='mtime'``:用 ``all_objects[*].lastModified > watermark['value']``
      判定;缺 lastModified 的 key 视为「无据判断 newer-than」→ 被过滤(避免重复)。
    - ``incremental.by='name'``:用 ``key > watermark['value']`` 字典序比较
      (适合按日期/序号命名的批次文件)。
    - 首跑(``watermark`` 为 None / 缺 ``value``)→ 不过滤(全量采)。
    - 无 ``incremental`` → 不过滤(零回归:既有任务行为不变)。
    - 未知 ``by`` → 不过滤(诚实降级,不丢数据)。

    与 HDFS ``_filter_paths_by_watermark`` 同形(后者仅支持 by=name,因为当前
    HDFS 连接器不走 LISTSTATUS 拿 mtime);若后续 HDFS 接 mtime,可统一到此函数。
    """
    if not incremental:
        return keys
    wm_value = (watermark or {}).get("value")
    if wm_value is None:
        return keys  # 首跑(无水位)→ 全量
    by = incremental.get("by")
    if by == "mtime":
        mtime_map: dict[str, Any] = {
            obj.get("key"): obj.get("lastModified")
            for obj in all_objects
            if obj.get("key")
        }
        return [
            k for k in keys
            if mtime_map.get(k) is not None and mtime_map[k] > wm_value
        ]
    if by == "name":
        return [k for k in keys if k > wm_value]
    return keys  # 未知 by → 不过滤


def _compute_file_watermark(
    keys: list[str],
    all_objects: list[dict[str, Any]],
    incremental: dict[str, Any] | None,
) -> Any | None:
    """计算本轮采集后应推进到的新水位值(本批已采 keys 的「最大值」)。

    - ``by='mtime'`` → ``max(lastModified of keys)``,忽略 None;
    - ``by='name'``  → ``max(key)``;
    - 空 keys / 无 incremental / 未知 by / 全部缺值 → ``None``
      (调用方据此**不推进**:无新增不动水位,避免误把 None 写入后下轮当成首跑全量)。
    """
    if not incremental or not keys:
        return None
    by = incremental.get("by")
    if by == "mtime":
        mtime_map = {
            obj.get("key"): obj.get("lastModified")
            for obj in all_objects
            if obj.get("key")
        }
        values = [
            mtime_map[k] for k in keys
            if k in mtime_map and mtime_map[k] is not None
        ]
        return max(values) if values else None
    if by == "name":
        return max(keys)
    return None


def _advance_file_watermark(
    task: IngestTask,
    keys: list[str],
    all_objects: list[dict[str, Any]],
    incremental: dict[str, Any] | None,
) -> None:
    """以 running-max 方式把「已成功落地的 keys」的 max(mtime|name) 写回 ``task.watermark``。

    必须在每个 key/媒体批次 land_records **成功之后**调用——取
    ``max(当前水位, 本批 keys 的 max)``,保证水位永远不会超过「实际已落地」的
    范畴。中途某个 key 落地失败抛异常 → 水位只反映此前已成功落地的 keys,失败的
    key 及后续 key 在重试时仍被采(防 DATA LOSS,C5 评审 Finding 1)。
    空 keys / 无 incremental / 计算值为 None → 不动既有水位(零回归)。
    """
    if not incremental or not keys:
        return
    new_value = _compute_file_watermark(keys, all_objects, incremental)
    if new_value is None:
        return  # 空批 → 不推进
    from datetime import UTC, datetime  # noqa: PLC0415

    current_wm = getattr(task, "watermark", None) or {}
    current_value = current_wm.get("value")
    merged = new_value if current_value is None else max(current_value, new_value)
    task.watermark = {
        "value": merged,
        "updatedAt": datetime.now(UTC).isoformat(),
    }


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
        """S3 平铺文件采集(s3/minio/oss/obs 同此连接器,endpoint 区分厂商)。

        按对象扩展名分两路处理:
        - **媒体二进制**(图/音/视频,BINARY_FORMATS):不解析,**原样复制进平台内置
          MinIO**(uploads 桶),整批汇成**一个** manifest 数据集——每行带 images/audios/
          videos 字段 + dj token + **type 模态标注**(满足多类型混装逐行说明);
          下游加工物化时按 manifest 下载成员(见 external_store.materialized_version)。
        - **结构化/文本/文档**:下载 → normalize → land_records,每对象一集(原行为)。

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

        # 切片 C / Task 5:增量过滤——按 task.incremental(by=mtime|name)
        # + task.watermark 过滤掉「不比水位新」的对象(只采新增)。
        # 首跑(无 watermark)/ 无 incremental → 不过滤(零回归,全量采)。
        incremental = getattr(task, "incremental", None)
        keys = _filter_keys_by_watermark(
            keys, all_objects, incremental, getattr(task, "watermark", None)
        )

        # 按扩展名分流:媒体走「复制进内置 MinIO + 汇成 manifest」,其余走逐对象落地
        media_keys = [k for k in keys if _ext(k) in BINARY_FORMATS]
        data_keys = [k for k in keys if _ext(k) not in BINARY_FORMATS]

        results: list[tuple[Dataset, DatasetVersion]] = []
        skipped = 0

        # C5 评审 Finding 1 修复:水位推进改为 running-max-after-success——
        # 每个 key 成功落地后再推进,中途失败水位只反映已落地部分(防 DATA LOSS)。

        for key in data_keys:
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
                    # 三轴:来源=对象存储;格式=对象原始扩展名
                    source_kind="object_store",
                    source_format=ext,
                    description=f"S3 采集:{datasource.name} / {key}",
                    note=f"S3 采集 job={job_id} bucket={bucket} key={key}",
                    produced_by_job_id=job_id,
                )
                results.append(pair)
                # 成功落地后推进水位(running max of landed keys only)——
                # 失败的 key 不推进,重试时仍可被采。
                _advance_file_watermark(task, [key], all_objects, incremental)

            finally:
                # 无论成功失败都清理临时文件
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)

        # 媒体文件:原样复制进平台内置 MinIO + 汇成一个 manifest 数据集(逐行带 type)
        if media_keys:
            pair = await self._ingest_media_to_manifest(
                session,
                task=task,
                datasource=datasource,
                src_config=config,
                src_bucket=bucket,
                media_keys=media_keys,
                data_type=data_type,
                job_id=job_id,
            )
            results.append(pair)
            # 媒体 manifest 成功落地后推进水位(整批 media_keys 作为一个单元)
            _advance_file_watermark(task, media_keys, all_objects, incremental)

        if skipped:
            logger.info(
                "S3 采集完成:成功 %d 个对象,跳过 %d 个(格式不支持或解析失败)",
                len(results),
                skipped,
            )

        return results

    # ------------------------------------------------------------------
    # 媒体清单落地 —— 复制进平台内置 MinIO + 汇成一个 manifest 数据集
    # ------------------------------------------------------------------

    @staticmethod
    async def _gc_prefix(cfg: dict, bucket: str, prefix: str) -> None:
        """尽力回收平台 MinIO 某前缀(失败半成品),不可达不阻断上层报错。"""
        try:
            await remove_prefix(cfg, bucket, prefix)
        except ExternalStoreError:
            pass

    async def _ingest_media_to_manifest(
        self,
        session: AsyncSession,
        *,
        task: IngestTask,
        datasource: DataSource,
        src_config: dict[str, Any],
        src_bucket: str,
        media_keys: list[str],
        data_type: str | None,
        job_id: str,
    ) -> tuple[Dataset, DatasetVersion]:
        """媒体对象 → 逐个从源对象存储下载,原样复制进平台内置 MinIO(uploads 桶),
        整批汇成**一个** format=manifest 数据集(每行带 images/audios/videos + dj token +
        type 模态标注)。返回 (Dataset, DatasetVersion)。

        与 datasets.upload-media 同一清单契约,故复用既有物化/成员/预览路径:不同点
        仅在每行追加 type 字段(混装多模态时逐行说明类型,dj 物化时随其余字段透传)。

        失败(下载/写入/超限)→ 回滚未提交行 + 回收已写平台对象 + 抛 IngestError;
        绝不留下没有清单的孤儿对象,也绝不动源对象存储(只读源、只写平台)。
        """
        if len(media_keys) > MAX_MANIFEST_MEMBERS:
            raise IngestError(
                f"S3 媒体采集一次最多 {MAX_MANIFEST_MEMBERS} 个文件,"
                f"实际 {len(media_keys)} 个,请缩小 paths/glob 范围"
            )
        try:
            cfg = platform_config()
        except ExternalStoreError as exc:
            raise IngestError(
                f"平台内置存储(MinIO)未配置,无法复制媒体文件:{exc}"
            ) from exc

        dst_bucket = settings.storage_minio_upload_bucket
        dataset_id = _new_dataset_id()
        prefix = f"{dataset_id}/"
        manifest_rows: list[dict] = []
        total_size = 0
        try:
            for idx, key in enumerate(media_keys):
                tmp_path: Path | None = None
                try:
                    try:
                        tmp_path = await download_to_temp(src_config, src_bucket, key)
                    except ExternalStoreError as exc:
                        raise IngestError(
                            f"S3 媒体下载失败 {src_bucket}/{key}:{exc}"
                        ) from exc
                    size = tmp_path.stat().st_size
                    total_size += size
                    if total_size > MAX_MATERIALIZE_BYTES:
                        raise IngestError(
                            "本次媒体采集总体积超过上限,无法复制/加工"
                        )
                    fmt = _ext(key) or tmp_path.suffix.lstrip(".").lower()
                    base = Path(key).name  # 去路径,防 key 注入
                    member_key = f"{prefix}{idx:06d}-{base}"
                    # 流式上传(从临时文件句柄,不把大媒体读进内存)
                    with tmp_path.open("rb") as fp:
                        await upload_object(cfg, dst_bucket, member_key, fp, size)
                    manifest_rows.append(
                        _media_manifest_row(member_key, base, size, fmt)
                    )
                finally:
                    if tmp_path is not None:
                        tmp_path.unlink(missing_ok=True)

            manifest_bytes = (
                "\n".join(
                    json.dumps(r, ensure_ascii=False) for r in manifest_rows
                )
                + "\n"
            ).encode("utf-8")
            manifest_key = f"{prefix}manifest.jsonl"
            await upload_object(
                cfg,
                dst_bucket,
                manifest_key,
                io.BytesIO(manifest_bytes),
                len(manifest_bytes),
                content_type="application/x-ndjson",
            )

            dataset = Dataset(
                id=dataset_id,
                name=task.name or datasource.name,
                description=(
                    f"S3 媒体采集:{datasource.name}"
                    f"({len(manifest_rows)} 个文件)"
                ),
                data_type=data_type,
                category_id=task.category_id,
                owner="admin",
                creator="admin",
            )
            session.add(dataset)
            version = DatasetVersion(
                id=_new_version_id(),
                dataset_id=dataset_id,
                version_no=1,
                storage_uri=f"s3://{dst_bucket}/{manifest_key}",
                format=MANIFEST_FORMAT,
                rows=len(manifest_rows),
                size=total_size,
                origin="managed",
                source_datasource_id=None,
                produced_by_job_id=job_id,
                note=(
                    f"S3 媒体采集 job={job_id} src={src_bucket}"
                    f"({len(manifest_rows)} 个文件)"
                ),
            )
            session.add(version)
            await session.commit()
            await session.refresh(dataset)
            await session.refresh(version)
            return dataset, version
        except IngestError:
            await session.rollback()
            await self._gc_prefix(cfg, dst_bucket, prefix)
            raise
        except ExternalStoreError as exc:
            await session.rollback()
            await self._gc_prefix(cfg, dst_bucket, prefix)
            raise IngestError(
                f"S3 媒体复制写入平台存储失败:{exc}"
            ) from exc
