"""上传记录路由：列表分页 GET /uploads 与文件上传 POST /upload。"""

from __future__ import annotations

import secrets
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_perm
from app.core.config import settings
from app.core.db import get_session
from app.models.upload import UploadRecord
from app.schemas.common import CamelModel, PageResponse
from app.schemas.upload import UploadRecordRead

router = APIRouter(tags=["uploads"])

# Annotated 依赖别名（与 datasources.py 的 SessionDep 同款写法，规避 ruff B008）
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UploadFileDep = Annotated[UploadFile, File(...)]

# 允许的扩展名白名单（小写，不含点）；匹配时大小写不敏感
ALLOWED_EXTENSIONS = {
    "txt",
    "pdf",
    "ppt",
    "pptx",
    "doc",
    "docx",
    "xlsx",
    "xls",
    "csv",
    "tsv",
    "html",
    "jsonl",
    "md",
    "markdown",
}

# 单文件大小上限：200 MB
MAX_UPLOAD_SIZE = 200 * 1024 * 1024


class UploadResult(CamelModel):
    """单个上传记录响应：{data:UploadRecord, success:true}。"""

    data: UploadRecordRead
    success: bool = True


def _new_upload_id() -> str:
    """生成形如 ``up-`` + 6 位 hex 的主键。"""
    return f"up-{secrets.token_hex(3)}"


def _file_ext(filename: str) -> str:
    """取文件扩展名（小写、不含点）；无扩展名返回空串。"""
    suffix = Path(filename).suffix
    return suffix[1:].lower() if suffix else ""


@router.get(
    "/uploads",
    response_model=PageResponse[UploadRecordRead],
    dependencies=[Depends(require_perm("ingest:upload:list"))],
)
async def list_uploads(
    session: SessionDep,
    current: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, alias="pageSize"),
) -> PageResponse[UploadRecordRead]:
    """分页查询上传记录，按上传时间倒序。"""
    total = await session.scalar(select(func.count()).select_from(UploadRecord))
    offset = (current - 1) * page_size
    rows = (
        await session.scalars(
            select(UploadRecord)
            .order_by(UploadRecord.uploaded_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()
    data = [UploadRecordRead.model_validate(r) for r in rows]
    return PageResponse[UploadRecordRead](data=data, total=total or 0)


@router.post("/upload")
async def create_upload(
    file: UploadFileDep,
    session: SessionDep,
) -> JSONResponse:
    """上传单个文件：校验扩展名/大小 → 落盘 → 写入 PG 记录。"""
    filename = file.filename or ""
    ext = _file_ext(filename)

    # 扩展名白名单校验（大小写不敏感）
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"不支持的文件类型 .{ext}，仅允许："
                + "、".join(sorted(ALLOWED_EXTENSIONS)),
            },
        )

    # 读取内容并按实际字节数校验大小
    content = await file.read()
    size = len(content)
    if size > MAX_UPLOAD_SIZE:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "文件超过 200MB 上限"},
        )

    # 落盘：目录不存在则创建，uuid 前缀防重名
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}_{filename}"
    (upload_dir / stored_name).write_bytes(content)

    # 写入 PG 记录
    record = UploadRecord(
        id=_new_upload_id(),
        filename=filename,
        size=size,
        format=ext,
        status="done",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    payload = UploadResult(data=UploadRecordRead.model_validate(record))
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))
