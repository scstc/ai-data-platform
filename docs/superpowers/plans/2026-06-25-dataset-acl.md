# 数据集级 ACL(共享/成员权限)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。Steps 用 `- [ ]` 勾选。

**Goal:** 给数据集加细粒度 ACL——单个数据集可向 用户/角色 授 查看/编辑/管理 级别;新建数据集默认私有(owner+超管可见);匿名沿用现状(看全部,生产无匿名)。

**Architecture:** 新增 `dataset_acl` 表 + `services/dataset_acl.py`(可见性过滤/级别判定,纯函数+只读查询)。`datasets.py` 外科手术式接入:list/detail 注入 optional current_user 并按 ACL 过滤;上传端点 owner 固化为当前用户;PATCH 改 ACL 判定、DELETE 收紧为 owner/超管;新增 `/datasets/{id}/acl` CRUD。独立于 P1 的角色 data_scope。

**Tech Stack:** FastAPI、SQLAlchemy 2.0 async、Pydantic、Alembic、pytest。

## Global Constraints

- 主键 `dac-<6hex>`;唯一约束 `(dataset_id, subject_type, subject_id)`。
- `subject_type ∈ {user, role}`;`level ∈ {view, edit, admin}`,级别序 view<edit<admin。
- **超管判定**沿用 `user.role=='admin'`(与 P1 通配一致)⇒ 绕过 ACL,全权。
- **owner** = `dataset.owner==user.id` 或 `dataset.creator==user.id` ⇒ 隐式 admin 级,可删。
- **匿名(user is None)沿用现状**:list 不过滤、detail 不拦截、上传 owner 落 `'admin'`。生产无匿名,故不构成新暴露;显式注释,不藏。
- **存量回填**(0020):每条现存数据集插 `(dataset_id,'role',role-000002[普通用户],'view')`,保现有普通用户可见;新数据集不回填(私有)。
- 不改 P1 的 `require_admin`/`require_user`/`current_user`;PATCH/DELETE 改用 handler 内 ACL 判定(替代 router 级 `require_admin` 依赖)。
- 测试库 create_all ⇒ 新模型注册进 `models/__init__.py`;远程库慢,窄跑。

## 关键事实(已读代码确认)

- 无 `test_datasets.py`;数据集端点由 test_data_access(33)/publish_gate(13)/external_store(8)/quality_api(7)/dataset_export(7)/categories(6) 覆盖,且**多为匿名调用** ⇒ 匿名兼容策略保证零回归。
- 上传端点 3 个:`/datasets/upload`、`/datasets/upload-media`、`/datasets/upload-batch`;另 `land_upload`/`land_upload_raw`(services/landing)内也设 owner/creator,需同步。
- `Dataset` 已有 `owner`/`creator`/`last_modifier` 列;`list_datasets`/`get_dataset` 现无鉴权依赖。

## File Structure

- Create `backend/app/models/dataset_acl.py`(`DatasetAcl`)、注册进 `models/__init__.py`。
- Create `backend/app/services/dataset_acl.py`(`visible_dataset_filter`/`get_acl_level`/`can_access`)。
- Create `backend/app/schemas/dataset_acl.py`(`AclEntry*` schemas)。
- Create `backend/alembic/versions/0020_dataset_acl.py`。
- Modify `backend/app/api/v1/datasets.py`(list/detail/upload-owner/PATCH/DELETE/ACL 端点)。
- Modify `backend/app/services/landing.py`(owner/creator 用传入的 actor,默认 'admin')。
- Tests:`backend/tests/test_dataset_acl.py`。

---

### Task 1: DatasetAcl 模型 + 0020 迁移 + 回填

**Produces:** `DatasetAcl(id, dataset_id, subject_type, subject_id, level, created_at)`,唯一 `(dataset_id,subject_type,subject_id)`;0020 建表 + 给现存数据集回填普通用户 view。

- [ ] **1.1 写失败测试** `test_dataset_acl.py::test_model_roundtrip`(建表+插入+查回;含唯一约束重复抛错)
- [ ] **1.2 跑→失败**(`ModuleNotFoundError`)
- [ ] **1.3 写 `models/dataset_acl.py`** + 注册 `models/__init__.py`
- [ ] **1.4 写 0020 迁移**(建表 + `UPDATE`/`INSERT SELECT` 回填:每现存 dataset 插 common-role view 行,用 `NOT EXISTS` 防重)
- [ ] **1.5 开发库 upgrade + 校验**(回填行数 == 现存 dataset 数)
- [ ] **1.6 跑测试→通过;提交**

### Task 2: ACL 服务层(可见性过滤/级别判定)

**Produces:** `services/dataset_acl.py`:
- `visible_dataset_filter(stmt, user)`:user None 或 admin ⇒ 不过滤;否则 `owner==uid OR creator==uid OR id ∈ {可见 dataset 子查询}`(子查询 = user 直授 + user 角色授权的 dataset_id 集)。
- `get_acl_level(session, user, dataset_id) -> 'admin'|'edit'|'view'|None`:admin/owner⇒admin;否则取直授 + 角色授中的最高级;无⇒None。
- `can_access(session, user, dataset_id, required) -> bool`:级别序比较;**user 为 None ⇒ True**(匿名兼容,只在登录态精细判定)。
- `_RANK = {'view':1,'edit':2,'admin':3}`。

- [ ] **2.1 写失败测试**(用 seed_rbac 造数:u-mgr 建私有 dataset ⇒ u-staff `visible_dataset_filter` 后看不到;授 view 后看到;get_acl_level 各档;can_access 匿名 True)
- [ ] **2.2 跑→失败**
- [ ] **2.3 写 `services/dataset_acl.py`**
- [ ] **2.4 跑→通过;提交**

### Task 3: list/detail 接入可见性 + 上传 owner 固化

**接入点(datasets.py):**
- `list_datasets`:增 optional `current_user` 依赖;`stmt = visible_dataset_filter(stmt, user)`。
- `get_dataset`:取 dataset 后,若 `not can_access(user, dataset, view)` ⇒ 404(user None 放行)。
- 三个上传端点:`actor = user.id if user else 'admin'`;owner/creator=actor;`land_upload`/`land_upload_raw` 传 actor(签名加 `actor: str='admin'`,默认值兼容现有调用)。

- [ ] **3.1 写 API 测试**:admin 看 all;u-mgr 上传的私有集,u-staff 列表看不到、直取 404;授 view 后看到;匿名列表照常全见。
- [ ] **3.2 跑→失败**
- [ ] **3.3 改 datasets.py + landing.py**
- [ ] **3.4 跑新测试 + 回归**(`test_data_access.py` 匿名路径全绿)
- [ ] **3.5 提交**

### Task 4: PATCH/DELETE 门控改 ACL + ACL CRUD 端点

**接入点:**
- `update_dataset`(PATCH):去 router 级 `require_admin`,handler 内 `can_access(user, ds, edit)` 否则 403(user None ⇒ 放行兼容)。
- `delete_dataset`(DELETE):去 `require_admin`,改 `owner or admin` 否则 403。
- 新 `GET/POST/PUT/DELETE /datasets/{id}/acl`:需 `can_access(user, ds, admin)`(owner/超管/ACL-admin);CRUD `DatasetAcl` 行。

- [ ] **4.1 写 API 测试**:owner/超管能 PATCH+DELETE+管 ACL;ACL-edit 能 PATCH 不能管 ACL/不能删;ACL-admin 能管 ACL 不能删;越权 403;匿名放行(兼容)。
- [ ] **4.2 跑→失败**
- [ ] **4.3 写 `schemas/dataset_acl.py` + 改 datasets.py(4 个 ACL 端点 + PATCH/DELETE 门控)**
- [ ] **4.4 跑新测试**
- [ ] **4.5 全量回归**:`test_dataset_acl + test_data_access + test_rbac + test_publish_gate + test_quality_api`(分批)
- [ ] **4.6 提交**

---

## Self-Review

- **Spec 覆盖**:数据集级 ACL(用户/角色 × view/edit/admin)+ 私有默认 + 回填 + list/detail/上传/PATCH/DELETE/ACL 端点 ⇒ Task 1–4 ✓。
- **匿名兼容**:显式决策(user None ⇒ 不过滤/放行/owner='admin'),零测试回归;生产无匿名。已注释+提交说明标注。
- **范围外**:其它实体(datasources/jobs/…)的数据权限(角色 data_scope)留待后续;本期只数据集 ACL。
- **类型一致**:level 枚举/`_RANK`/`can_access` 签名跨任务一致;`actor` 默认 'admin' 兼容 landing 现有调用。
