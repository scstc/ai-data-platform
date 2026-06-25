"""seed business menus: 把业务菜单(数据工程/数据集/评估/治理/运维/算子/助手)种子进 menus 表

Revision ID: 0021_seed_business_menus
Revises: 0020_dataset_acl
Create Date: 2026-06-25

动态菜单(B1):侧边栏由 menus 表驱动(经 getRouters 按角色授权)。本迁移把写死在
frontend/config/routes.ts 的**可见**业务菜单(M 目录 + C 叶子,非 hideInMenu)1:1 种子进表;
hideInMenu 的编辑器/详情/参数路由继续只在 routes.ts(路由用,不进侧边栏)。
普通用户角色(role-000002)回填授权全部业务菜单(保现有"全员可见"),
security/llm-settings 仅超管(保持原 access:canAdmin 语义)。超管看全部经 getRouters 通配,无需显式授权。

菜单 id 用 menu-100xxx 段,避开 0019 的系统菜单(menu-000xxx)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_seed_business_menus"
down_revision: str | None = "0020_dataset_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON_ROLE = "role-000002"


def _clean_business_menus() -> list[dict]:
    """业务可见菜单(显式、清晰版)。"""
    M = "M"
    C = "C"
    V = "0"  # visible
    S = "0"  # status
    rows: list[dict] = []

    def add(mid, pid, name, mtype, path, comp, icon, sort):
        rows.append({"id": mid, "parent_id": pid, "name": name, "menu_type": mtype,
                     "path": path, "component": comp, "perms": None, "icon": icon,
                     "sort": sort, "visible": V, "status": S})

    # 顶级
    add("menu-100001", None, "算子市场", C, "/operators", "processing/market", "block", 1)
    add("menu-100002", None, "数据接入", M, "/ingest", None, "api", 10)
    add("menu-100007", None, "数据集仓库", M, "/datasets", None, "database", 20)
    add("menu-100012", None, "数据评估", M, "/assessment", None, "audit", 30)
    add("menu-100014", None, "数据治理", M, "/governance", None, "safety", 40)
    add("menu-100021", None, "运维监控", M, "/ops", None, "dashboard", 50)
    add("menu-100026", None, "智能助手", C, "/assistant", "ingest/assistant", "robot", 70)
    # /ingest 下
    add("menu-100003", "menu-100002", "数据源", C, "/ingest/datasources", "ingest/datasources", None, 1)
    add("menu-100004", "menu-100002", "接入任务", C, "/ingest/tasks", "ingest/tasks", None, 2)
    add("menu-100005", "menu-100002", "本地上传", C, "/ingest/local-upload", "ingest/local-upload", None, 3)
    add("menu-100006", "menu-100002", "文件管理", C, "/ingest/files", "files", None, 4)
    # /datasets 下
    add("menu-100008", "menu-100007", "数据集", C, "/datasets/list", "datasets/list", None, 1)
    add("menu-100009", "menu-100007", "预设", C, "/datasets/presets", "datasets/presets", None, 2)
    add("menu-100010", "menu-100007", "分类", C, "/datasets/categories", "datasets/categories", None, 3)
    add("menu-100011", "menu-100007", "标签", C, "/datasets/tags", "datasets/tags", None, 4)
    # /assessment 下
    add("menu-100013", "menu-100012", "质量评估", C, "/assessment/quality", "quality", None, 1)
    # /governance 下
    add("menu-100015", "menu-100014", "内容安全", C, "/governance/content-safety", "content-safety", None, 1)
    add("menu-100016", "menu-100014", "清洗", C, "/governance/cleaning", "cleaning", None, 2)
    add("menu-100017", "menu-100014", "蒸馏", C, "/governance/distillation", "distillation", None, 3)
    add("menu-100018", "menu-100014", "合成", C, "/governance/make", "make", None, 4)
    add("menu-100019", "menu-100014", "增强", C, "/governance/augment", "augment", None, 5)
    add("menu-100020", "menu-100014", "标注", C, "/governance/annotation", "annotation", None, 6)
    # /ops 下
    add("menu-100022", "menu-100021", "数据任务", C, "/ops/data-tasks", "data-tasks", None, 1)
    add("menu-100023", "menu-100021", "血缘", C, "/ops/lineage", "lineage", None, 2)
    add("menu-100024", "menu-100021", "安全审计", C, "/ops/security", "security", None, 3)
    add("menu-100025", "menu-100021", "LLM 配置", C, "/ops/llm-settings", "ops/llm-settings", None, 4)
    return rows


def upgrade() -> None:
    menus = _clean_business_menus()
    op.bulk_insert(
        sa.table(
            "menus",
            sa.column("id", sa.String), sa.column("parent_id", sa.String),
            sa.column("name", sa.String), sa.column("menu_type", sa.String),
            sa.column("path", sa.String), sa.column("component", sa.String),
            sa.column("perms", sa.String), sa.column("icon", sa.String),
            sa.column("sort", sa.Integer), sa.column("visible", sa.String),
            sa.column("status", sa.String),
        ),
        menus,
    )

    # 普通用户(role-000002)← 全部业务菜单,排除 security/llm-settings(仅超管)
    admin_only = {"menu-100024", "menu-100025"}
    grants = [
        {"role_id": _COMMON_ROLE, "menu_id": m["id"]}
        for m in menus
        if m["id"] not in admin_only
    ]
    op.bulk_insert(
        sa.table("role_menus", sa.column("role_id", sa.String), sa.column("menu_id", sa.String)),
        grants,
    )


def downgrade() -> None:
    # 仅删本迁移种的业务菜单及其授权;系统菜单(menu-000xxx)与既有授权不动
    op.execute(
        "DELETE FROM role_menus WHERE menu_id LIKE 'menu-100%'"
    )
    op.execute("DELETE FROM menus WHERE id LIKE 'menu-100%'")
