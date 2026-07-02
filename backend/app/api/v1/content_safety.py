"""内容安全审核路由(#4)。

- POST /content-safety/jobs:对一个数据集版本建 review job + 同步跑 run_review
  (沿用 jobs.py / quality.py 的 create→run→回写 状态机),产出打标版本。
  不加 require_admin(审核属分析类;POST 经 #5 审计中间件留痕)。
- GET  /content-safety/jobs:分页列 type=review job。
- GET  /content-safety/jobs/{id}/report:job 状态 + review_report + 打标版本 id。
- GET  /content-safety/jobs/{id}/findings:分页 review_findings,过滤
  category/source/severity/tableName,按 row_index 升序。
- /content-safety/rules:自定义规则库 CRUD(敏感词/正则,带类别与严重度);
  建任务按 ruleIds 冻结进 spec,上传前置预检自动合并全部启用项。

设计见 docs/plan/07-内容安全设计.md §3.3。
"""

from __future__ import annotations

import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import require_perm
from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _build_input,
    _build_output,
    _dataset_job_filter,
    _item,
    _new_job_id,
)
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.review_finding import ReviewFinding
from app.models.review_rule import ReviewRule
from app.schemas.common import PageResponse
from app.schemas.job import JobRead
from app.schemas.review import (
    ReviewFindingRead,
    ReviewJobCreate,
    ReviewRuleCreate,
    ReviewRuleRead,
    ReviewRuleUpdate,
    RuleRegexSpec,
    RuleWordSpec,
)
from app.services import job_runner
from app.services.review import rules_to_config

router = APIRouter(tags=["content-safety"])


def _new_rule_id() -> str:
    return f"rr-{secrets.token_hex(3)}"


@router.post("/content-safety/jobs")
async def create_review_job(
    body: ReviewJobCreate, session: SessionDep
) -> JSONResponse:
    """新建内容审核任务并后台异步执行:扫描版本 → 落命中 → 产出打标版本 → 回写报告。

    异步(同治理类任务):立即返回 pending,不阻塞请求;可经 /jobs/{id}/stop|pause|resume
    统一管控。打标版本与报告在后台跑完后产出。
    """
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp

    # 规则库条目按 ruleIds 解析并冻结进 config(重跑/继续复用冻结值,
    # 不受规则库后续增删影响);未启用/不存在的 id 静默忽略
    if body.rule_ids:
        rules = (
            await session.scalars(
                select(ReviewRule).where(
                    ReviewRule.id.in_(body.rule_ids), ReviewRule.enabled
                )
            )
        ).all()
        rule_words, rule_regex = rules_to_config(list(rules))
        body.config.rule_words = [RuleWordSpec(**w) for w in rule_words]
        body.config.rule_regex = [RuleRegexSpec(**r) for r in rule_regex]

    job = Job(
        id=_new_job_id(),
        name=body.name or "内容安全审核",
        type="review",
        state="pending",
        progress=0,
        created_by="admin",
        # 存原始执行规格,供 job_runner 后台重建 body(config)+ 供重跑/继续
        spec=body.model_dump(mode="json"),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    # 交后台异步执行(与治理类任务同一执行路径 / 同一状态机)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.get(
    "/content-safety/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:contentsafety:list"))],
)
async def list_review_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
) -> PageResponse[JobRead]:
    """分页列出 type=review 任务,按创建时间倒序(带输入版本概要);
    可按 datasetId 过滤(输入或产物版本属于该数据集)。"""
    count_stmt = select(func.count()).select_from(Job).where(Job.type == "review")
    list_stmt = select(Job).where(Job.type == "review")
    if dataset_id:
        count_stmt = count_stmt.where(_dataset_job_filter(dataset_id))
        list_stmt = list_stmt.where(_dataset_job_filter(dataset_id))
    total = (await session.scalar(count_stmt)) or 0
    rows = (
        await session.scalars(
            list_stmt.order_by(Job.created_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data: list[JobRead] = []
    for r in rows:
        read = JobRead.model_validate(r)
        read.input = await _build_input(session, r.id)
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)


@router.get("/content-safety/jobs/{job_id}/report")
async def review_report(job_id: str, session: SessionDep) -> JSONResponse:
    """审核报告:job 状态 + review_report + 打标版本 id。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != "review":
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "审核任务不存在"},
        )
    output = await _build_output(session, job.id)
    return JSONResponse(
        content={
            "data": {
                "jobId": job.id,
                "name": job.name,
                "state": job.state,
                "error": job.error,
                "reviewReport": job.review_report,
                "taggedVersionId": output["versionId"] if output else None,
            },
            "success": True,
        }
    )


@router.get(
    "/content-safety/jobs/{job_id}/findings",
    response_model=PageResponse[ReviewFindingRead],
)
async def list_findings(
    job_id: str,
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    category: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    table_name: Annotated[str | None, Query(alias="tableName")] = None,
) -> PageResponse[ReviewFindingRead]:
    """分页列出某审核任务的命中,可按 category/source/severity/tableName 过滤,
    按(表名, 行号)升序。"""
    conds = [ReviewFinding.job_id == job_id]
    if category:
        conds.append(ReviewFinding.category == category)
    if source:
        conds.append(ReviewFinding.source == source)
    if severity:
        conds.append(ReviewFinding.severity == severity)
    if table_name:
        conds.append(ReviewFinding.table_name == table_name)

    total = (
        await session.scalar(
            select(func.count()).select_from(ReviewFinding).where(*conds)
        )
    ) or 0
    rows = (
        await session.scalars(
            select(ReviewFinding)
            .where(*conds)
            .order_by(
                ReviewFinding.table_name.asc().nulls_first(),
                ReviewFinding.row_index.asc(),
            )
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data = [ReviewFindingRead.model_validate(r) for r in rows]
    return PageResponse[ReviewFindingRead](data=data, total=total)


# ── 规则库 CRUD ──────────────────────────────────────────────────────────────


@router.get(
    "/content-safety/rules",
    response_model=PageResponse[ReviewRuleRead],
    dependencies=[Depends(require_perm("governance:contentsafety:list"))],
)
async def list_review_rules(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 20,
    kind: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
) -> PageResponse[ReviewRuleRead]:
    """分页列出规则库条目,可按 kind/enabled/关键词(名称或 pattern)过滤。"""
    conds = []
    if kind:
        conds.append(ReviewRule.kind == kind)
    if enabled is not None:
        conds.append(ReviewRule.enabled == enabled)
    if keyword:
        like = f"%{keyword}%"
        conds.append(ReviewRule.name.ilike(like) | ReviewRule.pattern.ilike(like))
    total = (
        await session.scalar(select(func.count()).select_from(ReviewRule).where(*conds))
    ) or 0
    rows = (
        await session.scalars(
            select(ReviewRule)
            .where(*conds)
            .order_by(ReviewRule.created_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data = [ReviewRuleRead.model_validate(r) for r in rows]
    return PageResponse[ReviewRuleRead](data=data, total=total)


def _bad_regex(kind: str, pattern: str) -> str | None:
    """kind=regex 时试编译 pattern,坏正则返回错误信息(建/改规则时前置校验)。"""
    if kind != "regex":
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"正则无效:{exc}"
    return None


@router.post("/content-safety/rules")
async def create_review_rule(
    body: ReviewRuleCreate, session: SessionDep
) -> JSONResponse:
    """新建规则库条目(POST 经审计中间件留痕)。regex 规则坏 pattern 直接 400。"""
    if (msg := _bad_regex(body.kind, body.pattern)) is not None:
        return JSONResponse(status_code=400, content={"success": False, "message": msg})
    rule = ReviewRule(
        id=_new_rule_id(),
        name=body.name,
        kind=body.kind,
        pattern=body.pattern,
        category=body.category,
        severity=body.severity,
        enabled=body.enabled,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return JSONResponse(
        content={
            "data": ReviewRuleRead.model_validate(rule).model_dump(
                by_alias=True, mode="json"
            ),
            "success": True,
        }
    )


@router.patch("/content-safety/rules/{rule_id}")
async def update_review_rule(
    rule_id: str, body: ReviewRuleUpdate, session: SessionDep
) -> JSONResponse:
    """更新规则库条目(只改给出的字段;含启用/停用)。"""
    rule = await session.get(ReviewRule, rule_id)
    if rule is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "规则不存在"}
        )
    patch = body.model_dump(exclude_unset=True)
    kind = patch.get("kind", rule.kind)
    pattern = patch.get("pattern", rule.pattern)
    if (msg := _bad_regex(kind, pattern)) is not None:
        return JSONResponse(status_code=400, content={"success": False, "message": msg})
    for field, value in patch.items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    return JSONResponse(
        content={
            "data": ReviewRuleRead.model_validate(rule).model_dump(
                by_alias=True, mode="json"
            ),
            "success": True,
        }
    )


@router.delete("/content-safety/rules/{rule_id}")
async def delete_review_rule(rule_id: str, session: SessionDep) -> JSONResponse:
    """删除规则库条目。已建任务不受影响(规则在建任务时已冻结进 spec)。"""
    rule = await session.get(ReviewRule, rule_id)
    if rule is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "规则不存在"}
        )
    await session.delete(rule)
    await session.commit()
    return JSONResponse(content={"success": True})
