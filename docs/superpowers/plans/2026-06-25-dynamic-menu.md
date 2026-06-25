# 全菜单统一管理(动态菜单)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。Steps 用 `- [ ]` 勾选。

**Goal:** 让「菜单管理」名副其实地管全部菜单——侧边栏菜单由后端 `menus` 表驱动,菜单管理页可对全部菜单做增删改/排序/显隐/按角色授权。

**Architecture(推荐 B1「动态菜单」而非 B2「patchClientRoutes 全动态路由」):**
- **路由**仍由 `config/routes.ts` 静态定义(零路由风险,所有现有页面/编辑器/参数路由/重定向原样工作)。
- **侧边栏菜单**改由后端 `getRouters` 下发的菜单树驱动,经 ProLayout `menuDataRender` 注入。菜单管理页编辑 `menus` 表 ⇒ 侧边栏随之变。
- 这样"菜单统一管理"目标达成,而不用重写路由体系(B2 才需要的 `patchClientRoutes` + 组件懒加载注册表 + 路由级回归,风险最高,且因页面组件必须存在代码里,B2 相对 B1 仅多得"可从 UI 加新路由路径"这点边际能力,不值当)。

**Tech Stack:** FastAPI/SQLAlchemy/Alembic(后端)、Umi Max + @ant-design/pro-components(前端 `menuDataRender`)。

## Global Constraints

- 只把**可见菜单**(M 目录 + C 叶子,非 `hideInMenu`)入表;`hideInMenu` 的编辑器/详情/参数路由(如 `/datasets/:id`、`/governance/processing/editor`、`/ingest/datasources/new/:type`)继续只在 `routes.ts`,不进表、不出现在侧边栏(与今天一致)。
- 重定向(旧路径兼容,~15 条)只在 `routes.ts`,不进表。
- 菜单 `name` 字段直接存中文(侧边栏直接用,不依赖 i18n key)。en-US 暂回退中文(MVP)。
- `path` 必须与 `routes.ts` 完全一致(点击菜单靠 path 路由)。
- 超管(`role_key=admin`)看全部(getRouters 已实现);**普通用户(`role-000002`)回填授予全部业务菜单**(保现有"全员可见")。`/ops/security`、`/ops/llm-settings` 当前是 `access:'canAdmin'`,新模型里**只授给超管角色、不授普通用户**(语义保持)。
- 图标:后端存图标字符串(如 'api'/'database');`menuDataRender` 端把字符串映射到 antd 图标(运行时静态 routes 的字符串图标由 Umi 构建期解析,运行时注入需自带映射表——**关键验证点**)。

## File Structure

- Create `backend/alembic/versions/0021_seed_business_menus.py` — 种子全部业务菜单 + 给普通用户授权业务菜单。
- Modify `frontend/src/app.tsx` — `getInitialState` 拉 `getRouters`;`layout.menuDataRender` 用后端菜单树渲染侧边栏。
- Create `frontend/src/utils/menuIcons.ts` — 图标字符串→antd 图标组件映射表。
- Modify `frontend/src/services/system/api.ts` — `getRouters`(已存在端点,补前端调用)。
- `config/routes.ts` **不动**(路由照旧)。
- Tests:`backend/tests/test_system_routers.py` 扩展(普通用户/超管菜单可见集);前端无单测,靠联调冒烟。

---

### Task 1: 迁移 0021 — 种子业务菜单 + 授权

**Produces:** `menus` 表新增 ~30 行业务菜单(M/C,中文 name、path、component、icon、sort、parent_id);`role_menus` 给普通用户授全部业务 M/C(不含 security/llm-settings)。

菜单结构(摘自 `routes.ts`):
- 顶级:`/operators`(算子市场,C,icon block,sort 1)、`/ingest`(数据接入,M,api,10)、`/datasets`(数据集仓库,M,database,20)、`/assessment`(数据评估,M,audit,30)、`/governance`(数据治理,M,safety,40)、`/ops`(运维监控,M,dashboard,50)、`/assistant`(智能助手,C,robot,70)。`/system`(sort 90)已存在。
- `/ingest` 下 C:datasources(数据源)/tasks(接入任务)/local-upload(本地上传)/files(文件管理)。
- `/datasets` 下 C:list(数据集)/presets(预设)/categories(分类)/tags(标签)。
- `/assessment` 下 C:quality(质量评估)。
- `/governance` 下 C:content-safety(内容安全)/cleaning(清洗)/distillation(蒸馏)/make(合成)/augment(增强)/annotation(标注)。
- `/ops` 下 C:data-tasks(数据任务)/lineage(血缘)/security(安全审计,仅超管)/llm-settings(LLM 配置,仅超管)。

- [ ] **1.1** 写 0021 迁移:`_seed_business_menu_rows()` 返回上述 ~30 行(顶级 parent_id=null,M 目录先建、C 叶子 parent_id 指向所属 M);固定 id(如 `menu-100001`…);`bulk_insert` menus。再 `bulk_insert` role_menus:普通用户(role-000002)← 全部刚种的业务 M/C(**排除** security/llm-settings 两条)。幂等(NOT EXISTS 防重)。
- [ ] **1.2** 开发库 `alembic upgrade head`;校验:`select count(*) from menus`(应 ≈ 26+30=56)、普通用户经 getRouters 可见的业务菜单数。
- [ ] **1.3** 后端测试扩展:`test_system_routers.py` 加——超管 getRouters 含 `/ingest`、`/datasets` 等业务目录;普通用户(u-staff 类)经 role_menus 也能看到业务菜单;未授权角色看不到。
- [ ] **1.4 跑测试→通过;提交**

### Task 2: 前端 menuDataRender + 图标映射 + getRouters 服务

- [ ] **2.1** `services/system/api.ts` 加 `getRouters()`(`GET /api/v1/system/menus/routers`,返回 `{data: RouterNode[], success}`;`RouterNode{id,name,path,component?,icon?,children?}` 加到 typings)。
- [ ] **2.2** `utils/menuIcons.ts`:导出 `iconMap: Record<string, ReactNode>` 覆盖 routes 用到的图标(api/database/audit/safety/dashboard/robot/setting/block + 常用);`resolveIcon(name)` 返回组件或 `undefined`。
- [ ] **2.3** `app.tsx`:`getInitialState` 在已有 `fetchUserInfo` 后并发拉 `getRouters` → 存入 `initialState.routers`(未登录页跳过)。`layout` 配置加 `menuDataRender: () => toMenuData(initialState?.routers ?? [])`,其中 `toMenuData` 把后端树转成 `{path,name,icon:resolveIcon(),children}`(`name` 直接用后端中文名)。
- [ ] **2.4** 验证图标字符串在运行时能渲染(关键风险点):若 `menuDataRender` 返回的字符串图标不显示,改用 `resolveIcon` 返回组件。
- [ ] **2.5 联调冒烟**:admin 登录 → 侧边栏出现全部业务菜单 + 系统管理,点各菜单路由正常;普通用户登录 → 看到业务菜单、看不到系统管理/security/llm-settings。
- [ ] **2.6 提交**

### Task 3: 菜单管理页验证 + 文档

- [ ] **3.1** 菜单管理页现在显示全部 ~56 条菜单树;新建/编辑/排序/显隐对侧边栏生效(改 visible→刷新侧边栏消失;改 sort→侧边栏重排)。
- [ ] **3.2** 角色管理「分配菜单」可勾选业务菜单给自定义角色 → 该角色登录侧边栏只看到勾选项。
- [ ] **3.3** `docs/` 登记:动态菜单架构说明 + "新增页面"流程(① routes.ts 加路由;② menus 表/菜单管理页加菜单行)。
- [ ] **3.4 全量回归**:后端 system 测试 + 前端 tsc/biome;提交。

---

## Self-Review

- **目标覆盖:** 菜单管理管全部菜单(侧边栏由表驱动)⇒ Task 1–3 ✓。
- **范围/风险:** 路由不动 ⇒ 现有页面/编辑器/重定向零回归;唯一新风险是 `menuDataRender` 运行时图标渲染(Task 2.4 验证)。比 B2(patchClientRoutes)安全得多。
- **保留:** getRouters/require_perm/角色授权机制复用;`hideInMenu` 路由继续 routing-only。
- **权衡已注明:** 新增"页面"仍需改 routes.ts(组件必须存在代码里),这与 B2 的边际差异在文档说清。

## 执行说明

按 Task 1→3 顺序。Task 1(后端种子)可独立先交付验证;Task 2 是前端核心(联调冒烟为验收);Task 3 收尾。建议 Task 1 完成后先让你看一眼种子菜单结构再继续 Task 2。
