# 动态菜单(RBAC)架构说明

- 日期：2026-06-25
- 状态：已实现（B1 方案：动态菜单 + 静态路由）

## 一句话

侧边栏菜单由后端 `menus` 表驱动（经 `GET /api/v1/system/menus/routers` 按角色授权裁剪），前端 `menuDataRender` 注入；**路由仍由 `frontend/config/routes.ts` 静态定义**。菜单管理页编辑 `menus` 表 ⇒ 侧边栏随之变；按角色授权菜单 ⇒ 不同角色看到不同菜单。

## 两个"事实源"（务必分清）

| 关注点 | 来源 | 改动方式 |
|---|---|---|
| **路由**（URL → 页面组件） | `frontend/config/routes.ts`（静态） | 改代码 |
| **侧边栏菜单**（显哪些、顺序、图标、按角色可见） | DB `menus` 表（动态） | 菜单管理页 / 角色-分配菜单 |

> 这是 B1（动态菜单）的刻意取舍：路由保持静态 = 零路由回归风险；侧边栏动态 = 菜单统一管理 + 按角色控可见。两者通过 `path` 对齐：菜单项的 `path` 必须与 `routes.ts` 里某条路由的 `path` 一致，点击菜单才能跳转。

## 哪些进 `menus` 表

- **进表**：可见菜单 = M 目录 + C 叶子（如 `/ingest`、`/ingest/datasources`）。
- **不进表**（只在 `routes.ts`，路由用、不出现在侧边栏）：
  - `hideInMenu` 的编辑器/详情/参数路由（如 `/datasets/:id`、`/governance/processing/editor`、`/ingest/datasources/new/:type`）；
  - 旧路径兼容重定向（如 `/files` → `/ingest/files`）；
  - `layout: false` 的 login / 404。

## 按角色控可见

`getRouters` 只返回当前用户所属角色（`user_roles`）被授权（`role_menus`）的 M/C 菜单。因此：

- 超管（`role_key=admin`）→ 全部（`getRouters` 对 admin 通配，不查 `role_menus`）；
- 普通用户（`role-000002`）→ 种子授权的业务菜单（不含系统管理 / 安全审计 / LLM 配置）；
- 自定义角色 → 仅「角色管理 → 分配菜单」勾选的项。

按钮级权限另走 `permissions`（`/api/currentUser` 下发，`access.hasPerm(code)` 门控），与菜单可见性正交。

## 新增一个页面（三步）

1. **写组件**：在 `frontend/src/pages/<域>/<页>/index.tsx` 写页面组件。
2. **加路由**：在 `frontend/config/routes.ts` 加一条 `{ path, name, component }`（路由注册）。
3. **加菜单**（要让它在侧边栏出现 / 按角色控可见时）：菜单管理页新建一条菜单（`path` 与上一步一致，`component` 填去掉 `./` 的串如 `datasets/list`），再在「角色管理 → 分配菜单」勾给目标角色。

> 跳过第 3 步 ⇒ 页面可达（URL 直接进）但不进侧边栏（适合详情/编辑器这类按需跳转页）。

## 图标

后端 `menus.icon` 存字符串（如 `api`/`database`）。静态路由的字符串图标由 Umi 构建期解析；`menuDataRender` 运行时注入的菜单不走构建期，故 `frontend/src/utils/menuIcons.tsx` 维护一份 `字符串 → antd 图标组件` 映射表。**新增图标**：在该文件的 `ICON_MAP` 加一行。

## 相关文件

- 后端：`app/services/rbac.py`（`build_router_tree`）、`app/api/v1/system/menus.py`（`getRouters`）、迁移 `0019`（系统菜单）/ `0021`（业务菜单）。
- 前端：`src/app.tsx`（`getInitialState` 拉 routers + `layout.menuDataRender`）、`src/utils/menuIcons.tsx`、`src/services/system/api.ts`（`getRouters`）。
- 设计/计划：`docs/superpowers/specs/2026-06-25-rbac-system-management-design.md`、`docs/superpowers/plans/2026-06-25-dynamic-menu.md`。
