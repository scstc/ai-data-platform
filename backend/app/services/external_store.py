"""外部 S3 数据托管(#18)的客户端与物化解析器。

物化策略=**纯引用·按需拉取 + 物化缓存**:平台只登记 S3 对象的引用(`s3://bucket/key`),
任何"与受管数据相同"的操作(预览/加工/质量/审核)在需要本地文件时,经
`materialized_version` 取对象并规范化为 jsonl。取对象走 `cached_bytes`:按
(endpoint,bucket,key,etag) 缓存到本地磁盘,命中即免重复下载,etag 变即自动作废重拉;
缓存只是可丢弃的性能副本,绝不回写源——source of truth 始终在三方 S3。

设计见 docs/plan/08-外部S3托管设计.md。

⚠️ 部署红线:本模块(及全代码库)**绝不调用任何 S3 删除**(remove_object /
remove_bucket)——托管不破坏源数据,"取消托管"只删平台引用。

⚠️ minio SDK 是同步阻塞的:所有 minio 调用一律经 `asyncio.to_thread(...)`
下沉到线程池,绝不在事件循环里直接阻塞。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import shutil
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
from app.services.landing import (
    BINARY_FORMATS,
    MANIFEST_FORMAT,
    normalize_to_records,
)

logger = logging.getLogger(__name__)

# 列对象/列桶的硬上限,避免大桶把内存/响应打爆(spec §2.1)
_LIST_LIMIT = 1000

# manifest 物化下载的扇出上限(防止大媒体集打爆临时盘/拖垮 dj-process)
MAX_MANIFEST_MEMBERS = 1000
MAX_MATERIALIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB


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
    # 华为云 OBS 适配:OBS 只支持虚拟主机域名寻址(bucket.endpoint/key),不接受
    # path-style(endpoint/bucket/key),否则以 VirtualHostDomainRequired(403)拒绝。
    # minio SDK 对 OBS 有两处默认行为会触发该 403,都要绕过:
    #   1. 默认仅对 AWS / 阿里云 OSS 开虚拟寻址,OBS 落 path-style —— 手动开
    #      _base_url._virtual_style_flag(SDK 无公开开关)让真正的 List/Get 走虚拟寻址;
    #   2. 首次访问桶时先发 GetBucketLocation 预检,该请求带 location 查询参数,被
    #      minio 强制回 path-style(build() 里 location→enforce_path_style),照样被 OBS
    #      拒 —— 显式传 region(从 obs.<region>.myhuaweicloud.com 解析),令 _get_region
    #      命中 base_url.region 直接返回、跳过预检。
    # 注:含 '.' 的桶名在 https 下仍被 minio 强制回 path-style(避免 SSL 证书校验失败),
    # OBS 桶名通常不含点,如遇此情况需改用不含点的桶名。
    host = endpoint.split(":", 1)[0].lower()
    is_obs = "myhuaweicloud" in host
    region: str | None = None
    if is_obs:
        # obs.cn-north-4.myhuaweicloud.com / obs.dualstack.cn-north-4.myhuaweicloud.com
        left = host.split(".myhuaweicloud", 1)[0]
        labels = [x for x in left.split(".") if x]
        if len(labels) >= 2:
            region = labels[-1]  # cn-north-4

    client = Minio(
        endpoint,
        access_key=str(config.get("accessKey") or ""),
        secret_key=str(config.get("secretKey") or ""),
        secure=secure,
        region=region,
        http_client=http_client,
    )
    if is_obs:
        base_url = getattr(client, "_base_url", None)
        if base_url is not None:
            base_url._virtual_style_flag = True

    return client


def s3_settings_for_duckdb(
    config: dict[str, Any] | None,
) -> tuple[str, bool, str, str]:
    """从 S3 cfg(``datasource.config`` 或 ``platform_config()``)解析 DuckDB
    httpfs 所需设置。

    返回 ``(endpoint, use_ssl, access_key, secret_key)``。endpoint 剥 scheme、
    只留 ``host[:port]``(DuckDB 的 ``s3_endpoint`` 不带 scheme),与 ``client_for``
    的 http/https 判定一致:http → use_ssl=False(平台 MinIO),https/无 scheme → True。
    缺 endpoint 或凭证 → ``ExternalStoreError``(调用方转 4xx/503)。
    """
    config = config or {}
    raw = str(config.get("endpoint") or "").strip()
    if not raw:
        raise ExternalStoreError("S3 配置缺少 endpoint")
    use_ssl = True
    if raw.startswith("http://"):
        use_ssl = False
        endpoint = raw[len("http://") :]
    elif raw.startswith("https://"):
        use_ssl = True
        endpoint = raw[len("https://") :]
    else:
        endpoint = raw
    endpoint = endpoint.rstrip("/")
    access_key = str(config.get("accessKey") or "")
    secret_key = str(config.get("secretKey") or "")
    if not (access_key and secret_key):
        raise ExternalStoreError("S3 配置缺少凭证")
    return endpoint, use_ssl, access_key, secret_key


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
    """同步 stat_object(供 to_thread):取 size/etag,不下载。"""
    stat = client.stat_object(bucket, key)
    return {"size": stat.size, "etag": stat.etag}


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
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("下载失败后清理临时文件失败:%s", tmp_path)
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


# ---------------------------------------------------------------------------
# 物化缓存(#18 性能优化):按 (endpoint,bucket,key,etag) 把三方 S3 对象缓存到
# 本地磁盘,避免每次加工/预览重复拉取。缓存是可丢弃的性能副本——绝不回写源、
# etag 变即自动作废重拉;LRU(按访问时间 mtime)+ 总量上限淘汰。
# 语义不变:hosted 的 source of truth 仍在三方 S3,取消托管/清缓存都不动源。
# ---------------------------------------------------------------------------
# 每个 cache key 一把锁:同一对象并发命中只下载一次(去重 + 防半写读)
_cache_locks: dict[str, asyncio.Lock] = {}
_cache_locks_guard = asyncio.Lock()
# 淘汰串行化:install/evict 这段(快)全局互斥,避免并发 iterdir/unlink 打架;
# 真正慢的下载在此锁之外,不串行化。
_evict_lock = asyncio.Lock()


def _cache_key(config: dict[str, Any] | None, bucket: str, key: str, etag: str) -> str:
    """缓存键:对 (endpoint,bucket,key,etag) 取 sha256;etag 入键 → 源变即换条目。"""
    endpoint = str((config or {}).get("endpoint") or "")
    raw = f"{endpoint}|{bucket}|{key}|{etag}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _lock_for(cache_key: str) -> asyncio.Lock:
    """取/建某 cache key 的锁(创建过程本身受 _cache_locks_guard 保护)。"""
    async with _cache_locks_guard:
        lock = _cache_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _cache_locks[cache_key] = lock
        return lock


def _install_and_evict(tmp_path: Path, cache_path: Path, max_bytes: int) -> None:
    """把下载好的临时文件移入缓存并按总量上限淘汰(同步,供 to_thread)。

    移动可能跨文件系统(临时目录 vs 缓存目录)→ 用 shutil.move(copy+del)。
    淘汰:按 mtime 旧→新删除,直到总量 <= max_bytes;绝不删刚装入的 cache_path。
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), str(cache_path))
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for p in cache_path.parent.iterdir():
        if not p.is_file():
            continue
        st = p.stat()
        entries.append((st.st_mtime, st.st_size, p))
        total += st.st_size
    entries.sort()  # 最旧的在前
    for _mtime, size, p in entries:
        if total <= max_bytes:
            break
        if p == cache_path:
            continue  # 刚装入的不淘汰(否则本次取数白下载)
        try:
            p.unlink()
            total -= size
        except OSError:
            pass


async def cached_bytes(
    config: dict[str, Any] | None, bucket: str, key: str
) -> bytes:
    """取 S3 对象字节,优先命中本地物化缓存,未命中则下载并缓存。

    - 缓存关闭(max_bytes<=0):退化为按需下载到临时区,读完即清(原行为)。
    - 缓存开启:先 stat 取 etag(廉价 HEAD)→ 命中则 touch(更新 LRU 访问时间)直接读;
      未命中则下载 → 移入缓存 → 淘汰 → 读。同 key 并发只下载一次。

    与 download_to_temp 一致:S3/对象错误抛 ExternalStoreError(不 500、不动源)。
    """
    max_bytes = settings.hosted_cache_max_bytes
    if max_bytes <= 0:
        tmp_path = await download_to_temp(config, bucket, key)
        try:
            return await asyncio.to_thread(tmp_path.read_bytes)
        finally:
            tmp_path.unlink(missing_ok=True)

    etag = str((await stat_object(config, bucket, key)).get("etag") or "")
    ck = _cache_key(config, bucket, key, etag)
    cache_path = Path(settings.hosted_cache_dir) / ck
    lock = await _lock_for(ck)
    async with lock:
        if cache_path.exists():
            # LRU touch:把访问时间推到最新,避免热对象被误淘汰
            await asyncio.to_thread(os.utime, cache_path, None)
            return await asyncio.to_thread(cache_path.read_bytes)
        tmp_path = await download_to_temp(config, bucket, key)
        try:
            async with _evict_lock:
                await asyncio.to_thread(
                    _install_and_evict, tmp_path, cache_path, max_bytes
                )
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("安装缓存失败后清理临时文件失败:%s", tmp_path)
            raise
        return await asyncio.to_thread(cache_path.read_bytes)


_INTERNAL_MINIO_ORIGIN = "http://minio:9000"


def browser_file_url(url: str) -> str:
    """presigned URL 归一为浏览器可达形态:内部域名(http://minio:9000,全容器化
    部署)改写为相对路径 /minio/...,浏览器基于页面同源解析后由前端 nginx 反代回
    MinIO(Host 固定与签名一致,SigV4 仍有效);其余形态(宿主 IP 直连、外部 S3)
    原样返回。与前端 utils/storageUrl.ts 的 toBrowserFileUrl 同一约定。
    仅用于交给浏览器跳转/下载的 URL;容器间消费(如 kkFileView 取件)勿用。"""
    prefix = f"{_INTERNAL_MINIO_ORIGIN}/"
    return f"/minio/{url[len(prefix):]}" if url.startswith(prefix) else url


def json_safe(value: Any) -> Any:
    """递归把非有限浮点(NaN/±Inf)转 None。json.loads 默认放行这些非标字面量,
    而 JSONResponse 按严格 JSON(allow_nan=False)序列化,漏一个整个接口 500。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


async def head_records(
    config: dict[str, Any] | None,
    bucket: str,
    key: str,
    fmt: str,
    limit: int,
) -> list[dict[str, Any]]:
    """预览用:取对象(命中缓存则免下载)→ normalize_to_records 取前 limit 条。

    简单稳妥(大对象成本在 spec §5 已注明,预览靠取前 N 缓解):规范化后截前 limit,
    出口统一 json_safe 清洗(数据里的 NaN/Inf 会让预览接口 500)。
    """
    content = await cached_bytes(config, bucket, key)
    records = normalize_to_records(content, fmt)
    return json_safe(records[:limit] if limit > 0 else records)


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


async def _version_cfg(
    version: DatasetVersion, session: AsyncSession
) -> dict[str, Any]:
    """解析版本访问凭证:有 source_datasource_id 用其数据源,否则平台 MinIO 回退。"""
    if version.source_datasource_id:
        ds = await session.get(DataSource, version.source_datasource_id)
        if ds is None:
            raise ExternalStoreError("托管版本对应的数据源已不存在,无法访问 S3")
        return ds.config
    return platform_config()


@asynccontextmanager
async def _materialized_manifest(
    version: DatasetVersion, session: AsyncSession
) -> AsyncIterator[Path]:
    """物化 manifest 版本:清单 + 各媒体成员下到同一临时目录,images/audios/videos
    路径改写为本地相对文件名(dj rel2abs 以 jsonl 目录为锚),剥掉平台旁路 __member。
    带成员数/总字节上限,退出时整目录清理。"""
    cfg = await _version_cfg(version, session)
    bucket, manifest_key = parse_s3_uri(version.storage_uri)
    # images/audios/videos 存的是相对 manifest 所在前缀的文件名(见
    # landing.land_media_manifest 的写入约定),下载前拼回完整对象 key。
    manifest_prefix = manifest_key.rpartition("/")[0]
    manifest_raw = await download_to_temp(cfg, bucket, manifest_key)
    try:
        lines = [
            ln
            for ln in manifest_raw.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    finally:
        manifest_raw.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="adp-manifest-") as td:
        tmpdir = Path(td)
        out_path = tmpdir / "data.jsonl"
        total_bytes = 0
        count = 0
        with out_path.open("w", encoding="utf-8") as out:
            for line in lines:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ExternalStoreError(f"清单格式损坏:{exc}") from exc
                for field in ("images", "audios", "videos"):
                    paths = row.get(field)
                    if not isinstance(paths, list):
                        continue
                    local_names: list[str] = []
                    for rel_name in paths:
                        if count >= MAX_MANIFEST_MEMBERS:
                            raise ExternalStoreError(
                                f"清单成员超过上限 {MAX_MANIFEST_MEMBERS},无法物化加工"
                            )
                        member_key = (
                            f"{manifest_prefix}/{rel_name}"
                            if manifest_prefix
                            else str(rel_name)
                        )
                        member_tmp = await download_to_temp(cfg, bucket, member_key)
                        total_bytes += member_tmp.stat().st_size
                        if total_bytes > MAX_MATERIALIZE_BYTES:
                            member_tmp.unlink(missing_ok=True)
                            raise ExternalStoreError("清单物化体积超过上限,无法加工")
                        local_name = f"{count:06d}-{Path(member_key).name}"
                        member_tmp.replace(tmpdir / local_name)
                        local_names.append(local_name)
                        count += 1
                    row[field] = local_names
                row.pop("__member", None)
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        yield out_path


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
    # 媒体批量接入(manifest):下载清单+各成员到同一临时目录,把 images/audios/videos
    # 路径改写为本地相对文件名(dj rel2abs 以 jsonl 所在目录为锚),剥掉 __member。
    if version.format == MANIFEST_FORMAT:
        async with _materialized_manifest(version, session) as mpath:
            yield mpath
        return

    # 按 storage_uri scheme 路由(不再凭 origin 二分):本地路径直接透传;s3:// 走对象存储。
    # 这样平台自有的 managed jsonl(单一格式批量上传,storage_uri=s3://adp-datasets/…)也能物化,
    # 而不破坏 hosted 外部 S3 / 平台零拷贝(均为 s3://)与本地受管(本地路径)的既有行为。
    if not str(version.storage_uri).startswith("s3://"):
        raw = Path(version.storage_uri)
        # 若含 U+FEFF BOM(原始 CSV 带 BOM、转 JSON 时粘到首列名上,如 "﻿title"),
        # DJ 不剥离 → text_key 对不上、load_dataset 报 'no key [text]'。仅此时写一份
        # 去 BOM 的临时文件供 DJ 读,用完清理;干净文件仍零 IO 透传。
        data = raw.read_bytes()
        if b"\xef\xbb\xbf" not in data:
            yield raw
            return
        tmp = Path(tempfile.mktemp(prefix="nobom-", suffix=".jsonl"))
        tmp.write_bytes(data.replace(b"\xef\xbb\xbf", b""))
        try:
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)
        return

    # parquet:dj ParquetFormatter 原生读 → 取字节落临时 .parquet,直接 yield 该路径
    if version.format == "parquet":
        cfg = await _version_cfg(version, session)
        bucket, key = parse_s3_uri(version.storage_uri)
        content = await cached_bytes(cfg, bucket, key)
        tmp = Path(tempfile.mktemp(prefix="adp-pq-", suffix=".parquet"))
        tmp.write_bytes(content)
        try:
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)
        return

    # 二进制 s3 版本无法规范化为 jsonl(加工/物化不适用)→ 明确报错,
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
    # 取源对象字节:命中物化缓存则免去重复下载(缓存自身的生命周期由 cached_bytes
    # 管理,这里不再清理原始文件;只清理本次派生的临时 jsonl)。
    content = await cached_bytes(cfg, bucket, key)
    jsonl_path: Path | None = None
    try:
        records = normalize_to_records(content, version.format)
        jsonl_path = await asyncio.to_thread(_write_jsonl_sync, records)
        yield jsonl_path
    finally:
        if jsonl_path is not None:
            jsonl_path.unlink(missing_ok=True)


async def persist_manifest_output(
    *, jsonl_path: Path, dataset_id: str, version_no: int
) -> tuple[str, int, int]:
    """把加工产出的 jsonl 持久化为平台 manifest 版本(``_materialized_manifest`` 的逆)。

    加工 manifest 数据集时,dj 产物里的 images/audios/videos 引用指向物化临时目录
    (用完即清)。本函数趁临时文件还在,把各媒体回传平台 MinIO
    (adp-datasets 桶 <dataset_id>/v<n>/...),路径改写为对象 key 并补回 __member,清单写到
    同前缀 manifest.jsonl。返回 (storage_uri, 行数, 媒体总字节)。

    图像算子改图后产出的是**新文件**,照样按其产物路径原样上传,故对未来的多模态加工
    (GPU 上跑图像算子)同样自洽——产物始终自包含,不回指输入对象。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    prefix = f"{dataset_id}/v{version_no}"
    lines = [
        ln
        for ln in jsonl_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    total_size = 0
    out_rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExternalStoreError(f"加工产物格式损坏:{exc}") from exc
        member: dict[str, Any] | None = None
        for field in ("images", "audios", "videos"):
            paths = row.get(field)
            if not isinstance(paths, list):
                continue
            # images/audios/videos 落盘存相对 manifest 前缀的文件名(与
            # _materialized_manifest 的写入约定对称),__member.key 才是完整对象 key。
            names: list[str] = []
            for local in paths:
                p = Path(str(local))
                if not p.is_absolute() or not p.exists():
                    raise ExternalStoreError(
                        f"加工产物媒体缺失,无法持久化:{local}"
                    )
                content = await asyncio.to_thread(p.read_bytes)
                key = f"{prefix}/{p.name}"
                await upload_object(
                    cfg, bucket, key, io.BytesIO(content), len(content)
                )
                total_size += len(content)
                names.append(p.name)
                if member is None:
                    member = {
                        "bucket": bucket,
                        "key": key,
                        "name": p.name,
                        "size": len(content),
                        "format": p.suffix.lstrip("."),
                    }
            row[field] = names
        if member is not None:
            row["__member"] = member
        out_rows.append(row)

    manifest_bytes = (
        "\n".join(
            json.dumps(r, ensure_ascii=False, default=str) for r in out_rows
        )
        + "\n"
    ).encode("utf-8")
    manifest_key = f"{prefix}/manifest.jsonl"
    await upload_object(
        cfg,
        bucket,
        manifest_key,
        io.BytesIO(manifest_bytes),
        len(manifest_bytes),
        content_type="application/x-ndjson",
    )
    return f"s3://{bucket}/{manifest_key}", len(out_rows), total_size


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


async def ensure_datasets_bucket() -> None:
    """启动时确保平台数据集桶存在(best-effort,由调用方吞异常)。

    - 平台 MinIO 未配置 → 直接跳过(文件管理端点仍各自返回 503)。
    - 已配置:桶不存在则创建,已存在则幂等跳过。
    创建桶是写操作但不涉删除,符合"绝不删源"红线。
    """
    cfg = platform_config()  # 未配置抛 ExternalStoreError,由调用方吞掉
    bucket = settings.storage_minio_datasets_bucket
    client = client_for(cfg)

    def _ensure() -> None:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    await asyncio.to_thread(_ensure)


async def ensure_lake_bucket() -> None:
    """启动时确保数据湖专用桶存在(best-effort,由调用方吞异常)。

    数据湖使用独立桶 `storage_minio_lake_bucket`(默认 adp-data-lake),与
    upload 桶物理隔离。见 docs/数据治理.md §2.1、data-lake.py 模块注释。
    未配置 MinIO → 直接跳过;已配置且桶不存在 → 创建;存在 → 幂等跳过。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_lake_bucket
    client = client_for(cfg)

    def _ensure() -> None:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    await asyncio.to_thread(_ensure)


async def upload_jsonl_to_datasets(
    dataset_id: str, version_no: int, jsonl_bytes: bytes
) -> str:
    """把 jsonl 字节上传到平台 MinIO 数据集桶,键 = ``<dataset_id>/v<n>/data.jsonl``。

    不同版本落不同文件夹(v1/v2/...),与 persist_manifest_output 同一前缀约定。
    返回 storage_uri(``s3://<bucket>/<key>``)。平台未配置 → ExternalStoreError。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    key = f"{dataset_id}/v{version_no}/data.jsonl"
    await upload_object(
        cfg, bucket, key, io.BytesIO(jsonl_bytes), len(jsonl_bytes),
        content_type="application/x-ndjson",
    )
    return f"s3://{bucket}/{key}"


async def upload_parquet_to_datasets(
    dataset_id: str, version_no: int, parquet_bytes: bytes
) -> str:
    """把 parquet 字节上传到平台 MinIO 数据集桶,
    键 = ``<dataset_id>/v<n>/data.parquet``。

    与 upload_jsonl_to_datasets 同前缀约定(不同版本落不同文件夹)。
    返回 storage_uri(``s3://<bucket>/<key>``)。平台未配置 → ExternalStoreError。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    key = f"{dataset_id}/v{version_no}/data.parquet"
    await upload_object(cfg, bucket, key, io.BytesIO(parquet_bytes), len(parquet_bytes))
    return f"s3://{bucket}/{key}"


async def upload_parquet_member(
    dataset_id: str, version_no: int, table_name: str, parquet_bytes: bytes
) -> str:
    """上传一个表成员 parquet,键 = ``<dataset_id>/v<n>/<table>.parquet``。"""
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    key = f"{dataset_id}/v{version_no}/{table_name}.parquet"
    await upload_object(
        cfg, bucket, key, io.BytesIO(parquet_bytes), len(parquet_bytes)
    )
    return f"s3://{bucket}/{key}"


async def upload_jsonl_member(
    dataset_id: str, version_no: int, table_name: str, jsonl_bytes: bytes
) -> str:
    """上传一个表成员 jsonl,键 = ``<dataset_id>/v<n>/<table>.jsonl``。"""
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    key = f"{dataset_id}/v{version_no}/{table_name}.jsonl"
    await upload_object(
        cfg, bucket, key, io.BytesIO(jsonl_bytes), len(jsonl_bytes),
        content_type="application/x-ndjson",
    )
    return f"s3://{bucket}/{key}"


async def copy_object_to_datasets(
    src_uri: str, dataset_id: str, version_no: int, table_name: str, fmt: str
) -> str:
    """平台 MinIO 内 server-side copy:把已有对象复制为新版本成员对象。

    供加工产新版本时全拷贝结转未处理成员:新版本物理持有全部成员文件、
    版本间零共享(删版本可整前缀删对象,不再依赖 storage_uri 引用计数)。
    复制在 MinIO 服务端完成,不经后端网络、不占本地磁盘。
    仅适用于平台 MinIO 内的源对象(单对象上限 5 GiB,与 MAX_MATERIALIZE_BYTES
    一致);外部 S3 / 本地路径来源由调用方下载后上传。
    返回新对象的 storage_uri。S3 错误抛 ExternalStoreError。
    """
    from minio.commonconfig import CopySource

    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    src_bucket, src_key = parse_s3_uri(src_uri)
    dst_key = f"{dataset_id}/v{version_no}/{table_name}.{fmt}"
    client = client_for(cfg)
    try:
        await asyncio.to_thread(
            client.copy_object, bucket, dst_key, CopySource(src_bucket, src_key)
        )
    except S3Error as exc:
        raise ExternalStoreError(
            f"复制对象失败 {src_bucket}/{src_key} → {bucket}/{dst_key}:{exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 连接类错误统一上报
        raise ExternalStoreError(
            f"复制对象失败 {src_bucket}/{src_key} → {bucket}/{dst_key}:{exc}"
        ) from exc
    return f"s3://{bucket}/{dst_key}"


async def upload_file_to_datasets(
    dataset_id: str, version_no: int, path: Path
) -> str:
    """把本地 jsonl 文件**流式**上传到平台 MinIO 数据集桶
    (键同 upload_jsonl_to_datasets)。

    供治理任务产出持久化:DJ 写本地文件后调此上传,storage_uri 指向 s3://,
    产出不在本地停留(读路径 preview/download/materialize 已按 s3:// scheme 走)。
    流式上传(不全量入内存),适合大体量产出。平台未配置 → ExternalStoreError。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    key = f"{dataset_id}/v{version_no}/data.jsonl"
    size = path.stat().st_size
    with path.open("rb") as f:
        await upload_object(
            cfg, bucket, key, f, size,
            content_type="application/x-ndjson",
        )
    return f"s3://{bucket}/{key}"


async def upload_parquet_file_to_datasets(
    dataset_id: str, version_no: int, path: Path
) -> str:
    """把本地 parquet 产物**流式**上传到平台 MinIO 数据集桶,
    键 = ``<id>/v<n>/data.parquet``。

    镜像 upload_file_to_datasets,但产物格式为 parquet。流式上传(不全量入内存),
    适合大体量产出。平台未配置 → ExternalStoreError。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_datasets_bucket
    key = f"{dataset_id}/v{version_no}/data.parquet"
    size = path.stat().st_size
    with path.open("rb") as f:
        await upload_object(cfg, bucket, key, f, size)
    return f"s3://{bucket}/{key}"


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
