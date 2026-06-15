"""外部 S3 数据托管(#18)的客户端与物化解析器。

物化策略=**纯引用·按需拉取**:平台只登记 S3 对象的引用(`s3://bucket/key`),
任何"与受管数据相同"的操作(预览/加工/质量/审核)在需要本地文件时,经
`materialized_version` 临时下载并规范化为 jsonl,用完即清理。

设计见 docs/plan/08-外部S3托管设计.md。

⚠️ 部署红线:本模块(及全代码库)**绝不调用任何 S3 删除**(remove_object /
remove_bucket)——托管不破坏源数据,"取消托管"只删平台引用。

⚠️ minio SDK 是同步阻塞的:所有 minio 调用一律经 `asyncio.to_thread(...)`
下沉到线程池,绝不在事件循环里直接阻塞。
"""

from __future__ import annotations

import asyncio
import io
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import urllib3
from minio import Minio
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.services.landing import BINARY_FORMATS, normalize_to_records

# 列对象/列桶的硬上限,避免大桶把内存/响应打爆(spec §2.1)
_LIST_LIMIT = 1000


class ExternalStoreError(RuntimeError):
    """外部 S3 访问失败(连不上 / 桶或对象缺失 / 凭证错误)。

    由调用方转 4xx,绝不让 S3 客户端错误冒成 500、更不影响受管数据。
    """


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """``s3://bucket/key/path`` → ``(bucket, key/path)``。

    非法(非 s3 scheme / 无 bucket / 无 key)抛 ExternalStoreError。
    """
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ExternalStoreError(f"非法 S3 URI(scheme 应为 s3):{uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise ExternalStoreError(f"非法 S3 URI(缺 bucket 或 key):{uri}")
    return bucket, key


def client_for(config: dict[str, Any] | None, *, fast_fail: bool = False) -> Minio:
    """从 datasource.config 建 Minio 客户端。

    config 约定(与前端 s3 表单一致):endpoint / accessKey / secretKey。
    endpoint 可带 ``http(s)://`` 前缀:据此判断 secure,并剥离 scheme(minio
    的 endpoint 只接受 host[:port],不带 scheme)。MinIO(60)用 http(secure=False),
    AWS S3 用 https。
    """
    config = config or {}
    raw_endpoint = str(config.get("endpoint") or "").strip()
    if not raw_endpoint:
        raise ExternalStoreError("S3 配置缺少 endpoint")

    secure = True
    if raw_endpoint.startswith("http://"):
        secure = False
        endpoint = raw_endpoint[len("http://") :]
    elif raw_endpoint.startswith("https://"):
        secure = True
        endpoint = raw_endpoint[len("https://") :]
    else:
        # 无 scheme:沿用旧约定按 https 处理(AWS S3 默认 https)
        endpoint = raw_endpoint
    endpoint = endpoint.rstrip("/")

    # fast_fail(供「测试连接」):短超时 + 几乎不重试,避免坏 endpoint 上
    # urllib3 默认多次重试拖慢探活;下载/列对象用默认客户端(容忍大对象)。
    http_client = None
    if fast_fail:
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=3.0, read=5.0),
            retries=urllib3.Retry(total=1, connect=1, read=1),
        )
    return Minio(
        endpoint,
        access_key=str(config.get("accessKey") or ""),
        secret_key=str(config.get("secretKey") or ""),
        secure=secure,
        http_client=http_client,
    )


async def test_connection(
    config: dict[str, Any] | None,
) -> tuple[bool, int, str]:
    """真探活:to_thread(list_buckets) + 计时。

    返回 (是否成功, 延迟毫秒, 文案);失败时延迟取 0,文案带原因。镜像
    datasources._probe_postgres 的契约。
    """
    start = perf_counter()
    try:
        client = client_for(config, fast_fail=True)
        await asyncio.to_thread(client.list_buckets)
    except ExternalStoreError as exc:
        return False, 0, f"连接失败:{exc}"
    except Exception as exc:  # noqa: BLE001 探测失败统一兜底为连接失败
        return False, 0, f"连接失败:{exc}"
    latency_ms = int((perf_counter() - start) * 1000)
    return True, latency_ms, f"连接成功,往返延迟 {latency_ms}ms"


async def list_buckets(config: dict[str, Any] | None) -> list[str]:
    """列出全部桶名(仅 s3,真连)。S3 错误抛 ExternalStoreError。"""
    client = client_for(config)
    try:
        buckets = await asyncio.to_thread(client.list_buckets)
    except S3Error as exc:
        raise ExternalStoreError(f"列桶失败:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"列桶失败:{exc}") from exc
    return [b.name for b in buckets]


def _list_objects_sync(
    client: Minio, bucket: str, prefix: str
) -> list[dict[str, Any]]:
    """同步列对象(供 to_thread):上限 _LIST_LIMIT,lastModified 转 ISO 字符串。"""
    items: list[dict[str, Any]] = []
    for obj in client.list_objects(bucket, prefix=prefix or None, recursive=True):
        if obj.is_dir:
            continue
        last_modified = obj.last_modified
        items.append(
            {
                "key": obj.object_name,
                "size": obj.size,
                "lastModified": (
                    last_modified.isoformat() if last_modified else None
                ),
            }
        )
        if len(items) >= _LIST_LIMIT:
            break
    return items


async def list_objects(
    config: dict[str, Any] | None, bucket: str, prefix: str = ""
) -> list[dict[str, Any]]:
    """列对象 [{key,size,lastModified}](上限 1000)。S3 错误抛 ExternalStoreError。"""
    client = client_for(config)
    try:
        return await asyncio.to_thread(_list_objects_sync, client, bucket, prefix)
    except S3Error as exc:
        raise ExternalStoreError(f"列对象失败:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"列对象失败:{exc}") from exc


def _stat_object_sync(client: Minio, bucket: str, key: str) -> dict[str, Any]:
    """同步 stat_object(供 to_thread):取 size,不下载。"""
    stat = client.stat_object(bucket, key)
    return {"size": stat.size}


async def stat_object(
    config: dict[str, Any] | None, bucket: str, key: str
) -> dict[str, Any]:
    """取对象元信息(size),不下载内容。S3 错误抛 ExternalStoreError。"""
    client = client_for(config)
    try:
        return await asyncio.to_thread(_stat_object_sync, client, bucket, key)
    except S3Error as exc:
        raise ExternalStoreError(f"对象不存在或无权访问 {bucket}/{key}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"读取对象元信息失败 {bucket}/{key}:{exc}") from exc


def _download_to_temp_sync(client: Minio, bucket: str, key: str) -> Path:
    """同步流式下载到临时文件(供 to_thread),返回临时路径。"""
    suffix = Path(key).suffix
    fd, tmp_name = tempfile.mkstemp(prefix="adp-s3-", suffix=suffix)
    tmp_path = Path(tmp_name)
    response = None
    try:
        response = client.get_object(bucket, key)
        with open(fd, "wb") as fp:
            for chunk in response.stream(64 * 1024):
                fp.write(chunk)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if response is not None:
            response.close()
            response.release_conn()
    return tmp_path


async def download_to_temp(
    config: dict[str, Any] | None, bucket: str, key: str
) -> Path:
    """流式下载 S3 对象到临时文件,返回临时路径(调用方负责清理)。

    S3 错误抛 ExternalStoreError(不 500)。
    """
    client = client_for(config)
    try:
        return await asyncio.to_thread(_download_to_temp_sync, client, bucket, key)
    except S3Error as exc:
        raise ExternalStoreError(f"下载对象失败 {bucket}/{key}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"下载对象失败 {bucket}/{key}:{exc}") from exc


async def head_records(
    config: dict[str, Any] | None,
    bucket: str,
    key: str,
    fmt: str,
    limit: int,
) -> list[dict[str, Any]]:
    """预览用:下载对象 → normalize_to_records 取前 limit 条。

    简单稳妥(大对象成本在 spec §5 已注明,预览靠取前 N 缓解):整对象下载到
    临时区,规范化后截前 limit。用完即清理临时文件。
    """
    tmp_path = await download_to_temp(config, bucket, key)
    try:
        content = await asyncio.to_thread(tmp_path.read_bytes)
        records = normalize_to_records(content, fmt)
        return records[:limit] if limit > 0 else records
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_jsonl_sync(records: list[dict[str, Any]]) -> Path:
    """同步把记录写临时 jsonl(供 to_thread),返回临时路径。"""
    fd, tmp_name = tempfile.mkstemp(prefix="adp-s3-norm-", suffix=".jsonl")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fp:
            for rec in records:
                fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


@asynccontextmanager
async def materialized_version(
    version: DatasetVersion, session: AsyncSession
) -> AsyncIterator[Path]:
    """物化解析器:把一个版本解析为一个 DJ 可读的本地 jsonl 路径。

    - 受管(origin != "hosted"):原样透传 `Path(version.storage_uri)`,零行为变化、
      零额外 IO——既有受管流程完全不受影响。
    - hosted:据 source_datasource_id 找回 S3 凭证 → 下载 S3 对象 →
      normalize_to_records(复用 landing 的规范化)→ 写临时 jsonl → yield 该路径;
      退出时清理下载文件与临时 jsonl。

    S3/对象错误抛 ExternalStoreError(由调用方转 4xx,不 500、不动源)。
    """
    if version.origin != "hosted":
        yield Path(version.storage_uri)
        return

    # 二进制 hosted 版本无法规范化为 jsonl(加工/物化不适用)→ 明确报错,
    # 避免 normalize_to_records 对二进制字节抛未捕获的 UnsupportedFormatError(冒 500)
    if version.format in BINARY_FORMATS:
        raise ExternalStoreError(
            f"二进制格式 .{version.format} 不支持物化/加工,请下载查看或选择文本类数据集"
        )

    if version.source_datasource_id:
        ds = await session.get(DataSource, version.source_datasource_id)
        if ds is None:
            raise ExternalStoreError("托管版本对应的数据源已不存在,无法访问 S3")
        cfg = ds.config
    else:
        # 平台对象零拷贝接入:用平台 MinIO 凭证(未配置 → ExternalStoreError)
        cfg = platform_config()

    bucket, key = parse_s3_uri(version.storage_uri)
    raw_path = await download_to_temp(cfg, bucket, key)
    jsonl_path: Path | None = None
    try:
        content = await asyncio.to_thread(raw_path.read_bytes)
        records = normalize_to_records(content, version.format)
        jsonl_path = await asyncio.to_thread(_write_jsonl_sync, records)
        yield jsonl_path
    finally:
        raw_path.unlink(missing_ok=True)
        if jsonl_path is not None:
            jsonl_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 平台 MinIO 文件管理(#19):管理平台自有对象存储,允许写/删(软保护在路由层)
# ---------------------------------------------------------------------------
def platform_config() -> dict[str, Any]:
    """从 settings 拼平台 MinIO 的 {endpoint, accessKey, secretKey}。

    缺 endpoint / 任一凭证 → 抛 ExternalStoreError("平台存储(MinIO)未配置"),
    由路由层兜底为 503,不报 500。
    """
    endpoint = (settings.storage_minio_endpoint or "").strip()
    access_key = (settings.storage_minio_access_key or "").strip()
    secret_key = (settings.storage_minio_secret_key or "").strip()
    if not (endpoint and access_key and secret_key):
        raise ExternalStoreError("平台存储(MinIO)未配置")
    return {"endpoint": endpoint, "accessKey": access_key, "secretKey": secret_key}


def _list_dir_sync(
    client: Minio, bucket: str, prefix: str
) -> dict[str, list[Any]]:
    """同步分组列举(供 to_thread):folders + files 两类,上限 _LIST_LIMIT。"""
    folders: list[str] = []
    files: list[dict[str, Any]] = []
    count = 0
    for obj in client.list_objects(bucket, prefix=prefix or None, recursive=False):
        name = obj.object_name
        if obj.is_dir:
            # 跳过当前目录自身的零字节标记对象(minio-py 对 key 以 '/' 结尾的对象
            # 置 is_dir=True,列举自身目录时会把标记当成同名幻影子目录)
            if name == prefix:
                continue
            # 文件夹:取末段(去尾部 '/')
            folders.append(name.rstrip("/").rsplit("/", 1)[-1])
        else:
            # 跳过等于 prefix 自身的零字节文件夹标记
            if name == prefix:
                continue
            last_modified = obj.last_modified
            files.append(
                {
                    "key": name,
                    "name": name[len(prefix):] if prefix else name,
                    "size": obj.size,
                    "lastModified": (
                        last_modified.isoformat() if last_modified else None
                    ),
                }
            )
        count += 1
        if count >= _LIST_LIMIT:
            break
    return {"folders": folders, "files": files}


async def list_dir(
    config: dict[str, Any] | None, bucket: str, prefix: str = ""
) -> dict[str, list[Any]]:
    """列单层目录:{folders:[name], files:[{key,name,size,lastModified}]}(上限 1000)。"""
    client = client_for(config)
    try:
        return await asyncio.to_thread(_list_dir_sync, client, bucket, prefix)
    except S3Error as exc:
        raise ExternalStoreError(f"列目录失败:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"列目录失败:{exc}") from exc


async def upload_object(
    config: dict[str, Any] | None,
    bucket: str,
    key: str,
    data: Any,
    length: int,
    content_type: str = "application/octet-stream",
) -> None:
    """上传对象(put_object)。S3 错误抛 ExternalStoreError。"""
    client = client_for(config)
    try:
        await asyncio.to_thread(
            client.put_object, bucket, key, data, length, content_type=content_type
        )
    except S3Error as exc:
        raise ExternalStoreError(f"上传失败 {bucket}/{key}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"上传失败 {bucket}/{key}:{exc}") from exc


async def create_folder(
    config: dict[str, Any] | None, bucket: str, prefix: str
) -> None:
    """新建"文件夹":put 一个零字节对象,key = prefix.rstrip('/') + '/'。"""
    key = prefix.rstrip("/") + "/"
    client = client_for(config)
    try:
        await asyncio.to_thread(
            client.put_object, bucket, key, io.BytesIO(b""), 0
        )
    except S3Error as exc:
        raise ExternalStoreError(f"新建文件夹失败 {bucket}/{key}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"新建文件夹失败 {bucket}/{key}:{exc}") from exc


async def remove_object(
    config: dict[str, Any] | None, bucket: str, key: str
) -> None:
    """删单个对象(remove_object)。S3 错误抛 ExternalStoreError。"""
    client = client_for(config)
    try:
        await asyncio.to_thread(client.remove_object, bucket, key)
    except S3Error as exc:
        raise ExternalStoreError(f"删除失败 {bucket}/{key}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"删除失败 {bucket}/{key}:{exc}") from exc


def _remove_prefix_sync(client: Minio, bucket: str, prefix: str) -> int:
    """同步递归删前缀下所有对象(供 to_thread),返回删除条数。"""
    count = 0
    for obj in client.list_objects(bucket, prefix=prefix or None, recursive=True):
        client.remove_object(bucket, obj.object_name)
        count += 1
    return count


async def remove_prefix(
    config: dict[str, Any] | None, bucket: str, prefix: str
) -> int:
    """递归删前缀(文件夹)下所有对象,返回删除条数。S3 错误抛 ExternalStoreError。"""
    client = client_for(config)
    try:
        return await asyncio.to_thread(_remove_prefix_sync, client, bucket, prefix)
    except S3Error as exc:
        raise ExternalStoreError(f"删除目录失败 {bucket}/{prefix}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"删除目录失败 {bucket}/{prefix}:{exc}") from exc


async def presigned_get_url(
    config: dict[str, Any] | None,
    bucket: str,
    key: str,
    expires_seconds: int = 600,
) -> str:
    """生成预签名下载 URL(浏览器可直连 MinIO endpoint 下载)。"""
    client = client_for(config)
    try:
        return await asyncio.to_thread(
            client.presigned_get_object,
            bucket,
            key,
            expires=timedelta(seconds=expires_seconds),
        )
    except S3Error as exc:
        raise ExternalStoreError(f"生成下载链接失败 {bucket}/{key}:{exc}") from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(f"生成下载链接失败 {bucket}/{key}:{exc}") from exc
