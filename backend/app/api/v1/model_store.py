"""本地模型仓库路由：根路径配置 + 实时扫描清单。

- GET /model-store/config：当前根路径（含目录是否存在）。
- PUT /model-store/config：保存根路径（require_admin），立即生效——
  dj-process 是按任务起的子进程，env 由 engine 注入，无需重启。
- GET /model-store/models：实时扫描清单（就位/缺失 + 覆盖算子），
  供模型仓库页与算子编辑器下拉共用。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.schemas.common import CamelModel
from app.services import model_store

router = APIRouter(tags=["model-store"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class ModelHomeUpdate(CamelModel):
    """根路径入参；空串表示清除配置。"""

    path: str


@router.get("/model-store/config")
async def get_config() -> dict[str, Any]:
    """当前模型仓库根路径配置。"""
    from pathlib import Path

    home = model_store.get_model_home()
    return {
        "data": {
            "path": home,
            "pathExists": bool(home and Path(home).is_dir()),
        },
        "success": True,
    }


@router.put("/model-store/config", dependencies=[Depends(require_admin)])
async def update_config(body: ModelHomeUpdate, session: SessionDep) -> dict[str, Any]:
    """保存模型仓库根路径（落库 + 刷新缓存）。"""
    from pathlib import Path

    await model_store.save_model_home(session, body.path)
    home = model_store.get_model_home()
    return {
        "data": {
            "path": home,
            "pathExists": bool(home and Path(home).is_dir()),
        },
        "success": True,
    }


@router.get("/model-store/models")
async def list_models() -> dict[str, Any]:
    """实时扫描模型清单（期望模型就位状态 + 目录额外模型）。"""
    result = model_store.scan_models()
    return {
        "data": {
            "path": result["path"],
            "pathExists": result["path_exists"],
            "presentCount": result["present_count"],
            "totalCount": result["total_count"],
            "models": [
                {
                    "id": m["id"],
                    "kind": m["kind"],
                    "present": m["present"],
                    "sizeBytes": m["size_bytes"],
                    "usedBy": m["used_by"],
                    "params": m["params"],
                    "note": m["note"],
                    "group": m["group"],
                }
                for m in result["models"]
            ],
        },
        "success": True,
    }
