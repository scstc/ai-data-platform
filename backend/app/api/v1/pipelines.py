"""治理工场·流水线 API:命名的算子编排(可保存复用)+ 一键执行。

预置模板(见 pipeline_presets.py)不入库,与用户自建流水线(表 pipelines)合并
展示、只读(改/删返回 400)。执行(execute)按 pipeline.scenario 分发到既有的
清洗(jobs.py)/蒸馏/合成/增强各自 _start_* 入口,产出的任务落既有 jobs 表并
回指 pipeline_id——不新建执行引擎,只是把"编排"与"执行"解耦。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_user
from app.api.v1.augment import _start_augment
from app.api.v1.distillation import _start_distillation
from app.api.v1.jobs import SessionDep, _now, _start_job
from app.api.v1.make import _start_make
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.pipeline import Pipeline
from app.models.user import User
from app.schemas.augment import AugmentGoal, AugmentJobCreate
from app.schemas.common import PageResponse
from app.schemas.distillation import DistillationJobCreate
from app.schemas.job import JobCreate, MemberOperatorConfig
from app.schemas.make import MakeGoal, MakeJobCreate
from app.schemas.pipeline import (
    PipelineCreate,
    PipelineExecuteRequest,
    PipelineRead,
    PipelineSpec,
    PipelineUpdate,
)
from app.services import pipeline_presets
from app.services.landing import MANIFEST_FORMAT, MANIFEST_MEMBER_NAME

router = APIRouter(tags=["pipelines"])

# scenario → 已播种的场景权限码(见 0028_route_perms.py),按 scenario 查询时复用,
# 不引入未播种的新权限码(否则已持有场景权限的角色打开 workbench 会被误 403)
_SCENARIO_LIST_PERM = {
    "clean": "governance:cleaning:list",
    "distillation": "governance:distillation:list",
    "synthesis": "governance:make:list",
    "augmentation": "governance:augment:list",
}


def _new_pipeline_id() -> str:
    return f"pl-{secrets.token_hex(3)}"


def _to_read(row: Pipeline) -> PipelineRead:
    """DB 行 → 读模型(isPreset 恒 False,预置模板走 pipeline_presets 独立构造)。"""
    return PipelineRead(
        id=row.id,
        name=row.name,
        description=row.description,
        scenario=row.scenario,
        spec=PipelineSpec.model_validate(row.spec),
        is_preset=False,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/pipelines",
    response_model=PageResponse[PipelineRead],
)
async def list_pipelines(
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    scenario: Annotated[str | None, Query()] = None,
) -> PageResponse[PipelineRead]:
    """分页列出流水线:预置模板固定排最前,其后接用户自建流水线(按创建时间倒序)。

    指定 scenario 时校验该场景对应的既有 governance:*:list 权限码;不指定
    scenario(如数据任务控制台按 id 批量取名)时只要求登录,不做场景鉴权。
    """
    if scenario:
        perm_code = _SCENARIO_LIST_PERM.get(scenario)
        if perm_code:
            from app.services import rbac

            perms = await rbac.get_user_perms(session, user)
            if not rbac.has_perm(perms, perm_code):
                raise HTTPException(
                    status_code=403,
                    detail={"success": False, "message": "无权限"},
                )
    presets = [
        PipelineRead.model_validate(p) for p in pipeline_presets.list_presets(scenario)
    ]
    stmt = select(Pipeline).order_by(Pipeline.created_at.desc())
    if scenario:
        stmt = stmt.where(Pipeline.scenario == scenario)
    rows = (await session.scalars(stmt)).all()
    combined = presets + [_to_read(r) for r in rows]
    total = len(combined)
    start = (current - 1) * page_size
    return PageResponse[PipelineRead](
        data=combined[start : start + page_size], total=total
    )


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str, session: SessionDep) -> JSONResponse:
    """流水线详情:预置模板优先命中(id 形如 preset-xxx 不落库)。"""
    preset = pipeline_presets.get_preset(pipeline_id)
    if preset is not None:
        read = PipelineRead.model_validate(preset)
        return JSONResponse(
            content={
                "data": read.model_dump(by_alias=True, mode="json"),
                "success": True,
            }
        )
    row = await session.get(Pipeline, pipeline_id)
    if row is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "流水线不存在"}
        )
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


@router.post("/pipelines", dependencies=[Depends(require_admin)])
async def create_pipeline(body: PipelineCreate, session: SessionDep) -> JSONResponse:
    """新建流水线(算子编排另存为可复用模板)。"""
    row = Pipeline(
        id=_new_pipeline_id(),
        name=body.name,
        description=body.description,
        scenario=body.scenario,
        spec=body.spec.model_dump(mode="json"),
        created_by="admin",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


@router.put("/pipelines/{pipeline_id}", dependencies=[Depends(require_admin)])
async def update_pipeline(
    pipeline_id: str, body: PipelineUpdate, session: SessionDep
) -> JSONResponse:
    """更新流水线(只改给出的字段);预置模板只读,返回 400。"""
    if pipeline_presets.get_preset(pipeline_id) is not None:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "预置模板不可修改"}
        )
    row = await session.get(Pipeline, pipeline_id)
    if row is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "流水线不存在"}
        )
    patch = body.model_dump(exclude_unset=True, mode="json")
    for field, value in patch.items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return JSONResponse(
        content={
            "data": _to_read(row).model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


@router.delete("/pipelines/{pipeline_id}", dependencies=[Depends(require_admin)])
async def delete_pipeline(pipeline_id: str, session: SessionDep) -> JSONResponse:
    """删除流水线;预置模板只读,返回 400。"""
    if pipeline_presets.get_preset(pipeline_id) is not None:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "预置模板不可删除"}
        )
    row = await session.get(Pipeline, pipeline_id)
    if row is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "流水线不存在"}
        )
    await session.delete(row)
    await session.commit()
    return JSONResponse(content={"success": True})


async def _resolve_pipeline(
    session: AsyncSession, pipeline_id: str
) -> PipelineRead | None:
    preset = pipeline_presets.get_preset(pipeline_id)
    if preset is not None:
        return PipelineRead.model_validate(preset)
    row = await session.get(Pipeline, pipeline_id)
    if row is None:
        return None
    return _to_read(row)


async def _execute_clean(
    session: AsyncSession,
    pipeline: PipelineRead,
    name: str,
    dataset_version_id: str,
    user: User | None = None,
) -> JSONResponse:
    """清洗场景:全部成员套用同一套算子(memberConfigs),对齐 _start_job 成员级模式。"""
    input_version = await session.get(DatasetVersion, dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据集版本不存在"}
        )
    operators = pipeline.spec.operators
    text_keys = pipeline.spec.text_keys
    if input_version.format == MANIFEST_FORMAT:
        member_configs = [
            MemberOperatorConfig(
                member_name=MANIFEST_MEMBER_NAME,
                operators=operators,
                text_keys=text_keys,
            )
        ]
    else:
        stmt = select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == dataset_version_id
        )
        members = (await session.scalars(stmt)).all()
        member_configs = [
            MemberOperatorConfig(
                member_name=m.table_name, operators=operators, text_keys=text_keys
            )
            for m in members
        ] or None
    job_body = JobCreate(
        name=name,
        type="clean",
        dataset_version_id=dataset_version_id,
        pipeline_id=pipeline.id,
        member_configs=member_configs,
        # 无成员表记录(早于成员级模型的旧版本)时退回统一 operators 配置
        operators=None if member_configs else operators,
        text_keys=None if member_configs else text_keys,
    )
    return await _start_job(session, job_body, user=user)


@router.post("/pipelines/{pipeline_id}/execute")
async def execute_pipeline(
    pipeline_id: str,
    body: PipelineExecuteRequest,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """一键执行:按流水线 scenario 分发到既有清洗/蒸馏/合成/增强执行入口。

    需登录;输入数据集 ACL ≥ edit 的校验在各 _start_* 内统一执行。
    """
    pipeline = await _resolve_pipeline(session, pipeline_id)
    if pipeline is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "流水线不存在"}
        )
    name = body.name or f"{pipeline.name}-{_now():%Y%m%d%H%M%S}"

    if pipeline.scenario == "clean":
        return await _execute_clean(
            session, pipeline, name, body.dataset_version_id, user=user
        )

    if pipeline.scenario == "distillation":
        # 蒸馏没有任务级 goal:保留多少/按什么字段/去不去重完全由算子链自身参数
        # 决定,故不像合成/增强那样要求预先配置目标。
        distill_body = DistillationJobCreate(
            name=name,
            dataset_version_id=body.dataset_version_id,
            pipeline_id=pipeline.id,
            operators=pipeline.spec.operators,
            text_keys=pipeline.spec.text_keys,
        )
        return await _start_distillation(session, distill_body, user=user)

    # 合成/增强(LLM 场景):goal 缺失说明预置模板未定制,不猜默认值,提示用户先补全
    if pipeline.scenario not in ("synthesis", "augmentation"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"未知场景:{pipeline.scenario}"},
        )
    if not pipeline.spec.goal:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "该流水线未配置目标(goal),LLM 场景需先补全目标后才能执行",
            },
        )
    operators = pipeline.spec.operators
    text_keys = pipeline.spec.text_keys
    if pipeline.scenario == "synthesis":
        try:
            make_goal = MakeGoal.model_validate(pipeline.spec.goal)
        except ValidationError:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "流水线 goal 配置不合法"},
            )
        make_body = MakeJobCreate(
            name=name,
            dataset_version_id=body.dataset_version_id,
            pipeline_id=pipeline.id,
            operators=operators,
            goal=make_goal,
            text_keys=text_keys,
        )
        return await _start_make(session, make_body, user=user)
    try:
        augment_goal = AugmentGoal.model_validate(pipeline.spec.goal)
    except ValidationError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "流水线 goal 配置不合法"},
        )
    augment_body = AugmentJobCreate(
        name=name,
        dataset_version_id=body.dataset_version_id,
        pipeline_id=pipeline.id,
        operators=operators,
        goal=augment_goal,
        text_keys=text_keys,
    )
    return await _start_augment(session, augment_body, user=user)
