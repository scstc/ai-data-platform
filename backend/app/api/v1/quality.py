"""质量评估路由(#6):建质量任务、逐条 stats 查询、聚合质量报告。

- POST /quality/jobs:仅接受 filter 类算子,跑 dj-analyze 逐条算 stats
  (不删行、不产新版本),成功后回写输入版本的 stats_uri。
- GET /dataset-versions/{id}/stats:stats jsonl 与数据文件按行号对齐,分页。
- GET /dataset-versions/{id}/quality-report:数值型指标的纯 Python 聚合。
"""

from __future__ import annotations

import asyncio
import json
import statistics
from itertools import islice
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _build_input,
    _item,
    _new_job_id,
    _now,
)
from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.schemas.job import QualityJobCreate
from app.services import operator_catalog as oc
from app.services.quality import QualityError, run_quality_job

router = APIRouter(tags=["quality"])

# DJ stats 列名(Fields.stats):stats jsonl 每行形如 {"__dj__stats__": {...}}
_DJ_STATS_KEY = "__dj__stats__"
_HIST_BUCKETS = 20
_NO_STATS_MSG = "该版本尚未进行质量评估"


@router.post("/quality/jobs")
async def create_quality_job(
    body: QualityJobCreate, session: SessionDep
) -> JSONResponse:
    """新建并执行质量评估任务:对版本逐条算 filter stats,不产新版本。"""
    if not body.operators:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个算子"},
        )
    unknown = [o.name for o in body.operators if oc.get_operator(o.name) is None]
    if unknown:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"未知算子:{', '.join(unknown)}"},
        )
    non_filter = [
        o.name
        for o in body.operators
        if oc.get_operator(o.name)["category"] != "filter"
    ]
    if non_filter:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "质量评估仅支持 filter 类算子,"
                f"以下算子不适用:{', '.join(non_filter)}",
            },
        )
    # 资源前置校验:跑不了的直接拦截并给原因(同 jobs.py)
    llm_configured = bool(settings.openai_api_key)
    blocked = [
        reason
        for o in body.operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "；".join(blocked)},
        )

    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp

    job = Job(
        id=_new_job_id(),
        name=body.name,
        type="quality",
        state="running",
        progress=0,
        created_by="admin",
        started_at=_now(),
    )
    session.add(job)
    await session.commit()

    try:
        yaml_text, log_path = await run_quality_job(
            session,
            job_id=job.id,
            input_version=input_version,
            operators=[o.model_dump() for o in body.operators],
        )
        job.state = "success"
        job.progress = 100
        job.config_yaml = yaml_text
        job.logs_uri = log_path
    except QualityError as exc:
        job.state = "failed"
        job.error = str(exc)
    except Exception as exc:  # 未预期异常也不能让 job 卡死在 running
        job.state = "failed"
        job.error = f"未预期错误:{exc}"
    job.finished_at = _now()
    await session.commit()
    await session.refresh(job)

    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, input_=input_))


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


async def _get_version_with_stats(
    version_id: str, session: SessionDep
) -> tuple[DatasetVersion, Path] | JSONResponse:
    """取版本并校验 stats 文件可用(且在受管目录内),失败直接给 404 响应。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )
    stats_path = _safe_path(version.stats_uri)
    if stats_path is None or not stats_path.exists():
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": _NO_STATS_MSG},
        )
    return version, stats_path


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
) -> JSONResponse:
    """逐条质量得分:stats jsonl 与数据文件按行号对齐,分页返回。"""
    found = await _get_version_with_stats(version_id, session)
    if isinstance(found, JSONResponse):
        return found
    version, stats_path = found

    offset = (current - 1) * page_size
    # 文件扫描放线程池,避免阻塞事件循环
    items, total, metrics = await asyncio.to_thread(
        _scan_stats,
        stats_path,
        _safe_path(version.storage_uri),
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
async def quality_report(version_id: str, session: SessionDep) -> JSONResponse:
    """质量报告:对 stats jsonl 的数值型指标做分布聚合(列表/字符串跳过)。"""
    found = await _get_version_with_stats(version_id, session)
    if isinstance(found, JSONResponse):
        return found
    _version, stats_path = found

    # 全量扫描 + 聚合放线程池,避免阻塞事件循环
    data = await asyncio.to_thread(_scan_report, stats_path)
    return JSONResponse(content={"data": data, "success": True})
