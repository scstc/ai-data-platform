"""route-level perms: 给 25 个业务/系统 C 菜单各补一个路由级权限标识(xxx:list)

Revision ID: 0028_route_perms
Revises: 0027_notifications
Create Date: 2026-06-28

背景:权限标识(menus.perms)此前只覆盖系统管理的 16 个 F 按钮
(system:{user|role|dept|menu}:{add|edit|list|remove}),25 个业务/系统 C 菜单
(路由页)全为空 → 业务侧无路由级 RBAC。本迁移给每个 C 菜单补一个 ``xxx:list``
路由 perm,粒度=「能否进这个页面」。

为何安全/非破坏:运行期鉴权 get_user_perms 由 ``role_menus → menus.perms`` 派生
——某角色一旦被分配了该菜单,就自动获得该菜单的 perms,无需再单独授权。故后续
若给业务 list 端点加 require_perm,已持有该菜单的角色不受影响,超管 ``*:*:*`` 亦放行。

系统管理的 4 个 C 菜单复用对应 F 按钮已有的 ``system:*:list`` 码(不造新串)。
M 目录按约定不带 perms(可见性随子项),不在本迁移范围。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_route_perms"
down_revision: str | None = "0027_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 路由 perm 按 path 绑定(只认 C 菜单)。系统管理 C 复用已有 system:*:list。
_PERM_BY_PATH: dict[str, str] = {
    # 数据接入
    "/ingest/datasources": "ingest:datasource:list",
    "/ingest/tasks": "ingest:task:list",
    "/ingest/local-upload": "ingest:upload:list",
    "/ingest/files": "ingest:file:list",
    # 数据集仓库
    "/datasets/list": "dataset:list",
    "/datasets/presets": "dataset:preset:list",
    "/datasets/categories": "dataset:category:list",
    "/datasets/tags": "dataset:tag:list",
    # 数据治理
    "/governance/content-safety": "governance:contentsafety:list",
    "/governance/cleaning": "governance:cleaning:list",
    "/governance/distillation": "governance:distillation:list",
    "/governance/make": "governance:make:list",
    "/governance/augment": "governance:augment:list",
    "/governance/annotation": "governance:annotation:list",
    # 数据评估
    "/assessment/quality": "assessment:quality:list",
    # 算子市场 / 智能助手
    "/operators": "operator:list",
    "/assistant": "assistant:list",
    # 运维监控
    "/ops/data-tasks": "ops:datatask:list",
    "/ops/lineage": "ops:lineage:list",
    "/ops/security": "ops:security:list",
    "/ops/llm-settings": "ops:llm:list",
    # 系统管理(C 菜单复用对应 F 的 list 码)
    "/system/user": "system:user:list",
    "/system/role": "system:role:list",
    "/system/dept": "system:dept:list",
    "/system/menu": "system:menu:list",
}


def upgrade() -> None:
    stmt = sa.text(
        "UPDATE menus SET perms = :perm WHERE path = :path AND menu_type = 'C'"
    )
    for path, perm in _PERM_BY_PATH.items():
        op.execute(stmt.bindparams(perm=perm, path=path))


def downgrade() -> None:
    # 这 25 个 C 菜单在本迁移前 perms 均为 NULL,回滚整体清空即可。
    stmt = sa.text(
        "UPDATE menus SET perms = NULL WHERE path = :path AND menu_type = 'C'"
    )
    for path in _PERM_BY_PATH:
        op.execute(stmt.bindparams(path=path))
