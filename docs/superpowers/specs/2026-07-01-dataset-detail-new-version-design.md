# 数据集详情页 · 新建版本按钮 — 设计

- **日期**:2026-07-01
- **分支**:`feature/governance-remediation-plan`
- **状态**:已批准,待写实现计划

## 1. 背景

数据集详情页(`frontend/src/pages/datasets/detail/index.tsx`)的「版本（N）」区块目前只能**查看**已有版本;新版本只能作为上传/采集动作的副作用间接产生(`landing._target_draft_version`:最新版本是 draft 则复用,是 published 则自动开 v+1 draft 并克隆上一版成员)。

需求:在版本列表标题右上角加一个「新建版本」按钮,点击即在该数据集下**显式**新建一个版本,并在 MinIO 对应数据集目录下出现一个新的版本文件夹。

## 2. 决策

- **永远新建**,不复用/合并现有 draft——这是用户的显式操作,与 `_target_draft_version`(上传时的隐式选取逻辑)是两条独立路径,互不影响、互不复用。
- 新版本**为空**:不克隆上一版本的表成员,`version_no = max(existing)+1`(无版本则为 1),`publish_status="draft"`,`storage_uri="pending://{dataset_id}/v{n}/"`(与 `_target_draft_version` 建空 draft 时的占位约定一致)。
- 为让 `v{n}/` 目录在「文件管理」页立即可见(S3 无空目录概念,靠公共前缀 + 至少一个对象体现),同步写一个空占位对象 `{dataset_id}/v{n}/.keep` 到 uploads 桶。
- 权限:与「编辑元数据」「本地上传」同级——`dataset_acl.can_access(session, user, dataset_id, "edit")`(owner/超管/ACL edit+;匿名放行,与仓库既有约定一致)。

## 3. 后端

新增 `POST /api/v1/datasets/{dataset_id}/versions`(`backend/app/api/v1/datasets.py`,置于 publish/unpublish 端点附近的「版本级」分组下):

1. 404:数据集不存在。
2. 403:`can_access(..., "edit")` 为 False。
3. 查 `max(DatasetVersion.version_no)`,得 `next_no`。
4. 建 `DatasetVersion(id=_new_version_id(), dataset_id, version_no=next_no, storage_uri=f"pending://{dataset_id}/v{next_no}/", format="jsonl", origin="managed", publish_status="draft")`,`session.add` + `commit` + `refresh`。
5. `platform_config()` 取 MinIO 配置,`upload_object(cfg, bucket=settings.storage_minio_upload_bucket, key=f"{dataset_id}/v{next_no}/.keep", io.BytesIO(b""), 0)` 写占位对象(复用 `upload_batch_as_dataset` 里同款 helper)。占位对象写入失败不回滚版本创建(纯展示性 best-effort,吞异常,不阻断主流程)。
6. 返回 `_version_item(version)`(与 publish/unpublish 同款响应 `{data, success}`)。

不改 `DatasetVersion` 模型、不加迁移——空版本是模型已支持的既有形态(`_target_draft_version` 建空 draft 分支已在生产路径跑过)。

## 4. 前端

- `frontend/src/services/data-platform/api.ts`:新增 `createDatasetVersion(datasetId: string)`,POST 到 `/api/v1/datasets/${datasetId}/versions`,返回类型对齐 `publishVersion` 的写法(`{ data: DataPlatform.DatasetVersion; success: boolean }`)。
- `frontend/src/pages/datasets/detail/index.tsx`:
  - 「版本（{count}）」`Typography.Title` 改为一行 flex 布局,右侧加「新建版本」`Button`(仅当 `detail.myLevel === 'edit' || detail.myLevel === 'admin'` 时渲染,对齐现有「编辑」按钮用 `myLevel`/`access` 判权的写法)。
  - `onClick`:调用 `createDatasetVersion(id)` → 成功后 `message.success('已新建版本')` → `reloadDetail()` → `loadPreview(res.data.id)`(把新版本设为当前选中,复用现有 `loadPreview`)。失败 `message.error`。

## 5. 不做的事

- 不克隆上一版本成员(与「上传时自动续接」的隐式行为区分开,显式按钮=纯新建)。
- 不做「已有 draft 时禁用/提示」的特殊分支——按钮永远可点,永远新建,行为可预测,不引入额外状态判断。
- 不改动 `_target_draft_version` 及其调用方(上传/采集流程不受影响)。
