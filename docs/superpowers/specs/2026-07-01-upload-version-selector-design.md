# 本地上传 · 目标版本选择器 — 设计

- **日期**:2026-07-01
- **分支**:`feature/governance-remediation-plan`
- **状态**:已批准,待写实现计划

## 1. 背景

本地上传(`frontend/src/pages/ingest/local-upload/single.tsx` 单一数据接入、`ImportCard.tsx` 场景数据/多模态)选好目标数据集后,直接提交上传;上传落到哪个版本完全由后端隐式规则决定:

- 媒体接入(`/upload-media`):查该数据集**任意一条** `publish_status="draft"` 的版本(按 `version_no` 取最大),有则复用,没有则新建。
- 普通/raw 批量(`/upload-batch` → `landing._target_draft_version`):看**版本号最大的那一条**——是 draft 则复用,是 published(冻结)则自动新建下一版本。

问题:用户看不到、也管不了这次上传会落到哪个版本,只能等上传完去详情页反查。已发布版本一旦存在,后续上传会静默新建版本,用户容易困惑("怎么又建版本了")。

需求:选好数据集后,加一个**版本下拉选择框**,显式选择本次上传落到"现有草稿"还是"新建版本"。

## 2. 决策

- 下拉框最多两个选项:
  - `现有草稿 v{n}`——仅当该数据集**存在** `publish_status="draft"` 的版本时出现,默认选中。
  - `新建版本`——始终存在;没有草稿时默认选中(此时是唯一选项)。
- 已发布/已下架的版本**不出现在下拉框里**——它们已冻结,写不进去,不构成一个可选项,不是"禁用态选项"。
- 这次不引入"选择某个具体历史草稿"的语义——设计前提(`_target_draft_version` 现有行为)决定了一个数据集任意时刻**至多一条**草稿版本,所以下拉框本质是"复用 vs 新建"的二选一,不是多草稿列表。
- 选中值只影响**是否强制新建**;不强制新建时,后端仍按现有规则复用当前草稿(不用把"选中的具体版本 id"传给后端,只传一个布尔标志,避免和后端并发状态产生竞态:选择框渲染时看到的草稿 id,到提交那一刻可能已被别的操作(如发布)改变——传布尔意图而不是快照 id,让后端按提交那一刻的真实状态决策)。

## 3. 后端(`backend/`)

1. `app/services/landing.py`:`_target_draft_version(session, dataset_id, *, force_new: bool = False)`——`force_new=True` 时跳过"最新版本是否 draft"的判断,直接走现有的"新建下一版本"分支(与"无任何版本"分支同一套逻辑:`version_no = max+1`,`storage_uri=f"pending://{dataset_id}/v{n}/"`,`format="jsonl"`,`publish_status="draft"`)。
   - `add_table_member(...)`(约 L710)、`add_raw_batch(...)`(约 L839)各加一个 `force_new: bool = False` 形参,原样转给 `_target_draft_version`。
2. `app/api/v1/datasets.py`:
   - `upload_batch_as_dataset`(`/upload-batch`,约 L669)新增 `force_new_version: Annotated[bool, Form(alias="forceNewVersion")] = False`,按 `raw` 分支分别传给 `add_raw_batch(..., force_new=force_new_version)` / `add_table_member(..., force_new=force_new_version)`。
   - `upload_media_as_dataset`(`/upload-media`,约 L447)新增同名 Form 字段;为 `True` 时,直接跳过"查 `publish_status="draft"` 的 `existing_draft`"那段查询(视为 `existing_draft = None`),走既有的"新建版本"分支(`version = None` → 下方按 `max_no+1` 新建)。
3. 不改 `getDataset` 返回结构——`versions[].publishStatus` / `versionNo` 已够前端判断"有没有草稿、草稿是第几版"。
4. 不改 `DatasetVersion` 模型、不加迁移。

## 4. 前端(`frontend/src/pages/ingest/local-upload/`)

新增 `VersionTargetSelect.tsx`(与 `single.tsx`/`ImportCard.tsx` 同级,两处复用):

- Props:`datasetId?: string`、`value: boolean`(是否新建)、`onChange(forceNewVersion: boolean): void`。
- `datasetId` 变化时调 `getDataset(datasetId)`,取 `versions` 数组里 `publishStatus === 'draft'` 的那条(至多一条,不存在则为 `undefined`)。
- 渲染一个 `Select`:
  - 有草稿:选项 `[{ label: '现有草稿 v{versionNo}', value: 'existing' }, { label: '新建版本', value: 'new' }]`,默认值 `'existing'`。
  - 无草稿:只有 `[{ label: '新建版本', value: 'new' }]`,值锁定 `'new'`(禁用态,避免用户以为还能选别的)。
  - `datasetId` 未选中时:整个组件不渲染(和现有"目标数据集"必选校验顺序一致,数据集选完才谈版本)。
  - `onChange` 把 `value === 'new'` 换算成布尔值上抛。
- 数据集切换时重置为默认值(有草稿→`false`,无草稿→`true`),避免残留上一个数据集的选择。

`single.tsx` / `ImportCard.tsx`:

- 各自加一个 `forceNewVersion` state,在"目标数据集" `Select` 下方渲染 `<VersionTargetSelect datasetId={datasetId} value={forceNewVersion} onChange={setForceNewVersion} />`。
- `onSubmit` 里,`fd.append('force_new_version', String(forceNewVersion))`——`single.tsx` 媒体分支和 raw 分支都加;`ImportCard.tsx` 媒体分支和普通分支都加。
- 不改 `services/data-platform/api.ts`——`uploadMediaDataset`/`uploadBatchDataset` 是手写的 `FormData` 直传函数(非 OpenAPI 生成),不用碰,也不用跑 `npm run openapi`。

## 5. 不做的事

- 不支持"选一个具体的历史草稿"——设计前提下同一时刻至多一条草稿,没有这个场景。
- 不在选择框里展示已发布版本(哪怕是灰态/禁用态)——它们不是这次上传的合法目标,列出来只会引发"为什么不能选"的疑问。
- 不改 `_target_draft_version` 的默认行为(`force_new=False` 时和现在完全一样)——只加一个显式覆盖开关,不动现有隐式规则。
- 不处理"提交那一刻版本已被别人发布"的竞态提示——`force_new=False` 时后端按提交时的真实状态走既有逻辑(该新建就新建),前端不用额外处理,不引入乐观锁/刷新提示之类的复杂度。
