# 系统管理 RBAC（用户/角色/部门/菜单/权限）— 设计稿

- 日期：2026-06-25
- 状态：已定稿，进入实现
- 范围：完整动态 RBAC（若依风格）。新增「系统管理」子域：用户/角色/部门/菜单管理 + 权限授权；动态路由；按部门的数据权限真过滤。

## 决策（已锁定）

1. **RBAC 深度 = 完整动态（若依风格）**：菜单即路由 + 按钮权限，后端下发当前用户菜单树，前端运行时生成动态路由；角色↔菜单多对多授权；部门数据权限（data scope）真过滤业务数据。
2. **路由迁移 = 增量共存**：`config/routes.ts` 现有业务路由全保留不动；仅「系统管理」五页走 DB 动态路由，把整套菜单/权限/数据权限体系先在这套上跑通；现有业务页**后续**再逐步登记进菜单表（本期不迁）。
3. **数据权限 = 真接业务过滤**：被纳管业务表补 `dept_id`、按生效 data_scope 过滤读端点、写端点固化真实归属。**纳管实体**：`datasets`、`datasources`、`ingest_tasks`、`jobs`、`uploads`。**全局不纳管**：`categories`、`tags`、`llm_*`、算子目录、`audit_log`。
4. **权限语义 = 挂菜单（若依原生）**：按钮级权限是 `menus.perms` 串（如 `system:user:add`）；角色授权 = 勾选菜单/按钮；无独立 `permissions` 表。「权限管理」页 = 角色×权限授权总览 + 菜单权限标识维护（给菜单项一个落点，非一等实体）。
5. **会话机制不变**：保留现有 cookie（`adp_session`）+ HMAC 令牌（载 username）。仅扩展「加载什么」（roles/perms/data_scope），不换 JWT。
6. **`users.role` 列保留并继续填值**：现有 `require_admin`/`require_user` 被 14 文件 90 处调用，靠该列保持原样全通过；新权限层为叠加（`require_perm`），不动这 90 处。
7. **YAGNI**：不做若依的岗位/字典/参数/公告等其它模块；只做被点名的 5 项。

## 现状关键事实

- `User`：单 `role` 串（`admin|user`），PK `usr-{6hex}`；cookie+HMAC 令牌（`app/services/auth.py`），`require_admin` 按 `role=='admin'` 门控（`app/api/deps.py`）。
- 业务表已有部分归属：`datasets` 有 `owner`/`creator`/`last_modifier`（default `admin`，注释明写「无 RBAC 前默认 admin / #19 共享 ACL」）；`jobs` 有 `created_by`（default `admin`）。`datasources`/`ingest_tasks`/`uploads` 无归属列。
- 迁移：Alembic 顺序号，最新 `0018_dataset_tags`，下一个 `0019`。模型用字符串前缀主键、async SQLAlchemy（`Mapped`/`mapped_column`）。
- 前端：Ant Design Pro v6 / Umi 4；路由静态写死 `config/routes.ts`（50+ 路由 + 大量重定向）；菜单由静态路由派生；`access.ts` 仅 `canAdmin = access==='admin'`；`getInitialState` 拉 `currentUser`（`app.tsx`）。登录兼容层 `backend/app/api/compat.py` 提供 `/api/login/account`、`/api/currentUser`、`/api/login/outLogin`。
- 服务约定：`frontend/src/services/<域>/{api.ts,index.ts,typings.d.ts}`；管理页参照近期已落地的标签/分类 ProTable CRUD（`CategoryManager` 组件、`pages` 下标签管理）。

## §1 架构与组件

后端（`backend/app`）：
- 模型 `models/`：新增 `role.py`、`department.py`、`menu.py`、`user_role.py`、`role_menu.py`、`role_dept.py`；改 `user.py`（+`dept_id`）。
- schemas `schemas/system.py`（或拆 `role.py`/`dept.py`/`menu.py`/`user.py`）。
- 鉴权 `services/rbac.py`：perms 聚合、data_scope 解析、菜单树构建、`apply_data_scope`。
- 依赖 `api/deps.py`：加 `require_perm(code)`；`current_user` 不变。
- 路由 `api/v1/system/`：`users.py`、`roles.py`、`depts.py`、`menus.py`（含 `getRouters`）；`main.py` 注册。
- 迁移 `alembic/versions/0019_rbac_system.py`（建表+种子+回填）。

前端（`frontend/src`）：
- `services/system/{api.ts,typings.d.ts}`。
- `pages/system/{user,role,dept,menu,permission}/index.tsx` + 子组件（授权抽屉、菜单树等）。
- `access.ts` 扩 `hasPerm`；`app.tsx` 扩 `getInitialState`（拉 perms+routers）+ 运行时 `patchClientRoutes`；组件懒加载注册表 `routes/asyncRoutes.ts`（component 串→`@/pages/*` 映射）。

## §2 数据模型与迁移（`alembic/0019`）

主键沿用 `xxx-{6hex}`：`role-`/`dept-`/`menu-`。

- `roles`：`id, name, role_key(uniq), sort, data_scope ∈ {all,custom,dept,dept_and_child,self}, status(0正常/1停用), remark, created_at, updated_at`。
- `departments`：`id, parent_id(nullable), ancestors(逗号祖先 id 路径,根='0'), name, sort, leader, phone, email, status, created_at`。树。
- `menus`：`id, parent_id(nullable,根=null), name, menu_type ∈ {M目录,C菜单,F按钮}, path, component(C 用,组件串), perms(F/C 用,如 system:user:add), icon, sort, visible(0显/1隐), status, is_frame, query, created_at`。树。
- `user_roles`：`user_id, role_id`（PK 复合）。
- `role_menus`：`role_id, menu_id`（PK 复合）。
- `role_depts`：`role_id, dept_id`（PK 复合，仅 `custom` data_scope 用）。
- `users` 改：`+ dept_id(nullable)`；`role` 列保留。
- 业务表改：`datasets/datasources/ingest_tasks/jobs/uploads` 各 `+ dept_id(nullable)`；缺归属列的 `datasources/ingest_tasks/uploads` 补 `creator(default 'admin')`。

**种子（迁移内联）**：
- 角色：`超级管理员`（`role_key=admin, data_scope=all`，授全部菜单）、`普通用户`（`role_key=common, data_scope=self`，**不授**系统管理菜单——普通用户看不到系统管理，仅受 data_scope 限制访问业务数据）。
- 部门：根 `dept-000000`（`AI 数据平台`，`ancestors='0'`）。
- 菜单：`系统管理`(M) 下 `用户管理/角色管理/部门管理/菜单管理/权限管理`(C) + 各页按钮(F)：`system:{user,role,dept,menu}:{list,query,add,edit,remove}`、`system:user:resetPwd`、`system:role:authorize`、`system:menu:perms`。
- 用户映射：现有 `admin`→`超级管理员`；`user`→`普通用户`；`users.dept_id=dept-000000`。
- 回填：业务表 `dept_id=dept-000000`，`creator/created_by/owner` 空值置 `admin`。

## §3 鉴权内核（后端）

- `services/rbac.py`：
  - `get_user_roles(session, user) -> list[Role]`。
  - `get_user_perms(session, user) -> set[str]`：聚合所授菜单 `perms`；超管（含 `admin` 角色）返回 `{"*:*:*"}`。
  - `get_effective_data_scope(session, user) -> (scope, dept_ids)`：取多角色中最宽 scope；`custom` 汇总 `role_depts`；`dept_and_child` 解析部门子树（按 `ancestors` like）。
  - `build_router_tree(session, user) -> list[dict]`：当前用户有权的 `M/C` 菜单树（按 `role_menus`；超管全量），含 `path/name/component/icon/meta`，供前端建路由（不含 `F`）。
  - `apply_data_scope(stmt, user, model, scope_ctx)`：注入 WHERE —— `all` 不过滤 / `dept` `model.dept_id==user.dept_id` / `dept_and_child` `dept_id in 子树` / `self` `creator==user.id`（或 `created_by`，按表实际归属列）/ `custom` `dept_id in role_depts`。
- `api/deps.py`：`require_perm(code)` 工厂依赖 —— 未登录 401；无该 perm（且非 `*:*:*`）→403 `{success:false,message:"无权限"}`。`require_admin` 不变。
- 端点 `GET /api/v1/system/menus/routers`：返回 `build_router_tree`。
- `compat.py` `/currentUser` 载荷 `_current_user_payload` 扩 `roles:[role_key...]`、`permissions:[perms...]`（供前端 access）。

## §4 前端动态路由（增量共存）

- `app.tsx` `getInitialState`：成功取 `currentUser` 后并发拉 `permissions`（已在 currentUser 载荷）与 `getRouters`，存入 `initialState.dynamicRoutes`。
- 运行时 `patchClientRoutes({ routes })`：把 `dynamicRoutes` 转成路由对象**追加到布局路由**下；`component` 串经注册表 `routes/asyncRoutes.ts`（`{ 'system/user': () => import('@/pages/system/user') , ... }`）解析为懒加载元素；未命中串→404 占位（fail loud，console.warn）。
- `access.ts`：`hasPerm = (code) => perms.includes('*:*:*') || perms.includes(code)`；保留 `canAdmin`。
- 按钮级：`<Access accessible={access.hasPerm('system:user:add')}>` / `useAccess()`。
- 菜单：动态系统菜单与现有静态菜单并存（系统管理作为新顶级/末级分组注入）。

## §5 数据权限（真接业务过滤）

- 读端点（list）：`datasets/datasources/ingest_tasks/jobs/uploads` 的列表查询经 `apply_data_scope(stmt, user, Model, ctx)`；`ctx` 每请求解析一次（`get_effective_data_scope`）。
- 写端点（create）：归属列由 `current_user` 固化 —— `creator/created_by/owner=user.id`、`dept_id=user.dept_id`（取代硬编码 `'admin'`）。
- 详情/改/删：非 `all` scope 时校验目标行归属在可见集内，否则 404/403（防越权直取 id）。
- `dept_and_child` 子树：`departments.ancestors like '%,<deptId>,%'` 或递归收集；解析一次缓存到 `ctx`。

## §6 前端五页（`pages/system/*`，镜像标签/分类 ProTable）

- `user`：ProTable（用户名/昵称/部门/角色/状态/创建时间）+ 新建/编辑 Modal（分配角色多选 + 部门 TreeSelect）+ 重置密码 + 启停。`<Access>` 按 `system:user:*` 控按钮。
- `role`：ProTable + 编辑（基本信息 + data_scope 选择 + `custom` 时部门多选）+ 「分配菜单」抽屉（菜单树勾选 = `role_menus` 授权）。
- `dept`：树表 CRUD（parent TreeSelect、sort、leader、状态）。
- `menu`：树表 CRUD（`menu_type` M/C/F 联动显隐 path/component/perms/icon；sort/visible）。
- `permission`：角色×权限授权总览（只读矩阵 + 跳转角色授权）+ 菜单 `perms` 标识维护入口。给「权限管理」菜单项落点，非独立实体。
- 服务 `services/system/{api.ts,typings.d.ts}`：各模块 list/get/create/update/remove + `assignRoleMenus`、`assignUserRoles`、`getRouters`、`resetPwd`。

## §7 兼容与回填

- `users.role` 续填（admin/common→admin/user 映射保持），90 处 `require_admin` 零改动。
- 现有业务写入点：把硬编码 `creator='admin'`/`owner='admin'`/`created_by='admin'` 改为 `current_user.id`，并补 `dept_id`（需要这些端点能拿到 `current_user`；多数已注入鉴权依赖，缺的补依赖）。
- 迁移回填保证存量行 `dept_id=根部门`，存量 `creator/owner` 空值置 `admin`，不破坏现有列表。

## §8 测试（测意图）

后端 pytest：
- 数据权限：`self` 用户列表**绝不**含他人 `datasets/jobs/...`；`dept` 仅本部门；`dept_and_child` 含子部门；`all` 全量；`custom` 命中 `role_depts`。（每条编码「为什么」：越权不可见）
- 越权直取：非 `all` 用户按 id 取他人行→404/403。
- `require_perm`：无 perm→403；超管 `*:*:*` 通过；`require_admin` 兼容仍通过。
- 鉴权内核：多角色 perms 聚合、最宽 data_scope 选取、部门子树解析、菜单树按角色裁剪。
- 种子/回填：迁移后 admin 映射超管、存量行有根部门。

前端：
- `access.hasPerm` 通配与精确；`<Access>` 隐藏越权按钮。
- 菜单树→`patchClientRoutes` 动态路由可达；未命中 component 串告警不静默。
- 五页 CRUD 冒烟 + 角色授权抽屉提交。

## §9 边界与风险

- `dept_and_child` 用 `ancestors` 字符串匹配，删/移部门需同步重算后代 `ancestors`（dept 改父时级联更新）。
- 多角色 data_scope 取「最宽」可能放大可见域——明确语义，测试覆盖。
- 动态路由仅系统五页；现有业务路由不进 DB（增量共存），菜单合并需保证选中/展开态正常。
- 写端点归属固化依赖 `current_user`；逐个端点核对依赖注入，缺失补齐（fail loud，勿默默回退 admin）。
- 数据权限是跨切面改动（P3），按实体逐个接入并各自测试，避免一次性大改回归。

## §10 Workflow 拆分（按依赖排序，每阶段可独立交付/可独立成 plan 任务）

| 阶段 | 内容 | 模型/agent |
|---|---|---|
| **P0 地基** | `0019` 迁移（5+2 表 + `users.dept_id` + 业务表 `dept_id` + 种子 + 回填）；ORM 模型 + schemas | 后端 |
| **P1 鉴权内核** | `services/rbac.py`（perms/data_scope/菜单树/`apply_data_scope`）；`require_perm`；`getRouters`；扩 `currentUser`；兼容测试 | 后端 |
| **P2 五模块 CRUD** | `system/{users,roles,depts,menus}` CRUD + 授权端点（role-menu、user-role、role-dept）+ 权限总览 | 后端 |
| **P3 数据权限接业务** | 业务表归属固化 + `apply_data_scope` 接入 5 实体读/写/详情端点；逐实体测试 | 后端（跨切面） |
| **P4 前端路由内核** | `getInitialState` 拉 perms/routers；`patchClientRoutes`；`asyncRoutes` 注册表；`access.hasPerm`；菜单合并 | 前端 |
| **P5 前端五页** | `system` 五页 + `services/system` + typings | 前端 |
| **P6 联调/文档** | 真后端联调、`docs/` 登记、菜单核对、e2e 冒烟 | 全栈 |
