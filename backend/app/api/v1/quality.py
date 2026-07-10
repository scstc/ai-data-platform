"""质量评估路由(#6):建质量任务、逐条 stats 查询、聚合质量报告。

- POST /quality/jobs:仅接受 filter 类算子,跑 dj-analyze 逐条算 stats
  (不删行、不产新版本),成功后回写输入版本的 stats_uri。
- GET /dataset-versions/{id}/stats:stats jsonl 与数据文件按行号对齐,分页。
- GET /dataset-versions/{id}/quality-report:数值型指标的纯 Python 聚合。
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
from itertools import islice
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from app.api.deps import current_user
from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _item,
    _new_job_id,
)
from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.job import Job
from app.models.user import User
from app.schemas.job import OperatorSpec, QualityJobCreate
from app.services import dataset_acl, job_runner
from app.services import operator_catalog as oc
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["quality"])

# DJ stats 列名(Fields.stats):stats jsonl 每行形如 {"__dj__stats__": {...}}
_DJ_STATS_KEY = "__dj__stats__"
_HIST_BUCKETS = 20
_NO_STATS_MSG = "该版本尚未进行质量评估"


def _validate_quality_operators(
    operators: list[OperatorSpec], *, llm_configured: bool
) -> str | None:
    """校验一组质量算子:存在 / filter 类 / 资源可执行。不合规返回错误消息。"""
    unknown = [o.name for o in operators if oc.get_operator(o.name) is None]
    if unknown:
        return f"未知算子:{', '.join(unknown)}"
    non_filter = [
        o.name for o in operators if oc.get_operator(o.name)["category"] != "filter"
    ]
    if non_filter:
        return f"质量评估仅支持 filter 类算子,以下算子不适用:{', '.join(non_filter)}"
    blocked = [
        reason
        for o in operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return "；".join(blocked)
    return None


@router.post("/quality/jobs")
async def create_quality_job(
    body: QualityJobCreate,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """新建质量评估任务并后台异步执行:对版本逐条算 filter stats,不产新版本。

    异步(同治理类任务):立即返回 pending,不阻塞请求;进度经轮询 GET 反映,
    可经 /jobs/{id}/stop|pause|resume 统一管控。stats 跑完回写输入版本/成员
    stats_uri,报告按输入版本查看。

    member_configs(成员级,多文件版本优先)与 operators(旧版统一配置,向后兼容)
    二选一,不可同时指定。
    """
    if body.member_configs and body.operators:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "不能同时指定 memberConfigs 和 operators",
            },
        )
    if not body.member_configs and not body.operators:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请指定 memberConfigs 或 operators"},
        )

    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    # 数据集 ACL:质量评估会回写输入版本 stats,要求 edit 及以上
    if not await dataset_acl.can_access(
        session, user, input_version.dataset_id, "edit"
    ):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无数据集编辑权限,无法发起评估"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp

    llm_configured = bool(get_active_llm_config().api_key)

    if body.member_configs:
        stmt = select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == body.dataset_version_id
        )
        members = (await session.execute(stmt)).scalars().all()
        member_names = {m.table_name for m in members}

        for cfg in body.member_configs:
            if cfg.member_name not in member_names:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员不存在:{cfg.member_name}",
                    },
                )
            if not cfg.operators:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员 {cfg.member_name} 请至少选择一个算子",
                    },
                )
            if err := _validate_quality_operators(
                cfg.operators, llm_configured=llm_configured
            ):
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员 {cfg.member_name}:{err}",
                    },
                )
    else:
        if err := _validate_quality_operators(
            body.operators, llm_configured=llm_configured
        ):
            return JSONResponse(
                status_code=400, content={"success": False, "message": err}
            )
        if body.target_members:
            stmt = select(DatasetVersionTable).where(
                DatasetVersionTable.dataset_version_id == body.dataset_version_id
            )
            members = (await session.execute(stmt)).scalars().all()
            if members:  # 有成员表记录时才校验(旧版单文件版本无成员表)
                member_names = {m.table_name for m in members}
                unknown_members = set(body.target_members) - member_names
                if unknown_members:
                    names = ", ".join(sorted(unknown_members))
                    return JSONResponse(
                        status_code=400,
                        content={
                            "success": False,
                            "message": f"成员不存在:{names}",
                        },
                    )

    job = Job(
        id=_new_job_id(),
        name=body.name,
        type="quality",
        state="pending",
        progress=0,
        created_by=user.username if user else "admin",
        # 存原始执行规格,供 job_runner 后台重建 body + 供重跑/继续
        spec=body.model_dump(mode="json"),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    # 交后台异步执行(与治理类任务同一执行路径 / 同一状态机)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


# ---------------------------------------------------------------------------
# 逐条 stats 与质量报告
# ---------------------------------------------------------------------------
def _unwrap_stats(line: str) -> dict[str, Any]:
    """解析 stats jsonl 一行,剥掉 DJ 的 __dj__stats__ 包装。"""
    rec = json.loads(line)
    stats = rec.get(_DJ_STATS_KEY)
    return stats if isinstance(stats, dict) else rec


def _safe_path(uri: str | None) -> Path | None:
    """校验 uri 落在受管数据目录内(DB 字段不可信),越界或为空返回 None。"""
    if not uri:
        return None
    path = Path(uri).resolve()
    root = Path(settings.datasets_dir).resolve()
    return path if path.is_relative_to(root) else None


def _window_texts(path: Path, offset: int, limit: int) -> dict[int, str]:
    """读数据文件窗口内各行的 text(截断 200 字符),按绝对行号索引。"""
    result: dict[int, str] = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as fp:
        lines = (ln for ln in fp if ln.strip())
        for i, line in enumerate(islice(lines, offset, offset + limit)):
            text = json.loads(line).get("text")
            result[offset + i] = "" if text is None else str(text)[:200]
    return result


async def _resolve_member(
    version_id: str, member: str | None, session: SessionDep
) -> tuple[DatasetVersion, DatasetVersionTable | None] | JSONResponse:
    """取版本 + 定位到具体成员(多文件场景下的一个表/文件)。

    版本无成员表记录(旧版单文件版本)→ 返回 (version, None),调用方读版本级字段。
    版本有成员表记录:
      - 传 member → 按 table_name 精确匹配,不存在 404。
      - 未传且只有一个成员 → 自动选中(单表数据集,行为等同旧版)。
      - 未传且有多个成员 → 400,要求前端显式指定(不猜)。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )
    stmt = select(DatasetVersionTable).where(
        DatasetVersionTable.dataset_version_id == version_id
    )
    members = (await session.execute(stmt)).scalars().all()
    if not members:
        return version, None
    if member is not None:
        found = next((m for m in members if m.table_name == member), None)
        if found is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": f"成员不存在:{member}"},
            )
        return version, found
    if len(members) == 1:
        return version, members[0]
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "message": "该版本含多个文件成员,请指定 member 参数",
        },
    )


async def _get_version_with_stats(
    version_id: str, session: SessionDep, member: str | None = None
) -> tuple[DatasetVersion, Path, Path | None] | JSONResponse:
    """取版本(+成员)并校验 stats 文件可用(且在受管目录内),失败直接给错误响应。

    返回 (version, stats_path, storage_path);storage_path 供 stats 端点对齐 text
    (成员级用 member.storage_uri,旧版单文件用 version.storage_uri)。
    """
    resolved = await _resolve_member(version_id, member, session)
    if isinstance(resolved, JSONResponse):
        return resolved
    version, member_row = resolved
    stats_uri = member_row.stats_uri if member_row is not None else version.stats_uri
    storage_uri = (
        member_row.storage_uri if member_row is not None else version.storage_uri
    )
    stats_path = _safe_path(stats_uri)
    if stats_path is None or not stats_path.exists():
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": _NO_STATS_MSG},
        )
    return version, stats_path, _safe_path(storage_uri)


def _analysis_dir(stats_uri: str | None) -> Path | None:
    """从 stats_uri 推导 dj-analyze 产出的 analysis 目录,并校验落在受管数据目录内。

    成员级 stats_uri 形如 <ds>/quality/<job_id>/<job_id>-<table>/<table>_stats.jsonl
    (历史任务曾落 <ds>/v<n>/<job_id>-<table>/ 下,同样兼容);旧版单文件形如
    <ds>/quality/<job_id>/data_stats.jsonl。各情形 analysis 均为其同级 analysis/
    (dj-analyze 写 overall.csv + PNG 到此,见 services/quality.py 的 work_dir 约定)。
    """
    stats_path = _safe_path(stats_uri)
    if stats_path is None:
        return None
    analysis = (stats_path.parent / "analysis").resolve()
    root = Path(settings.datasets_dir).resolve()
    return analysis if analysis.is_relative_to(root) else None


@router.get("/dataset-versions/{version_id}/analysis-report")
async def analysis_report(
    version_id: str,
    session: SessionDep,
    member: Annotated[str | None, Query()] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """dj-analyze 分析报告:overall.csv(跨算子聚合统计表)+ analysis/ 下 PNG 清单。

    无 analysis/(任务未完成或未产出)→ data=None + 提示,前端回退到手算聚合。
    多文件版本未传 member 且有多个成员 → 400(与 stats 端点一致,不猜)。
    """
    resolved = await _resolve_member(version_id, member, session)
    if isinstance(resolved, JSONResponse):
        return resolved
    version, member_row = resolved
    if not await dataset_acl.can_access(session, user, version.dataset_id, "view"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无数据集查看权限"}
        )
    stats_uri = member_row.stats_uri if member_row is not None else version.stats_uri
    analysis = _analysis_dir(stats_uri)
    if analysis is None or not analysis.exists():
        return JSONResponse(
            content={
                "data": None,
                "success": True,
                "message": "该版本无 dj-analyze 分析报告",
            }
        )
    overall: dict[str, Any] | None = None
    overall_csv = analysis / "overall.csv"
    if overall_csv.exists():
        # utf-8-sig 兼容 pandas to_csv 可能带的 BOM
        with overall_csv.open(encoding="utf-8-sig") as fp:
            rows = [r for r in csv.reader(fp)]
        if rows:
            overall = {"columns": rows[0], "rows": rows[1:]}
    images: list[dict[str, str]] = []
    for p in sorted(analysis.glob("*.png")):
        name = p.name
        if name.startswith("stats-corr"):
            kind = "correlation"
        elif (
            name.startswith("all-stats")
            or name.endswith("-hist.png")
            or name.endswith("-box.png")
            or name.endswith("-wordcloud.png")
        ):
            kind = "distributions"
        else:
            kind = "other"
        images.append({"name": name, "kind": kind})
    return JSONResponse(
        content={"data": {"overall": overall, "images": images}, "success": True}
    )


@router.get("/dataset-versions/{version_id}/analysis-image", response_model=None)
async def analysis_image(
    version_id: str,
    session: SessionDep,
    name: Annotated[str, Query()],
    member: Annotated[str | None, Query()] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse | FileResponse:
    """取 analysis/ 下某张 PNG(供前端 <img> 直接嵌入)。"""
    resolved = await _resolve_member(version_id, member, session)
    if isinstance(resolved, JSONResponse):
        return resolved
    version, member_row = resolved
    if not await dataset_acl.can_access(session, user, version.dataset_id, "view"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无数据集查看权限"}
        )
    stats_uri = member_row.stats_uri if member_row is not None else version.stats_uri
    analysis = _analysis_dir(stats_uri)
    if analysis is None or not analysis.exists():
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "无分析报告"},
        )
    # 防 path traversal:name 仅允许纯文件名
    if "/" in name or "\\" in name or ".." in name:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "非法文件名"},
        )
    img = (analysis / name).resolve()
    if not img.is_relative_to(analysis) or not img.exists():
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "图片不存在"},
        )
    return FileResponse(str(img), media_type="image/png")


def _scan_stats(
    stats_path: Path, storage_path: Path | None, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """同步扫 stats jsonl(供 to_thread):窗口行 + 总数 + 指标名集合。"""
    texts = (
        _window_texts(storage_path, offset, limit) if storage_path else {}
    )
    items: list[dict[str, Any]] = []
    metrics: set[str] = set()
    total = 0
    with stats_path.open(encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            stats = _unwrap_stats(line)
            metrics.update(stats)
            if offset <= total < offset + limit:
                items.append(
                    {
                        "index": total,
                        "text": texts.get(total, ""),
                        "stats": stats,
                    }
                )
            total += 1
    return items, total, sorted(metrics)


@router.get("/dataset-versions/{version_id}/stats")
async def version_stats(
    version_id: str,
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    member: Annotated[str | None, Query()] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """逐条质量得分:stats jsonl 与数据文件按行号对齐,分页返回。

    多文件版本需传 member 指定成员(表/文件);单成员或旧版单文件版本可省略。
    """
    found = await _get_version_with_stats(version_id, session, member)
    if isinstance(found, JSONResponse):
        return found
    _version, stats_path, storage_path = found
    if not await dataset_acl.can_access(session, user, _version.dataset_id, "view"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无数据集查看权限"}
        )

    offset = (current - 1) * page_size
    # 文件扫描放线程池,避免阻塞事件循环
    items, total, metrics = await asyncio.to_thread(
        _scan_stats,
        stats_path,
        storage_path,
        offset,
        page_size,
    )
    return JSONResponse(
        content={
            "data": items,
            "total": total,
            "metrics": metrics,
            "success": True,
        }
    )


def _histogram(vals: list[float], lo: float, hi: float) -> list[dict]:
    """等宽 20 桶直方图;所有值相同(宽度 0)时退化为单桶。"""
    if hi == lo:
        return [{"x0": lo, "x1": hi, "count": len(vals)}]
    width = (hi - lo) / _HIST_BUCKETS
    counts = [0] * _HIST_BUCKETS
    for v in vals:
        counts[min(int((v - lo) / width), _HIST_BUCKETS - 1)] += 1
    return [
        {"x0": lo + i * width, "x1": lo + (i + 1) * width, "count": counts[i]}
        for i in range(_HIST_BUCKETS)
    ]


def _aggregate(name: str, vals: list[float]) -> dict[str, Any]:
    """单指标聚合:计数 / 均值 / 极值 / 四分位 + 直方图。"""
    vals.sort()
    lo, hi = vals[0], vals[-1]
    if len(vals) >= 2:
        p25, p50, p75 = statistics.quantiles(vals, n=4, method="inclusive")
    else:
        p25 = p50 = p75 = lo
    return {
        "name": name,
        "count": len(vals),
        "mean": statistics.fmean(vals),
        "min": lo,
        "max": hi,
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "histogram": _histogram(vals, lo, hi),
    }


def _scan_report(stats_path: Path) -> dict[str, Any]:
    """同步扫描 + 聚合(供 to_thread):全量数值指标的分布统计。"""
    rows = 0
    values: dict[str, list[float]] = {}
    with stats_path.open(encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            rows += 1
            for key, val in _unwrap_stats(line).items():
                if isinstance(val, bool) or not isinstance(val, int | float):
                    continue
                values.setdefault(key, []).append(float(val))
    metrics = [_aggregate(name, vals) for name, vals in sorted(values.items())]
    return {"rows": rows, "metrics": metrics}


@router.get("/dataset-versions/{version_id}/quality-report")
async def quality_report(
    version_id: str,
    session: SessionDep,
    member: Annotated[str | None, Query()] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """质量报告:对 stats jsonl 的数值型指标做分布聚合(列表/字符串跳过)。

    多文件版本需传 member 指定成员(表/文件);单成员或旧版单文件版本可省略。
    """
    found = await _get_version_with_stats(version_id, session, member)
    if isinstance(found, JSONResponse):
        return found
    _version, stats_path, _storage_path = found
    if not await dataset_acl.can_access(session, user, _version.dataset_id, "view"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无数据集查看权限"}
        )

    # 全量扫描 + 聚合放线程池,避免阻塞事件循环
    data = await asyncio.to_thread(_scan_report, stats_path)
    return JSONResponse(content={"data": data, "success": True})


@router.get("/dataset-versions/{version_id}/quality-members")
async def quality_members(
    version_id: str,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """该版本各成员(表/文件)是否已做过质量评估,供前端渲染成员切换 Tab。

    无成员表记录(旧版单文件版本)时,合成单一元素("data",按版本级 stats_uri
    判断),让前端不必特判"是否多文件"。版本不存在 → 404。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )
    if not await dataset_acl.can_access(session, user, version.dataset_id, "view"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无数据集查看权限"}
        )
    stmt = (
        select(DatasetVersionTable)
        .where(DatasetVersionTable.dataset_version_id == version_id)
        .order_by(DatasetVersionTable.table_name)
    )
    members = (await session.execute(stmt)).scalars().all()
    if not members:
        data = [{"memberName": "data", "hasStats": bool(version.stats_uri)}]
    else:
        data = [
            {"memberName": m.table_name, "hasStats": bool(m.stats_uri)}
            for m in members
        ]
    return JSONResponse(content={"data": data, "success": True})
