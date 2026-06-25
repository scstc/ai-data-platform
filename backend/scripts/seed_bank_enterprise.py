"""种子脚本:向 dev 库灌入一家模拟商业银行的组织架构 / 角色 / 员工。

用途:RBAC 与部门树 UI 的演示与联调数据(非生产迁移,按需运行,幂等可重跑)。
模拟对象:华信商业银行(虚构)。银行根本身即 dept-000000——该 id 是迁移 0019 建的
占位根部门(原名"AI 数据平台"),本脚本将其改名为"华信商业银行"作为组织树唯一根
(ancestors="0");总行 / 各分行直接挂在该根下。

统一口令:hxbank@2026(仅 demo,脚本头已写明,生产环境勿用)。

幂等:每次运行先按精确 id 删除本脚本历史灌入的子部门(含上一版的废弃中间层
dept-100000),再把根部门 dept-000000 改名为银行名,最后重新插入子部门/角色/员工。
可安全重跑。

运行(脚本会自行 chdir 到 backend/ 以便读到 .env):
    ./.venv/Scripts/python.exe scripts/seed_bank_enterprise.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 运行前切到 backend/:app.core.config 以相对 cwd 的 ".env" 读取配置,
# 且 app 包需在 sys.path 上。
_BACKEND = Path(__file__).resolve().parent.parent
os.chdir(_BACKEND)
sys.path.insert(0, str(_BACKEND))

from sqlalchemy import delete, update  # noqa: E402

from app.core.db import async_session_factory  # noqa: E402
from app.models.department import Department  # noqa: E402
from app.models.rbac_links import UserRole  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth import hash_password  # noqa: E402

# 银行根:复用迁移 0019 已建的 dept-000000(原占位名"AI 数据平台"),
# 本脚本将其改名为"华信商业银行",作为整棵组织树的根(ancestors="0")。
_BANK_ROOT = "dept-000000"
# 上一版脚本曾用 dept-100000 作银行根,重跑时清掉这个废弃中间层。
_LEGACY_BANK_ROOT = "dept-100000"
_DOMAIN = "huaxin-bank.com.cn"
_DEFAULT_PWD = "hxbank@2026"  # 所有 demo 员工统一口令

# ---------------------------------------------------------------------------
# 组织架构:总行(13 个部) + 3 家分行(含 7 家支行),全部直接挂在银行根 dept-000000 下。
# 根部门 dept-000000 本身不在此列表(它是迁移既有行,由 main() 改名更新,不能删)。
# (id, parent_id, name, sort, leader, phone, email)
# ---------------------------------------------------------------------------
_DEPARTMENTS: list[tuple] = [
    # ---- 总行 ----
    ("dept-110000", _BANK_ROOT, "总行", 1, None, None, None),
    ("dept-110100", "dept-110000", "董事会办公室", 1, None, None, None),
    ("dept-110200", "dept-110000", "监事会办公室", 2, None, None, None),
    ("dept-110300", "dept-110000", "办公室(行长室)", 3, "陈志远", "010-66608301", f"office@{_DOMAIN}"),
    ("dept-110400", "dept-110000", "公司金融部", 4, "李建国", "010-66608401", f"corp@{_DOMAIN}"),
    ("dept-110500", "dept-110000", "零售金融部", 5, "张丽华", "010-66608501", f"retail@{_DOMAIN}"),
    ("dept-110600", "dept-110000", "金融市场部", 6, "刘伟", "010-66608601", f"market@{_DOMAIN}"),
    ("dept-110700", "dept-110000", "风险管理部", 7, "王慧敏", "010-66608701", f"risk@{_DOMAIN}"),
    ("dept-110800", "dept-110000", "内控合规部", 8, "郑文静", "010-66608801", f"compliance@{_DOMAIN}"),
    ("dept-110900", "dept-110000", "信息科技部", 9, "赵强", "010-66608901", f"it@{_DOMAIN}"),
    ("dept-111000", "dept-110000", "人力资源部", 10, "孙美玲", "010-66609001", f"hr@{_DOMAIN}"),
    ("dept-111100", "dept-110000", "计划财务部", 11, "周明", "010-66609101", f"finance@{_DOMAIN}"),
    ("dept-111200", "dept-110000", "运营管理部", 12, None, None, None),
    ("dept-111300", "dept-110000", "审计部", 13, "黄国平", "010-66609301", f"audit@{_DOMAIN}"),
    # ---- 北京分行 ----
    ("dept-120000", _BANK_ROOT, "北京分行", 2, "罗志强", "010-66610000", f"bj@{_DOMAIN}"),
    ("dept-120100", "dept-120000", "分行营业部", 1, None, None, None),
    ("dept-120200", "dept-120000", "中关村支行", 2, "邓秀芳", "010-82888001", f"zgc@{_DOMAIN}"),
    ("dept-120300", "dept-120000", "国贸支行", 3, None, None, None),
    ("dept-120400", "dept-120000", "海淀支行", 4, None, None, None),
    # ---- 上海分行 ----
    ("dept-130000", _BANK_ROOT, "上海分行", 3, "高建华", "021-66620000", f"sh@{_DOMAIN}"),
    ("dept-130100", "dept-130000", "陆家嘴支行", 1, "冯德海", "021-58880001", f"ljz@{_DOMAIN}"),
    ("dept-130200", "dept-130000", "徐汇支行", 2, None, None, None),
    # ---- 深圳分行 ----
    ("dept-140000", _BANK_ROOT, "深圳分行", 4, "蔡明亮", "0755-66630000", f"sz@{_DOMAIN}"),
    ("dept-140100", "dept-140000", "福田支行", 1, None, None, None),
    ("dept-140200", "dept-140000", "南山支行", 2, None, None, None),
]

# ---------------------------------------------------------------------------
# 业务角色:覆盖全部 5 种 data_scope(all/custom/dept/dept_and_child/self)。
# (id, name, role_key, sort, data_scope, remark)
# 注:超管 admin(role-000001)/普通用户 common(role-000002)为迁移 0019 既有,不在此重建。
# ---------------------------------------------------------------------------
_ROLES: list[tuple] = [
    ("role-000003", "行长/高管", "executive", 3, "all", "总行高管,可见全行数据"),
    ("role-000004", "部门负责人", "dept_manager", 4, "dept_and_child", "总行部门负责人"),
    ("role-000005", "分行行长", "branch_manager", 5, "dept_and_child", "管辖本分行及下属支行"),
    ("role-000006", "支行行长", "sub_branch_manager", 6, "dept", "仅可见本支行"),
    ("role-000007", "客户经理", "relationship_manager", 7, "self", "仅本人名下客户"),
    ("role-000008", "风控专员", "risk_officer", 8, "dept_and_child", "风险条线"),
    ("role-000009", "合规专员", "compliance_officer", 9, "dept_and_child", "合规条线"),
    ("role-000010", "审计员", "auditor", 10, "all", "审计独立,可见全行"),
    ("role-000011", "柜员", "teller", 11, "self", "网点柜面"),
    ("role-000012", "科技运维", "it_ops", 12, "all", "信息科技运维"),
]

# ---------------------------------------------------------------------------
# 员工:25 名,分布于总行各部门与各分行/支行,平台级 role=user,业务角色走 user_roles。
# (id, username, display_name, dept_id, role_id)
# ---------------------------------------------------------------------------
_EMPLOYEES: list[tuple] = [
    # 总行高管
    ("usr-100001", "chenzhiyuan", "陈志远", "dept-110300", "role-000003"),  # 行长
    ("usr-100002", "wanghuimin", "王慧敏", "dept-110700", "role-000003"),  # 副行长/首席风险官
    # 总行部门负责人
    ("usr-100003", "lijianguo", "李建国", "dept-110400", "role-000004"),  # 公司金融部
    ("usr-100004", "zhanglihua", "张丽华", "dept-110500", "role-000004"),  # 零售金融部
    ("usr-100005", "liuwei", "刘伟", "dept-110600", "role-000004"),  # 金融市场部
    ("usr-100006", "zhaoqiang", "赵强", "dept-110900", "role-000004"),  # 信息科技部
    ("usr-100007", "sunmeiling", "孙美玲", "dept-111000", "role-000004"),  # 人力资源部
    ("usr-100008", "zhouming", "周明", "dept-111100", "role-000004"),  # 计划财务部
    # 风控/合规/审计条线
    ("usr-100009", "wuxiaofeng", "吴晓峰", "dept-110700", "role-000008"),  # 风控专员
    ("usr-100010", "zhengwenjing", "郑文静", "dept-110800", "role-000009"),  # 合规专员
    ("usr-100011", "huangguoping", "黄国平", "dept-111300", "role-000010"),  # 审计员
    # 信息科技部运维
    ("usr-100012", "linhao", "林浩", "dept-110900", "role-000012"),
    ("usr-100013", "yangfan", "杨帆", "dept-110900", "role-000012"),
    # 总行客户经理
    ("usr-100014", "xulei", "徐磊", "dept-110400", "role-000007"),
    ("usr-100015", "hejing", "何静", "dept-110500", "role-000007"),
    # 分行行长
    ("usr-100016", "luozhiqiang", "罗志强", "dept-120000", "role-000005"),  # 北京分行
    ("usr-100017", "gaojianhua", "高建华", "dept-130000", "role-000005"),  # 上海分行
    ("usr-100018", "caimingliang", "蔡明亮", "dept-140000", "role-000005"),  # 深圳分行
    # 支行行长
    ("usr-100019", "dengxiufang", "邓秀芳", "dept-120200", "role-000006"),  # 中关村支行
    ("usr-100020", "fengdehai", "冯德海", "dept-130100", "role-000006"),  # 陆家嘴支行
    # 支行客户经理
    ("usr-100021", "shenwei", "沈炜", "dept-120300", "role-000007"),  # 国贸支行
    ("usr-100022", "songjia", "宋佳", "dept-140200", "role-000007"),  # 南山支行
    # 柜员
    ("usr-100023", "hanxue", "韩雪", "dept-120200", "role-000011"),  # 中关村支行
    ("usr-100024", "caojun", "曹俊", "dept-130100", "role-000011"),  # 陆家嘴支行
    ("usr-100025", "pengli", "彭丽", "dept-140100", "role-000011"),  # 福田支行
]


def _ancestors(dept_id: str, parent_map: dict[str, str | None]) -> str:
    """由父链推 ancestors:银行根 dept-000000 的 ancestors="0"。

    例:dept-120200(中关村支行)→ "0,dept-000000,dept-120000,"。
    """
    chain: list[str] = []
    cur = parent_map.get(dept_id)
    while cur is not None:
        chain.append(cur)
        cur = parent_map.get(cur)
    chain.reverse()  # 自根向下:银行根 → ... → 直接父
    return "0," + ",".join(chain) + "," if chain else "0"


async def main() -> None:
    # parent_map 覆盖所有子部门 + 银行根(根无父)。
    parent_map: dict[str, str | None] = {d[0]: d[1] for d in _DEPARTMENTS}
    parent_map[_BANK_ROOT] = None

    dept_ids = [d[0] for d in _DEPARTMENTS]
    role_ids = [r[0] for r in _ROLES]
    user_ids = [e[0] for e in _EMPLOYEES]

    async with async_session_factory() as session:
        # 幂等:先按精确 id 清掉本脚本历史灌入的子部门(含废弃中间层 dept-100000)、
        # 角色、员工及其角色绑定(user_roles 需先于 users)。根 dept-000000 不删——
        # 它是迁移既有行,且被 admin/user 等引用,只能改名。
        await session.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.execute(delete(Role).where(Role.id.in_(role_ids)))
        await session.execute(
            delete(Department).where(Department.id.in_(dept_ids + [_LEGACY_BANK_ROOT]))
        )
        await session.flush()

        # 根部门:占位名"AI 数据平台" → "华信商业银行"(ancestors="0",无父)。
        await session.execute(
            update(Department)
            .where(Department.id == _BANK_ROOT)
            .values(
                parent_id=None,
                ancestors="0",
                name="华信商业银行",
                leader="陈志远",
                phone="010-66608888",
                email=f"admin@{_DOMAIN}",
                status="0",
            )
        )

        for did, pid, name, sort, leader, phone, email in _DEPARTMENTS:
            session.add(
                Department(
                    id=did,
                    parent_id=pid,
                    ancestors=_ancestors(did, parent_map),
                    name=name,
                    sort=sort,
                    leader=leader,
                    phone=phone,
                    email=email,
                    status="0",
                )
            )
        for rid, name, key, sort, scope, remark in _ROLES:
            session.add(
                Role(
                    id=rid,
                    name=name,
                    role_key=key,
                    sort=sort,
                    data_scope=scope,
                    status="0",
                    remark=remark,
                )
            )
        pwd_hash = hash_password(_DEFAULT_PWD)
        for uid, username, display, dept_id, role_id in _EMPLOYEES:
            session.add(
                User(
                    id=uid,
                    username=username,
                    password_hash=pwd_hash,
                    role="user",
                    display_name=display,
                    dept_id=dept_id,
                    disabled=False,
                )
            )
            session.add(UserRole(user_id=uid, role_id=role_id))

        await session.commit()

    print(
        f"已灌入 demo 数据:银行根 1 + 子部门 {len(_DEPARTMENTS)} / 角色 {len(_ROLES)} / "
        f"员工 {len(_EMPLOYEES)}(统一口令 {_DEFAULT_PWD})"
    )
    print("示例登录:chenzhiyuan / wanghuimin / luozhiqiang / hanxue ...")


if __name__ == "__main__":
    asyncio.run(main())
