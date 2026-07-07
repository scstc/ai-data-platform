"""数据集 ACL schema(共享/成员授权条目)。"""

from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class AclCreate(CamelModel):
    subject_type: str  # user | all(role 授权已取消)
    subject_id: str
    level: str  # view | edit | admin


class AclUpdate(CamelModel):
    level: str  # view | edit | admin


class AclRead(CamelModel):
    id: str
    dataset_id: str
    subject_type: str  # user | all(role 为存量遗留,仅展示/删除)
    subject_id: str
    subject_name: str | None = None  # 显示名:list 端点批量解析(user→display/username, role→name, all→固定文案)
    level: str  # view | edit | admin
    created_at: UtcDateTime
