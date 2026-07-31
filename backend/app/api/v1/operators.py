"""算子目录路由:兼容旧 /operators + 全量算子工厂(查询/分面/详情)。

设计见 docs/plan/04-算子市场设计.md。算子目录是无状态参考数据(不入库)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import update

from app.api.deps import SessionDep, require_perm, require_user
from app.core.config import settings
from app.models.operator import Operator
from app.models.user import User
from app.services import capabilities as caps
from app.services import operator_catalog as oc
from app.services.custom_operators import (
    MAX_SOURCE_BYTES,
    CustomOperatorError,
    parse_custom_operator,
    parse_params_json,
)
from app.services.engine import multimodal_ready
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["operators"])


@router.get("/operators/capabilities")
async def operator_capabilities() -> JSONResponse:
    """当前环境探测到的执行能力(cuda/vllm/ray/llm),供前端「环境能力」指示。"""
    return JSONResponse(content={"data": caps.capabilities_api(), "success": True})


@router.get("/operators")
async def list_operators() -> JSONResponse:
    """加工算子目录(旧形态,供加工页编排选择)。

    含 ready;并按当前部署能力额外纳入:配了 LLM API → needs_api;装了多模态引擎
    (torch)→ needs_media。needs_compute 不列出(无 GPU 跑不通)。
    """
    data = oc.legacy_operators(
        llm_configured=bool(get_active_llm_config().api_key),
        multimodal_ready=await multimodal_ready(),
    )
    return JSONResponse(content={"data": data, "success": True})


# 注意:/operators/catalog* 必须声明在 /operators/{name} 之前,
# 否则 "catalog" 会被当成 name 命中详情路由。
@router.get("/operators/catalog/drift")
async def catalog_drift() -> JSONResponse:
    """算子快照漂移检测(G15):对比 DB 快照与 DJ venv 真实安装。

    运维/CI 用。status=unavailable 表示本环境无 DJ venv 无法判定;首次探测约数十秒
    (import data_juicer 慢),结果进程内缓存后续秒回。
    """
    return JSONResponse(content={"data": oc.detect_operator_drift(), "success": True})


@router.get("/operators/catalog/meta")
async def catalog_meta() -> JSONResponse:
    """算子目录概览(总数/各维度分布/推荐数),驱动工厂筛选项与统计卡。"""
    return JSONResponse(content={"data": oc.meta_api(), "success": True})


# 同步 def:纯同步体(DB 查询 + 可能的冷能力探测),走线程池执行,
# 不阻塞事件循环——否则冷探测(~2.5s)会拖住同页并发的其他请求。
@router.get("/operators/catalog")
def catalog(
    scenario: Annotated[str | None, Query()] = None,
    bucket: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    modality: Annotated[str | None, Query()] = None,
    resource_class: Annotated[str | None, Query(alias="resourceClass")] = None,
    runnable: Annotated[str | None, Query()] = None,
    recommend: Annotated[bool | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    include_hidden: Annotated[bool, Query(alias="includeHidden")] = False,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500, alias="pageSize")] = 24,
) -> JSONResponse:
    """算子工厂主接口:多维分面过滤 + 分页。

    ``bucket`` 业务桶(cleansing/distillation/make/augment):任务编辑器按此只拉对应算子。
    ``includeHidden`` 纳入已隐藏算子(工厂管理视图用);默认只出可见算子。
    """
    result = oc.query_catalog(
        scenario=scenario,
        bucket=bucket,
        category=category,
        modality=modality,
        resource_class=resource_class,
        runnable=runnable,
        recommend=recommend,
        keyword=keyword,
        include_hidden=include_hidden,
        current=current,
        page_size=page_size,
    )
    return JSONResponse(
        content={
            "data": [oc.to_api(op) for op in result["data"]],
            "total": result["total"],
            "success": True,
        }
    )


@router.post("/operators/custom")
async def upload_custom_operator(
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
    file: UploadFile,
    zh_label: Annotated[str, Form(alias="zhLabel")],
    summary_zh: Annotated[str | None, Form(alias="summaryZh")] = None,
    desc_zh: Annotated[str | None, Form(alias="descZh")] = None,
    zh_usage_tip: Annotated[str | None, Form(alias="zhUsageTip")] = None,
    scenario_group: Annotated[str | None, Form(alias="scenarioGroup")] = None,
    example: Annotated[str | None, Form()] = None,
    params_json: Annotated[str | None, Form(alias="params")] = None,
) -> JSONResponse:
    """上传自定义算子(.py 源码,静态校验后注册进算子工厂目录)。

    文件须恰好定义一个继承 Mapper/Filter/Deduplicator/Selector 的算子类,并带
    ``@OPERATORS.register_module("算子名")`` 装饰器——与 data-juicer 原生自定义
    算子写法一致;任务实际执行时经 ``custom_operator_paths`` 动态加载进该子进程的
    OPERATORS 注册表(见 engine.build_config)。仅做结构性静态校验,不是安全沙箱
    (data-juicer 自身加载器同样无沙箱,详见 custom_operators.py 模块说明)。
    """
    if not zh_label.strip():
        return JSONResponse(
            status_code=422,
            content={"success": False, "message": "请填写算子中文名称"},
        )
    content = await file.read()
    if len(content) > MAX_SOURCE_BYTES:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"源文件超过 {MAX_SOURCE_BYTES // 1024}KB 上限",
            },
        )
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "文件须为 UTF-8 编码的 Python 源码"},
        )
    try:
        info = parse_custom_operator(source)
    except CustomOperatorError as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )

    params: list[dict[str, str]] | None = None
    if params_json:
        try:
            params = parse_params_json(params_json)
        except CustomOperatorError as exc:
            return JSONResponse(
                status_code=422, content={"success": False, "message": str(exc)}
            )

    existing = await session.get(Operator, info.op_name)
    if existing is not None:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"算子名「{info.op_name}」已存在(内置或已上传),"
                "请改名后重新注册",
            },
        )

    rel_name = f"{info.op_name}.py"
    dest_dir = Path(settings.upload_dir) / "custom_operators"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / rel_name).write_text(source, encoding="utf-8")

    record = Operator(
        name=info.op_name,
        category=info.category,
        zh_label=zh_label.strip(),
        summary_zh=summary_zh,
        desc_zh=desc_zh,
        zh_usage_tip=zh_usage_tip,
        scenario_group=scenario_group,
        example=example,
        params=params,
        resource_class="cpu",
        runnable="ready",
        is_custom=True,
        source_object_key=rel_name,
        created_by=user.username,
    )
    try:
        session.add(record)
        await session.commit()
    except Exception:
        await session.rollback()
        (dest_dir / rel_name).unlink(missing_ok=True)
        raise

    return JSONResponse(
        content={"data": oc.to_api(oc.get_operator(info.op_name)), "success": True}
    )


@router.delete("/operators/custom/{name}")
async def delete_custom_operator(
    name: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """删除自定义算子(仅上传者本人或 admin)。内置算子不可删。"""
    record = await session.get(Operator, name)
    if record is None or not record.is_custom:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "自定义算子不存在"}
        )
    if record.created_by != user.username and user.role != "admin":
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无权限删除他人上传的算子"},
        )
    if record.source_object_key:
        op_dir = Path(settings.upload_dir) / "custom_operators"
        (op_dir / record.source_object_key).unlink(missing_ok=True)
    await session.delete(record)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.patch("/operators/{name}/visible")
async def set_operator_visible(
    name: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_perm("operator:visibility"))],
    visible: Annotated[bool, Body(embed=True)],
) -> JSONResponse:
    """设置算子可见性:隐藏后工厂与任务编排选择器不再展示。

    已编排任务不受影响——执行与提交校验(get_operator/runnable_reason)仍走全量。
    """
    record = await session.get(Operator, name)
    if record is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "算子不存在"}
        )
    record.visible = visible
    await session.commit()
    return JSONResponse(
        content={"data": {"name": name, "visible": visible}, "success": True}
    )


@router.post("/operators/{name}/star")
async def star_operator(
    name: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """算子加星:纯正向人气计数,任意登录用户可点,每次 +1,不做撤销/去重。"""
    record = await session.get(Operator, name)
    if record is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "算子不存在"}
        )
    await session.execute(
        update(Operator)
        .where(Operator.name == name)
        .values(star_count=Operator.star_count + 1)
    )
    await session.commit()
    await session.refresh(record)
    return JSONResponse(
        content={
            "data": {"name": name, "starCount": record.star_count},
            "success": True,
        }
    )


@router.get("/operators/{name}")
async def operator_detail(name: str) -> JSONResponse:
    """单算子详情(全字段:参数表 + 示例 + 引用 + 详情页链接)。"""
    op = oc.get_operator(name)
    if op is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "算子不存在"},
        )
    return JSONResponse(content={"data": oc.to_api(op), "success": True})
