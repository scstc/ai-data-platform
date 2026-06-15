"""文件管理请求体 schema(收编 BCC 文件管理,见 docs/plan/10-文件管理设计.md)。

读端点的响应均为 JSONResponse 裸 dict(不套读模型);此处只定义写端点的请求体。
"""

from __future__ import annotations

from app.schemas.common import CamelModel


class FolderCreate(CamelModel):
    """新建文件夹:{bucket, prefix?, name}。最终 key = f"{prefix}{name}"。"""

    bucket: str
    prefix: str = ""
    name: str


class FolderDelete(CamelModel):
    """递归删文件夹:{bucket, prefix}。"""

    bucket: str
    prefix: str
