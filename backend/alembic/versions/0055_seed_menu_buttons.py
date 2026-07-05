"""seed menu buttons: 给业务 C 菜单补真实的按钮级权限(F)——按各页面实际操作按钮

Revision ID: 0055_seed_menu_buttons
Revises: 0054_trainset_menu
Create Date: 2026-07-06

背景:系统管理的 4 个 C 菜单早已各带 4 个 F 按钮(查询/新增/修改/删除),而 24 个
业务 C 菜单此前只有 0028 补的路由级 ``xxx:list``,没有按钮级权限行 → 角色「分配菜单」
矩阵的「权限」列对业务菜单全为空。本迁移逐页探索各业务页面**真实存在**的操作按钮
(新建/编辑/删除/执行/停止/测试连接/托管/导出…),1:1 种成 F 菜单挂到对应 C 下,
让权限矩阵按实际可分配。

约定(与 0019 系统菜单一致):
- F 行:menu_type='F',path/component=NULL,perms=``<base>:<action>``,visible='0';
- 每菜单第一颗按钮 action='list'、label='查询',perms 复用该 C 已有的 ``<base>:list``
  (与系统菜单「用户管理查询」复用 system:user:list 同口径);
- F id = ``<Cid><序号>``(如 menu-100003 → menu-1000031..),业务段 12 位不与任何现存
  id 冲突;
- **不回填 role_menus**:超管 ``*:*:*`` 通配无需授权,其余角色由管理员经「分配菜单」
  矩阵按需勾选(现阶段业务页尚无 hasPerm 门控,不授权即不改变现状,后续加门控时再收紧)。

治理工场(menu-100029)、数据湖列表(menu-100028)此前 C.perms 为 NULL(晚于 0028
才建),本迁移顺带补 ``governance:workbench:list`` / ``datalake:list`` 路由 perm。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0054 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055_seed_menu_buttons"
down_revision: str | None = "0054_trainset_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# cid -> (菜单名, 权限码 base, [(按钮中文标签, 英文 action), ...])
# 每项按钮 perms = f"{base}:{action}";查询(list)复用该 C 的既有 list 码。
# 清单来自逐页源码探索,仅收录真实存在且会触发动作的按钮(剔除占位 message.info、
# 表单内微操作、只读详情/预览、抽屉内子操作)。动作词已归一(create→add / delete→remove /
# 批量→batch-remove / retry→rerun / cancel→stop)。
_BUTTONS: dict[str, tuple[str, str, list[tuple[str, str]]]] = {
    # 算子市场
    "menu-100001": ("算子市场", "operator", [("查询", "list"), ("上传自定义算子", "upload")]),
    # 智能助手(纯对话页)
    "menu-100026": ("智能助手", "assistant", [("查询", "list")]),
    # 数据接入
    "menu-100003": ("数据源管理", "ingest:datasource", [
        ("查询", "list"), ("新建", "add"), ("编辑", "edit"), ("删除", "remove"),
        ("测试连接", "test"), ("重新检测", "recheck"), ("轮换Token", "rotate"),
    ]),
    "menu-100004": ("采集任务", "ingest:task", [
        ("查询", "list"), ("新建", "add"), ("编辑", "edit"),
        ("运行", "run"), ("停止", "stop"), ("删除", "remove"),
    ]),
    "menu-100005": ("本地上传", "ingest:upload", [("查询", "list"), ("上传", "upload")]),
    "menu-100006": ("文件管理", "ingest:file", [
        ("查询", "list"), ("上传", "upload"), ("新建文件夹", "add"),
        ("下载", "download"), ("接入数据集", "import"), ("删除", "remove"),
    ]),
    # 数据集仓库
    "menu-100008": ("数据集列表", "dataset", [
        ("查询", "list"), ("新建", "add"), ("托管S3", "host"), ("编辑", "edit"),
        ("取消托管", "unhost"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100009": ("已发布数据集", "dataset:preset", [
        ("查询", "list"), ("下载", "download"), ("导出", "export"), ("编辑", "edit"),
    ]),
    "menu-100010": ("分类管理", "dataset:category", [
        ("查询", "list"), ("新增", "add"), ("编辑", "edit"), ("删除", "remove"),
    ]),
    "menu-100011": ("标签管理", "dataset:tag", [
        ("查询", "list"), ("新建", "add"), ("重命名", "edit"), ("删除", "remove"),
        ("批量删除", "batch-remove"), ("合并", "merge"),
    ]),
    # 数据评估
    "menu-100013": ("质量评估", "assessment:quality", [
        ("查询", "list"), ("新建", "add"), ("删除", "remove"),
        ("批量删除", "batch-remove"), ("删除低质数据", "filter"),
    ]),
    # 数据治理
    "menu-100015": ("内容安全", "governance:contentsafety", [
        ("查询", "list"), ("开始审核", "run"), ("新增规则", "add"),
        ("编辑规则", "edit"), ("删除规则", "remove"),
    ]),
    "menu-100029": ("治理工场", "governance:workbench", [
        ("查询", "list"), ("新建编排", "add"), ("执行", "run"),
        ("编辑编排", "edit"), ("删除", "remove"),
    ]),
    "menu-100016": ("数据清洗", "governance:cleaning", [
        ("查询", "list"), ("新建", "add"), ("编辑编排", "edit"), ("执行", "run"),
        ("停止", "stop"), ("重新运行", "rerun"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100017": ("数据蒸馏", "governance:distillation", [
        ("查询", "list"), ("新建", "add"), ("编辑编排", "edit"), ("执行", "run"),
        ("停止", "stop"), ("重新运行", "rerun"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100018": ("数据合成", "governance:make", [
        ("查询", "list"), ("新建", "add"), ("停止", "stop"),
        ("重新运行", "rerun"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100019": ("数据增强", "governance:augment", [
        ("查询", "list"), ("新建", "add"), ("编辑编排", "edit"), ("执行", "run"),
        ("停止", "stop"), ("重新运行", "rerun"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100030": ("训练集生成", "governance:trainset", [
        ("查询", "list"), ("新建", "add"), ("停止", "stop"),
        ("重新运行", "rerun"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100020": ("数据标注", "governance:annotation", [("查询", "list")]),  # 占位页
    # 运维监控
    "menu-100022": ("数据任务", "ops:datatask", [
        ("查询", "list"), ("暂停", "pause"), ("继续", "resume"), ("停止", "stop"),
        ("重新运行", "rerun"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
    "menu-100023": ("数据血缘", "ops:lineage", [("查询", "list")]),  # 只读
    "menu-100024": ("安全审计", "ops:security", [("查询", "list")]),  # 只读审计日志
    "menu-100025": ("LLM 配置", "ops:llm", [
        ("查询", "list"), ("新建供应商", "add"), ("编辑", "edit"), ("删除", "remove"),
        ("测试", "test"), ("激活", "activate"), ("管理模型", "manage-model"),
    ]),
    # 数据湖
    "menu-100028": ("数据湖列表", "datalake", [
        ("查询", "list"), ("新建", "add"), ("删除", "remove"), ("批量删除", "batch-remove"),
    ]),
}

# 晚于 0028 才建、C.perms 仍为 NULL 的菜单,顺带补路由 perm(仅当仍为 NULL)
_SET_C_PERMS: dict[str, str] = {
    "menu-100029": "governance:workbench:list",
    "menu-100028": "datalake:list",
}


def _f_rows() -> list[dict]:
    rows: list[dict] = []
    for cid, (cname, base, buttons) in _BUTTONS.items():
        for j, (label, action) in enumerate(buttons, start=1):
            rows.append({
                "id": f"{cid}{j}", "parent_id": cid,
                "name": f"{cname}{label}", "menu_type": "F",
                "path": None, "component": None,
                "perms": f"{base}:{action}", "icon": None,
                "sort": j, "visible": "0", "status": "0",
            })
    return rows


def upgrade() -> None:
    for cid, perm in _SET_C_PERMS.items():
        op.execute(
            sa.text("UPDATE menus SET perms = :p WHERE id = :i AND perms IS NULL")
            .bindparams(p=perm, i=cid)
        )
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
        _f_rows(),
    )


def downgrade() -> None:
    ids = [r["id"] for r in _f_rows()]
    id_list = ",".join(f"'{i}'" for i in ids)
    # 未回填授权,但为稳妥先清可能的 role_menus 引用
    op.execute(f"DELETE FROM role_menus WHERE menu_id IN ({id_list})")
    op.execute(f"DELETE FROM menus WHERE id IN ({id_list})")
    for cid, perm in _SET_C_PERMS.items():
        op.execute(
            sa.text("UPDATE menus SET perms = NULL WHERE id = :i AND perms = :p")
            .bindparams(i=cid, p=perm)
        )
