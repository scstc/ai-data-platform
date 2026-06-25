"""数据集 ACL schema(共享/成员授权条目)。"""

from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class AclCreate(CamelModel):
    subject_type: str  # user | role
    subject_id: str
    level: str  # view | edit | admin


class AclUpdate(CamelModel):
    level: str  # view | edit | admin


class AclRead(CamelModel):
    id: str
    dataset_id: str
    subject_type: str
    subject_id: str
    level: str
    created_at: UtcDateTime
