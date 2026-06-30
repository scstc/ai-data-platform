"""HDFS 连接器(数据接入重构 §4.6)。

走 WebHDFS REST 协议,无需本地 HDFS 客户端/驱动。

可测性层次:
- ``_build_webhdfs_url`` / ``_parse_liststatus`` —— **纯函数,可纯单测**,无需集群。
- ``probe`` / ``list_tables`` / ``run_ingest`` —— 端到端承诺级,需真实 HDFS 集群验证。

无集群时 ``probe`` 返回 ``(False, 0, 明确文案)``(ConnectorNotReady 语义),
``list_tables`` / ``run_ingest`` 抛 ``ConnectorNotReady``。诚实失败,绝不伪造 success。
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

import httpx

from app.services.connectors.base import ConnectorNotReady
from app.services.landing import normalize_to_records

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion
    from app.models.datasource import DataSource
    from app.models.ingest_task import IngestTask

logger = logging.getLogger(__name__)


# WebHDFS 默认端口(namenode HTTP)
_DEFAULT_PORT = 50070

# 每次 HTTP 请求超时(秒)
_HTTP_TIMEOUT = 30.0

_NOT_READY_MSG = (
    "HDFS 连接器结构就绪,当前无可达 NameNode,暂不可真连;"
    "端到端拉取需 HDFS 集群就绪(承诺级)"
)


# ---------------------------------------------------------------------------
# 纯函数(可纯单测,无网络依赖)
# ---------------------------------------------------------------------------


def _build_webhdfs_url(
    namenode: str,
    path: str,
    op: str,
    **params: Any,
) -> str:
    """构造 WebHDFS REST URL。

    Parameters
    ----------
    namenode:
        NameNode 地址,形如 ``"host"``、``"host:port"``、``"http://host:port"``。
        无协议前缀时自动补 ``http://``。无端口时补默认端口 50070。
    path:
        HDFS 路径,形如 ``"/user/data"``(若不以 ``/`` 开头则自动补)。
    op:
        WebHDFS operation,如 ``"LISTSTATUS"``、``"OPEN"``、``"GETFILESTATUS"``。
    **params:
        追加到查询串的额外参数(如 ``offset=0``、``length=4096``、
        ``noredirect=true``)。

    Returns
    -------
    str
        完整 WebHDFS URL,如
        ``http://nn:50070/webhdfs/v1/user/data?op=LISTSTATUS``。
    """
    # ── 1. 规范化 namenode ──────────────────────────────────────────────────
    nn = namenode.strip()
    if not nn.startswith(("http://", "https://")):
        nn = "http://" + nn

    parsed = urllib.parse.urlparse(nn)
    host = parsed.hostname or ""
    port = parsed.port or _DEFAULT_PORT
    scheme = parsed.scheme or "http"
    base = f"{scheme}://{host}:{port}"

    # ── 2. 规范化 path ──────────────────────────────────────────────────────
    clean_path = path.strip()
    if not clean_path.startswith("/"):
        clean_path = "/" + clean_path

    # ── 3. 拼 WebHDFS 路径 + 查询串 ────────────────────────────────────────
    webhdfs_path = f"/webhdfs/v1{clean_path}"
    query: dict[str, str] = {"op": op.upper()}
    query.update({k: str(v) for k, v in params.items()})

    return f"{base}{webhdfs_path}?{urllib.parse.urlencode(query)}"


def _parse_liststatus(json_body: dict) -> list[dict]:
    """解析 WebHDFS ``LISTSTATUS`` 响应,返回文件/目录条目列表。

    Parameters
    ----------
    json_body:
        已反序列化的 JSON,形如::

            {
                "FileStatuses": {
                    "FileStatus": [
                        {"pathSuffix": "foo.jsonl", "type": "FILE", "length": 1234},
                        ...
                    ]
                }
            }

    Returns
    -------
    list[dict]
        每项含 ``{name: str, type: str, length: int}``。
        ``type`` 原样保留 WebHDFS 值(``"FILE"`` 或 ``"DIRECTORY"``)。
        若响应结构不符预期则返回空列表,不抛异常(诚实降级)。
    """
    try:
        statuses = json_body["FileStatuses"]["FileStatus"]
    except (KeyError, TypeError):
        return []

    result: list[dict] = []
    for fs in statuses:
        if not isinstance(fs, dict):
            continue
        result.append(
            {
                "name": fs.get("pathSuffix", ""),
                "type": fs.get("type", "FILE"),
                "length": int(fs.get("length", 0)),
            }
        )
    return result


# ---------------------------------------------------------------------------
# HdfsConnector — 实现 base.Connector 协议
# ---------------------------------------------------------------------------


class HdfsConnector:
    """HDFS WebHDFS REST 连接器。

    ``config`` 字段约定(JSONB,由前端/API 传入)::

        {
            "namenode": "hdfs-nn:50070",   # 必填
            "path":     "/user/data",      # 可选,列目录/采集根路径(默认 "/")
            "user":     "hdfs",            # 可选,WebHDFS user.name 参数
        }

    probe: 向 NameNode 发 GETFILESTATUS ``/``(根目录),验证可达性。
    list_tables: LISTSTATUS 指定 path,返回文件/目录条目 list[dict]。
    run_ingest: 按 extract.paths 或 extract.glob 下载文件 → normalize_to_records
                → land_records(端到端承诺级,需真实集群)。
    """

    # ── 内部辅助 ────────────────────────────────────────────────────────────

    def _namenode(self, config: dict) -> str:
        nn = (config.get("namenode") or "").strip()
        if not nn:
            raise ConnectorNotReady(
                "HDFS 配置缺少 namenode 地址,无法连接"
            )
        return nn

    def _path(self, config: dict) -> str:
        return (config.get("path") or "/").strip() or "/"

    def _extra_params(self, config: dict) -> dict[str, str]:
        """附加到每个 WebHDFS 请求的通用参数(如 user.name)。"""
        params: dict[str, str] = {}
        user = (config.get("user") or "").strip()
        if user:
            params["user.name"] = user
        return params

    async def _get(self, url: str) -> httpx.Response:
        """发起 GET 请求,超时返回 ConnectorNotReady,保持 not-ready 语义。"""
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT, follow_redirects=True
            ) as client:
                return await client.get(url)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ConnectorNotReady(
                f"HDFS NameNode 不可达:{exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorNotReady(
                f"HDFS WebHDFS 请求失败:{exc}"
            ) from exc

    # ── Connector 协议实现 ───────────────────────────────────────────────────

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        """探测 NameNode 可达性。

        向根目录发 ``GETFILESTATUS`` 请求。成功返回 ``(True, 耗时ms, 文案)``,
        失败(超时/拒连/HTTP 非 200)返回 ``(False, 耗时ms, 明确错误文案)``。
        """
        try:
            nn = self._namenode(config)
        except ConnectorNotReady as exc:
            return (False, 0, str(exc))

        extra = self._extra_params(config)
        url = _build_webhdfs_url(nn, "/", "GETFILESTATUS", **extra)

        t0 = time.monotonic()
        try:
            resp = await self._get(url)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        except ConnectorNotReady as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return (False, elapsed_ms, str(exc))

        if resp.status_code == 200:
            return (
                True,
                elapsed_ms,
                f"HDFS NameNode 可达({nn}),探测耗时 {elapsed_ms}ms",
            )

        return (
            False,
            elapsed_ms,
            f"HDFS NameNode 返回 HTTP {resp.status_code},探测失败({nn})",
        )

    async def list_tables(self, config: dict) -> list:
        """列出指定路径下的文件/目录,返回 ``list[dict]``。

        每项 ``{name, type, length}``(type: "FILE"|"DIRECTORY")。
        NameNode 不可达 → 抛 ``ConnectorNotReady``。
        HTTP 非 200 → 抛 ``ConnectorNotReady``(含状态码和路径)。
        """
        nn = self._namenode(config)
        path = self._path(config)
        extra = self._extra_params(config)

        url = _build_webhdfs_url(nn, path, "LISTSTATUS", **extra)
        resp = await self._get(url)

        if resp.status_code != 200:
            raise ConnectorNotReady(
                f"HDFS LISTSTATUS 失败:HTTP {resp.status_code},路径 {path!r},"
                f" NameNode {nn}"
            )

        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ConnectorNotReady(
                f"HDFS LISTSTATUS 响应无法解析为 JSON:{exc}"
            ) from exc

        return _parse_liststatus(body)

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """从 HDFS 下载文件并落地。

        读 ``task.extract`` 中的 ``paths``(list[str])字段,每个路径走
        WebHDFS ``OPEN`` 下载 → ``normalize_to_records`` → ``land_records``。

        端到端承诺级:需真实 HDFS 集群。
        无 paths 配置 / NameNode 不可达 → 抛 ``ConnectorNotReady``。

        切片 C / Task 5 + C5 评审修复:增量过滤(仅支持 ``incremental.by='name'``,因为
        当前 run_ingest 不走 LISTSTATUS,没有 mtime)。``by='mtime'`` 在 HDFS 上
        **显式 warn**(不静默降级,Rule 12)→ 本次全量采,建议改用 ``by=name``。
        水位推进改为每路径 land_records **成功之后** running-max(C5 Finding 1);
        中途失败水位只反映此前已落地路径,失败/后续路径重试时仍被采(防 DATA LOSS)。
        """
        config = datasource.config or {}
        nn = self._namenode(config)
        extra = self._extra_params(config)

        from app.models.dataset import Dataset  # noqa: PLC0415
        from app.services.landing import add_table_member  # noqa: PLC0415

        extract: dict = task.extract or {}
        paths: list[str] = [
            p.strip() for p in (extract.get("paths") or []) if str(p).strip()
        ]
        if not paths:
            raise ConnectorNotReady(
                "HDFS 采集未配置 extract.paths,无法拉取文件(端到端承诺级)"
            )

        # 增量过滤:HDFS 当前 run_ingest 不获取 mtime,仅支持 by=name。
        incremental = getattr(task, "incremental", None) or {}
        watermark = getattr(task, "watermark", None) or {}
        by = incremental.get("by")
        if by == "mtime":
            # HDFS run_ingest 不走 LISTSTATUS,拿不到 mtime → 无法按 mtime 增量。
            # 不静默降级(Rule 12):显式 warn + 本次全量采,建议改用 by=name。
            logger.warning(
                "HDFS 增量采集 by=mtime 不支持(当前 run_ingest 不经 LISTSTATUS "
                "获取 mtime),本次降级为全量拉取;建议改用 by=name 按路径字典序增量"
            )
        if by == "name":
            wm_value = watermark.get("value")
            if wm_value is not None:
                paths = [p for p in paths if p > wm_value]

        version: DatasetVersion | None = None

        for hdfs_path in paths:
            url = _build_webhdfs_url(nn, hdfs_path, "OPEN", **extra)
            resp = await self._get(url)

            if resp.status_code != 200:
                raise ConnectorNotReady(
                    f"HDFS OPEN 失败:HTTP {resp.status_code},路径 {hdfs_path!r}"
                )

            content: bytes = resp.content

            # 从路径推断格式(取最后一段扩展名)
            filename = hdfs_path.rstrip("/").rsplit("/", 1)[-1]
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

            records = normalize_to_records(content, ext)

            # 数据集优先(Task 10):每个文件作成员落进 task.dataset_id 的 draft
            # 版本;成员名 = 文件名(去路径),空则兜底 "data"。
            table_name = filename or "data"
            version, _member = await add_table_member(
                session,
                task.dataset_id,
                records,
                table_name=table_name,
                semantic_type=config.get("semantic_type"),
                # 三轴:来源=HDFS;格式=拉取对象原始扩展名
                source_format=ext,
                note=f"HDFS 采集落地:{hdfs_path}(job={job_id})",
                produced_by_job_id=job_id,
            )

            # C5 评审 Finding 1 修复:水位推进改为每路径成功落地**之后**
            # running-max(当前水位, 本路径名)。中途失败 → 水位只反映此前已成功
            # 落地的路径,失败及后续路径在重试时仍被采(防 DATA LOSS)。
            if by == "name":
                from datetime import UTC, datetime  # noqa: PLC0415

                current_wm = getattr(task, "watermark", None) or {}
                current_value = current_wm.get("value")
                merged = (
                    hdfs_path
                    if current_value is None
                    else max(current_value, hdfs_path)
                )
                task.watermark = {
                    "value": merged,
                    "updatedAt": datetime.now(UTC).isoformat(),
                }

        if version is None:
            return []
        dataset = await session.get(Dataset, task.dataset_id)
        return [(dataset, version)]
