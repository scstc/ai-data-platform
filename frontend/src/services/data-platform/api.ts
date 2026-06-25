// @ts-ignore
/* eslint-disable */
import { request } from '@umijs/max';

/** 分类列表（所有登录用户）GET /api/v1/categories（#15） */
export async function listCategories(options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.Category[]; success: boolean }>(
    '/api/v1/categories',
    { method: 'GET', ...(options || {}) },
  );
}

/** 全部标签（管理页列表 / 自由输入联想）GET /api/v1/tags */
export async function listTags(options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.Tag[]; success: boolean }>(
    '/api/v1/tags',
    { method: 'GET', ...(options || {}) },
  );
}

/** 新建标签（admin;同名 find-or-create 返回已存在项）POST /api/v1/tags */
export async function createTag(
  body: DataPlatform.TagCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Tag; success: boolean }>(
    '/api/v1/tags',
    { method: 'POST', data: body, ...(options || {}) },
  );
}

/** 重命名标签（admin;重名 409）PATCH /api/v1/tags/{id} */
export async function updateTag(
  id: string,
  body: DataPlatform.TagUpdate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Tag; success: boolean }>(
    `/api/v1/tags/${id}`,
    { method: 'PATCH', data: body, ...(options || {}) },
  );
}

/** 删除标签 + 级联解绑（admin）DELETE /api/v1/tags/{id} */
export async function deleteTag(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(
    `/api/v1/tags/${id}`,
    { method: 'DELETE', ...(options || {}) },
  );
}

/** 批量删除标签 + 级联（admin）DELETE /api/v1/tags */
export async function batchDeleteTags(
  body: DataPlatform.TagBatchDelete,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    '/api/v1/tags',
    { method: 'DELETE', data: body, ...(options || {}) },
  );
}

/** 合并标签 source→target（去重 + 删源，admin）POST /api/v1/tags/merge */
export async function mergeTags(
  body: DataPlatform.TagMerge,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    '/api/v1/tags/merge',
    { method: 'POST', data: body, ...(options || {}) },
  );
}

/** 新建分类（仅 admin；重名 409）POST /api/v1/categories（#15） */
export async function createCategory(
  body: DataPlatform.CategoryCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Category; success: boolean }>(
    '/api/v1/categories',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      // skipErrorHandler:交由调用方 catch 展示后端重名 message
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/** 更新分类（仅 admin；改名撞名 409）PATCH /api/v1/categories/{id}（#15） */
export async function updateCategory(
  id: string,
  body: DataPlatform.CategoryUpdate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Category; success: boolean }>(
    `/api/v1/categories/${id}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      // skipErrorHandler:交由调用方 catch 展示后端重名 message
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/** 删除分类（仅 admin；被引用返回 409 + message）DELETE /api/v1/categories/{id}（#15） */
export async function deleteCategory(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/categories/${id}`, {
    method: 'DELETE',
    // skipErrorHandler:交由调用方 catch 展示后端「正被 N 处引用」message
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/** 获取数据源列表 GET /api/v1/datasources */
export async function listDataSources(
  params?: DataPlatform.DataSourceListParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.DataSource>>('/api/v1/datasources', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  });
}

/** 新建数据源 POST /api/v1/datasources */
export async function createDataSource(
  body: DataPlatform.DataSourceCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DataSource; success: boolean }>('/api/v1/datasources', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/** 更新数据源 PUT /api/v1/datasources/:id */
export async function updateDataSource(
  id: string,
  body: DataPlatform.DataSourceUpdate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DataSource; success: boolean }>(
    `/api/v1/datasources/${id}`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      ...(options || {}),
    },
  );
}

/** 删除数据源 DELETE /api/v1/datasources/:id */
export async function deleteDataSource(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/datasources/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

/** 测试数据源连接 POST /api/v1/datasources/test */
export async function testDataSource(
  body: DataPlatform.TestConnectionParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.TestConnectionResult>('/api/v1/datasources/test', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/** 列出数据源库内的表 GET /api/v1/datasources/{id}/tables */
export async function listDatasourceTables(
  id: string,
  options?: { [key: string]: any },
) {
  return request<{ data: string[]; success: boolean }>(
    `/api/v1/datasources/${id}/tables`,
    {
      method: 'GET',
      ...(options || {}),
    },
  );
}

/** 轮换 API 数据源推送 token（旧 token 立即失效）POST /api/v1/datasources/{id}/rotate-push-token */
export async function rotatePushToken(
  id: string,
  options?: { [key: string]: any },
) {
  return request<{ data: { pushToken: string; url: string }; success: boolean }>(
    `/api/v1/datasources/${id}/rotate-push-token`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

/** 列出 S3 数据源的桶 GET /api/v1/datasources/{id}/buckets（仅 s3，#18） */
export async function listBuckets(
  datasourceId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: string[]; success: boolean }>(
    `/api/v1/datasources/${datasourceId}/buckets`,
    {
      method: 'GET',
      ...(options || {}),
    },
  );
}

/** 列出 S3 桶内对象 GET /api/v1/datasources/{id}/objects（仅 s3，#18） */
export async function listObjects(
  datasourceId: string,
  params: { bucket: string; prefix?: string },
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.S3Object[]; success: boolean }>(
    `/api/v1/datasources/${datasourceId}/objects`,
    {
      method: 'GET',
      params: { ...params },
      ...(options || {}),
    },
  );
}

/** 托管 S3 数据为受管数据集（纯引用，不下载）POST /api/v1/datasets/host-s3（#18） */
export async function hostS3(
  body: DataPlatform.HostS3Params,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Dataset[]; success: boolean }>(
    '/api/v1/datasets/host-s3',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 文件管理零拷贝接入为受管数据集 POST /api/v1/datasets/host-platform */
export async function hostPlatformFiles(
  body: DataPlatform.PlatformHostParams,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Dataset[]; success: boolean }>(
    '/api/v1/datasets/host-platform',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 取消托管（仅 admin，仅移除平台引用，绝不删 S3 源对象）POST /api/v1/datasets/{id}/unhost（#18） */
export async function unhostDataset(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/datasets/${id}/unhost`, {
    method: 'POST',
    ...(options || {}),
  });
}

/** 发布版本为可训练（仅 admin；未过安全扫描返回 409 + message）（#4 发布门） */
export async function publishVersion(
  versionId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetVersion; success: boolean }>(
    `/api/v1/dataset-versions/${versionId}/publish`,
    {
      method: 'POST',
      // skipErrorHandler:交由调用方 catch 展示后端「未过安全扫描」message
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/** 下架已发布版本（仅 admin）（#4 发布门） */
export async function unpublishVersion(
  versionId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetVersion; success: boolean }>(
    `/api/v1/dataset-versions/${versionId}/unpublish`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

/** 人工覆盖安全扫描结论（仅 admin；接受风险=passed / 驳回=failed）（#4 发布门） */
export async function setVersionVerdict(
  versionId: string,
  body: { verdict: 'passed' | 'failed'; note?: string },
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetVersion; success: boolean }>(
    `/api/v1/dataset-versions/${versionId}/verdict`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 获取采集任务列表 GET /api/v1/ingest-tasks */
export async function listIngestTasks(
  params?: DataPlatform.IngestTaskListParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.IngestTask>>('/api/v1/ingest-tasks', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  });
}

/** 新建采集任务 POST /api/v1/ingest-tasks */
export async function createIngestTask(
  body: DataPlatform.IngestTaskCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.IngestTask; success: boolean }>('/api/v1/ingest-tasks', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/** 获取单个采集任务（每次 GET 会推进 running 任务进度）GET /api/v1/ingest-tasks/:id */
export async function getIngestTask(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.IngestTask; success: boolean }>(
    `/api/v1/ingest-tasks/${id}`,
    {
      method: 'GET',
      ...(options || {}),
    },
  );
}

/** 重跑采集任务 POST /api/v1/ingest-tasks/:id/rerun */
export async function rerunIngestTask(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.IngestTask; success: boolean }>(
    `/api/v1/ingest-tasks/${id}/rerun`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

/** 生成数据集（库数据→jsonl→平台 MinIO，不同版本不同文件夹）POST /api/v1/ingest-tasks/:id/generate-dataset */
export async function generateDataset(
  id: string,
  options?: { [key: string]: any },
) {
  return request<{
    data: {
      datasetId: string;
      datasetName: string;
      versionId: string;
      versionNo: number;
      rows: number;
      bucket: string;
      fileKey: string;
      storageUri: string;
    };
    success: boolean;
  }>(`/api/v1/ingest-tasks/${id}/generate-dataset`, {
    method: 'POST',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/** 编辑采集任务 PUT /api/v1/ingest-tasks/:id */
export async function updateIngestTask(
  id: string,
  body: Partial<DataPlatform.IngestTaskCreate>,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.IngestTask; success: boolean }>(
    `/api/v1/ingest-tasks/${id}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 采集任务运行记录 GET /api/v1/ingest-tasks/:id/runs */
export async function listIngestRuns(
  id: string,
  params?: { current?: number; pageSize?: number },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.IngestRun>>(
    `/api/v1/ingest-tasks/${id}/runs`,
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 停止采集任务 POST /api/v1/ingest-tasks/:id/stop */
export async function stopIngestTask(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.IngestTask; success: boolean }>(
    `/api/v1/ingest-tasks/${id}/stop`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

/** 删除采集任务 DELETE /api/v1/ingest-tasks/:id */
export async function deleteIngestTask(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/ingest-tasks/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

/** 获取上传记录列表 GET /api/v1/uploads */
export async function listUploads(
  params?: DataPlatform.UploadListParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.UploadRecord>>('/api/v1/uploads', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  });
}

/** 上传文件 POST /api/v1/upload */
export async function uploadFile(body: FormData, options?: { [key: string]: any }) {
  return request<DataPlatform.UploadResult>('/api/v1/upload', {
    method: 'POST',
    data: body,
    requestType: 'form',
    ...(options || {}),
  });
}

/** AI 推断 schema POST /api/v1/ai/infer-schema */
export async function inferSchema(
  body: { sample: string },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.InferSchemaResult>('/api/v1/ai/infer-schema', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/** AI 生成采集任务 POST /api/v1/ai/generate-task */
export async function generateTask(
  body: { prompt: string },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.GenerateTaskResult>('/api/v1/ai/generate-task', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/** AI 问答 POST /api/v1/ai/qa */
export async function aiQa(body: { question: string }, options?: { [key: string]: any }) {
  return request<DataPlatform.QaResult>('/api/v1/ai/qa', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/** AI:据目标生成算子流水线 POST /api/v1/ai/generate-pipeline */
export async function generatePipeline(
  body: DataPlatform.GeneratePipelineParams,
) {
  return request<{ data: DataPlatform.GeneratedPipeline; success: boolean }>(
    '/api/v1/ai/generate-pipeline',
    { method: 'POST', data: body },
  );
}

/** AI:据目标推荐质量评估算子 POST /api/v1/ai/generate-quality */
export async function generateQuality(body: DataPlatform.GeneratePipelineParams) {
  return request<{ data: DataPlatform.GeneratedPipeline; success: boolean }>(
    '/api/v1/ai/generate-quality',
    { method: 'POST', data: body },
  );
}

/** AI:据文件名/格式/分类建议数据集名 POST /api/v1/ai/suggest-dataset-name */
export async function suggestDatasetName(
  body: DataPlatform.SuggestDatasetNameParams,
) {
  return request<{
    data: DataPlatform.SuggestedDatasetName;
    success: boolean;
  }>('/api/v1/ai/suggest-dataset-name', { method: 'POST', data: body });
}

/** 上传文件并落地为数据集 POST /api/v1/datasets/upload */
export async function uploadDataset(
  formData: FormData,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetDetail; success: boolean }>(
    '/api/v1/datasets/upload',
    { method: 'POST', data: formData, ...(options || {}) },
  );
}

/** 媒体批量接入:一批文件 → 一个 manifest 数据集 POST /api/v1/datasets/upload-media */
export async function uploadMediaDataset(
  formData: FormData,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetDetail; success: boolean }>(
    '/api/v1/datasets/upload-media',
    { method: 'POST', data: formData, ...(options || {}) },
  );
}

/** 单一格式批量本地上传:一批同格式文件 → 原文件存 MinIO + 合并生成一个 jsonl 数据集
 * POST /api/v1/datasets/upload-batch */
export async function uploadBatchDataset(
  formData: FormData,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetDetail; success: boolean }>(
    '/api/v1/datasets/upload-batch',
    { method: 'POST', data: formData, ...(options || {}) },
  );
}

/** 列出版本的成员文件 GET /api/v1/dataset-versions/{id}/members */
export async function listDatasetMembers(
  versionId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetMember[]; success: boolean }>(
    `/api/v1/dataset-versions/${versionId}/members`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 取成员对象的预签名预览/下载 URL GET /api/v1/dataset-versions/{id}/member-url */
export async function getDatasetMemberUrl(
  versionId: string,
  key: string,
  options?: { [key: string]: any },
) {
  return request<{ data: { url: string }; success: boolean }>(
    `/api/v1/dataset-versions/${versionId}/member-url`,
    { method: 'GET', params: { key }, ...(options || {}) },
  );
}

/** 向媒体集追加成员文件 POST /api/v1/datasets/{id}/members */
export async function addDatasetMembers(
  datasetId: string,
  formData: FormData,
  options?: { [key: string]: any },
) {
  return request<{ data: { rows: number }; success: boolean }>(
    `/api/v1/datasets/${datasetId}/members`,
    { method: 'POST', data: formData, ...(options || {}) },
  );
}

/** 从媒体集移除一个成员文件 DELETE /api/v1/datasets/{id}/members?key= */
export async function deleteDatasetMember(
  datasetId: string,
  key: string,
  options?: { [key: string]: any },
) {
  return request<{ data: { rows: number }; success: boolean }>(
    `/api/v1/datasets/${datasetId}/members`,
    { method: 'DELETE', params: { key }, ...(options || {}) },
  );
}

/** 数据集列表 GET /api/v1/datasets */
export async function listDatasets(
  params?: DataPlatform.DatasetListParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.Dataset>>('/api/v1/datasets', {
    method: 'GET',
    params: { ...params },
    ...(options || {}),
  });
}

/** 数据集详情（含版本） GET /api/v1/datasets/{id} */
export async function getDataset(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.DatasetDetail; success: boolean }>(
    `/api/v1/datasets/${id}`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 数据集血缘图(版本↔任务 DAG,跨数据集)GET /api/v1/datasets/{id}/lineage */
export async function getDatasetLineage(
  datasetId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.LineageGraph; success: boolean }>(
    `/api/v1/datasets/${datasetId}/lineage`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 更新数据集元数据 PATCH /api/v1/datasets/{id} */
export async function updateDataset(id: string, body: DataPlatform.DatasetUpdate) {
  return request<{ data: DataPlatform.DatasetDetail; success: boolean }>(
    `/api/v1/datasets/${id}`,
    { method: 'PATCH', data: body },
  );
}

/** 删除数据集 DELETE /api/v1/datasets/{id} */
export async function deleteDataset(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/datasets/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

/** 数据集 ACL 授权列表(需 admin 级)GET /api/v1/datasets/{id}/acl */
export async function listAcl(
  datasetId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetAcl[]; success: boolean }>(
    `/api/v1/datasets/${datasetId}/acl`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 新增 ACL 授权条目(subjectType='all' 时 subjectId 传 "*")POST /api/v1/datasets/{id}/acl */
export async function addAcl(
  datasetId: string,
  body: DataPlatform.AclCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetAcl; success: boolean }>(
    `/api/v1/datasets/${datasetId}/acl`,
    { method: 'POST', data: body, ...(options || {}) },
  );
}

/** 修改 ACL 授权级别 PUT /api/v1/datasets/{id}/acl/{aclId} */
export async function updateAcl(
  datasetId: string,
  aclId: string,
  level: DataPlatform.AclLevel,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DatasetAcl; success: boolean }>(
    `/api/v1/datasets/${datasetId}/acl/${aclId}`,
    { method: 'PUT', data: { level }, ...(options || {}) },
  );
}

/** 删除 ACL 授权条目 DELETE /api/v1/datasets/{id}/acl/{aclId} */
export async function deleteAcl(
  datasetId: string,
  aclId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/datasets/${datasetId}/acl/${aclId}`,
    { method: 'DELETE', ...(options || {}) },
  );
}

/** 模糊搜索授权对象(用户/角色,需 admin 级)GET /api/v1/datasets/{id}/acl/candidates */
export async function searchAclCandidates(
  datasetId: string,
  params: { q?: string; type: 'user' | 'role' },
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.AclCandidate[]; success: boolean }>(
    `/api/v1/datasets/${datasetId}/acl/candidates`,
    { method: 'GET', params, ...(options || {}) },
  );
}

/** 批量删除数据集 POST /api/v1/datasets/batch-delete */
export async function batchDeleteDatasets(
  ids: string[],
  options?: { [key: string]: any },
) {
  return request<{ data: { deleted: number }; success: boolean }>(
    '/api/v1/datasets/batch-delete',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: { ids },
      ...(options || {}),
    },
  );
}

/** 版本数据预览 GET /api/v1/dataset-versions/{versionId}/preview */
export async function previewDatasetVersion(
  versionId: string,
  params?: { limit?: number; offset?: number; key?: string },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.DatasetPreview>(
    `/api/v1/dataset-versions/${versionId}/preview`,
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 版本数据 DuckDB 只读 SQL 查询 POST /api/v1/dataset-versions/{versionId}/query
 *  body: { sql, limit?, offset? } → 形状同 preview(DatasetPreview)。 */
export async function queryDatasetVersion(
  versionId: string,
  body: { sql: string; limit?: number; offset?: number },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.DatasetPreview>(
    `/api/v1/dataset-versions/${versionId}/query`,
    { method: 'POST', data: { ...body }, ...(options || {}) },
  );
}

/** dj-analyze 分析报告(overall.csv 聚合表 + analysis/ PNG 清单)
 *  GET /api/v1/dataset-versions/{versionId}/analysis-report
 *  无报告时 data 为 null(前端回退到手算聚合)。 */
export async function getAnalysisReport(
  versionId: string,
  options?: { [key: string]: any },
) {
  return request<{
    data: DataPlatform.AnalysisReport | null;
    success: boolean;
    message?: string;
  }>(`/api/v1/dataset-versions/${versionId}/analysis-report`, {
    method: 'GET',
    ...(options || {}),
  });
}

/** 加工算子目录 GET /api/v1/operators */
export async function listOperators(options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.Operator[]; success: boolean }>(
    '/api/v1/operators',
    { method: 'GET', ...(options || {}) },
  );
}

/** 加工任务列表 GET /api/v1/jobs */
export async function listJobs(
  params?: { current?: number; pageSize?: number; type?: string },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.Job>>('/api/v1/jobs', {
    method: 'GET',
    params: { ...params },
    ...(options || {}),
  });
}

/** 数据任务统一列表(跨治理+评估类型)GET /api/v1/data-tasks
 *  types=逗号分隔类型白名单;state=单值;keyword=任务名模糊。 */
export async function listDataTasks(
  params?: {
    current?: number;
    pageSize?: number;
    types?: string;
    state?: string;
    keyword?: string;
  },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.Job>>(
    '/api/v1/data-tasks',
    {
      method: 'GET',
      params: { ...params },
      ...(options || {}),
    },
  );
}

/** 新建并执行加工任务 POST /api/v1/jobs */
export async function createJob(
  body: DataPlatform.JobCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>('/api/v1/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/** 重跑加工任务 POST /api/v1/jobs/:id/rerun（用原配置对原输入版本再跑一次，产新版本） */
export async function rerunJob(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/jobs/${id}/rerun`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 停止运行中/排队中的加工任务（杀子进程并标记 cancelled）POST /api/v1/jobs/:id/stop */
export async function stopJob(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/jobs/${id}/stop`, {
    method: 'POST',
    // skipErrorHandler:交由调用方 catch 展示后端「不在运行中」等 message
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/** 暂停运行中/排队中的任务（杀子进程并标记 paused，保留 spec）POST /api/v1/jobs/:id/pause。
 *  dj-process 无原生暂停,故暂停=终止当前运行;继续(resume)将按 spec 从头重跑。 */
export async function pauseJob(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/jobs/${id}/pause`, {
    method: 'POST',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/** 继续已暂停的任务（重置 pending 并按原 spec 从头重跑，复用同一记录）POST /api/v1/jobs/:id/resume */
export async function resumeJob(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/jobs/${id}/resume`, {
    method: 'POST',
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/** 样例试跑:前 N 行跑算子预览加工前后 POST /api/v1/jobs/preview */
export async function previewJob(body: {
  datasetVersionId: string;
  operators: DataPlatform.PipelineStep[];
  sampleSize?: number;
}) {
  // skipErrorHandler:交由调用方 catch 展示后端 message,避免全局 handler 再弹一条 "Response status:400"
  return request<{ data: DataPlatform.PreviewResult; success: boolean }>(
    '/api/v1/jobs/preview',
    { method: 'POST', data: body, skipErrorHandler: true },
  );
}

/** 加工任务详情 GET /api/v1/jobs/{id} */
export async function getJob(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/jobs/${id}`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 删除加工任务（只删任务记录，产出的数据集版本保留）DELETE /api/v1/jobs/:id */
export async function deleteJob(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(`/api/v1/jobs/${id}`, {
    method: 'DELETE',
    ...(options || {}),
  });
}

/** 批量删除加工任务（只删任务记录，产出版本保留；运行中/不存在自动跳过）POST /api/v1/jobs/batch-delete */
export async function batchDeleteJobs(
  ids: string[],
  options?: { [key: string]: any },
) {
  return request<{ data: { deleted: number }; success: boolean }>(
    '/api/v1/jobs/batch-delete',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: { ids },
      ...(options || {}),
    },
  );
}

/** 新建并执行质量评估任务 POST /api/v1/quality/jobs */
export async function createQualityJob(
  body: DataPlatform.QualityJobCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>('/api/v1/quality/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

// ---------------------------------------------------------------------------
// 数据蒸馏
// ---------------------------------------------------------------------------
/** 新建并执行蒸馏任务 POST /api/v1/distillation/jobs */
export async function createDistillationJob(
  body: DataPlatform.DistillationJobCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    '/api/v1/distillation/jobs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 分页列出蒸馏任务 GET /api/v1/distillation/jobs */
export async function listDistillationJobs(
  params: { current?: number; pageSize?: number } = {},
  options?: { [key: string]: any },
) {
  return request<{
    data: DataPlatform.Job[];
    total: number;
    success: boolean;
  }>('/api/v1/distillation/jobs', {
    method: 'GET',
    params: { current: 1, pageSize: 10, ...params },
    ...(options || {}),
  });
}

/** 蒸馏任务详情 GET /api/v1/distillation/jobs/{id} */
export async function getDistillationJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/distillation/jobs/${jobId}`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 重跑蒸馏任务 POST /api/v1/distillation/jobs/{id}/rerun */
export async function rerunDistillationJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/distillation/jobs/${jobId}/rerun`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 停止蒸馏任务 POST /api/v1/distillation/jobs/{id}/stop */
export async function stopDistillationJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/distillation/jobs/${jobId}/stop`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 删除蒸馏任务 DELETE /api/v1/distillation/jobs/{id} */
export async function deleteDistillationJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/distillation/jobs/${jobId}`,
    { method: 'DELETE', ...(options || {}) },
  );
}

/** 批量删除蒸馏任务 POST /api/v1/distillation/jobs/batch-delete */
export async function batchDeleteDistillationJobs(
  ids: string[],
  options?: { [key: string]: any },
) {
  return request<{ data: { deleted: number }; success: boolean }>(
    '/api/v1/distillation/jobs/batch-delete',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: { ids },
      ...(options || {}),
    },
  );
}

/** 读取蒸馏报告 GET /api/v1/distillation/jobs/{id}/report */
export async function getDistillationReport(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.DistillationReport; success: boolean }>(
    `/api/v1/distillation/jobs/${jobId}/report`,
    { method: 'GET', ...(options || {}) },
  );
}

// ---------------------------------------------------------------------------
// 数据合成(make)——LLM 造新数据
// ---------------------------------------------------------------------------
/** 新建合成任务 POST /api/v1/synthesis/jobs */
export async function createMakeJob(
  body: DataPlatform.MakeJobCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    '/api/v1/synthesis/jobs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 分页列出合成任务 GET /api/v1/synthesis/jobs */
export async function listMakeJobs(
  params: { current?: number; pageSize?: number } = {},
  options?: { [key: string]: any },
) {
  return request<{
    data: DataPlatform.Job[];
    total: number;
    success: boolean;
  }>('/api/v1/synthesis/jobs', {
    method: 'GET',
    params: { current: 1, pageSize: 10, ...params },
    ...(options || {}),
  });
}

/** 合成任务详情 GET /api/v1/synthesis/jobs/{id} */
export async function getMakeJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/synthesis/jobs/${jobId}`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 重跑合成任务 POST /api/v1/synthesis/jobs/{id}/rerun */
export async function rerunMakeJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/synthesis/jobs/${jobId}/rerun`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 停止合成任务 POST /api/v1/synthesis/jobs/{id}/stop */
export async function stopMakeJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/synthesis/jobs/${jobId}/stop`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 删除合成任务 DELETE /api/v1/synthesis/jobs/{id} */
export async function deleteMakeJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/synthesis/jobs/${jobId}`,
    { method: 'DELETE', ...(options || {}) },
  );
}

/** 批量删除合成任务 POST /api/v1/synthesis/jobs/batch-delete */
export async function batchDeleteMakeJobs(
  ids: string[],
  options?: { [key: string]: any },
) {
  return request<{ data: { deleted: number }; success: boolean }>(
    '/api/v1/synthesis/jobs/batch-delete',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: { ids },
      ...(options || {}),
    },
  );
}

/** 读取合成报告 GET /api/v1/synthesis/jobs/{id}/report */
export async function getMakeReport(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.MakeReport; success: boolean }>(
    `/api/v1/synthesis/jobs/${jobId}/report`,
    { method: 'GET', ...(options || {}) },
  );
}

// ---------------------------------------------------------------------------
// 数据增强(augment)——LLM 改写已有数据
// ---------------------------------------------------------------------------
/** 新建增强任务 POST /api/v1/augmentation/jobs */
export async function createAugmentJob(
  body: DataPlatform.AugmentJobCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    '/api/v1/augmentation/jobs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 分页列出增强任务 GET /api/v1/augmentation/jobs */
export async function listAugmentJobs(
  params: { current?: number; pageSize?: number } = {},
  options?: { [key: string]: any },
) {
  return request<{
    data: DataPlatform.Job[];
    total: number;
    success: boolean;
  }>('/api/v1/augmentation/jobs', {
    method: 'GET',
    params: { current: 1, pageSize: 10, ...params },
    ...(options || {}),
  });
}

/** 增强任务详情 GET /api/v1/augmentation/jobs/{id} */
export async function getAugmentJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/augmentation/jobs/${jobId}`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 重跑增强任务 POST /api/v1/augmentation/jobs/{id}/rerun */
export async function rerunAugmentJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    `/api/v1/augmentation/jobs/${jobId}/rerun`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 停止增强任务 POST /api/v1/augmentation/jobs/{id}/stop */
export async function stopAugmentJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/augmentation/jobs/${jobId}/stop`,
    { method: 'POST', ...(options || {}) },
  );
}

/** 删除增强任务 DELETE /api/v1/augmentation/jobs/{id} */
export async function deleteAugmentJob(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    `/api/v1/augmentation/jobs/${jobId}`,
    { method: 'DELETE', ...(options || {}) },
  );
}

/** 批量删除增强任务 POST /api/v1/augmentation/jobs/batch-delete */
export async function batchDeleteAugmentJobs(
  ids: string[],
  options?: { [key: string]: any },
) {
  return request<{ data: { deleted: number }; success: boolean }>(
    '/api/v1/augmentation/jobs/batch-delete',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: { ids },
      ...(options || {}),
    },
  );
}

/** 读取增强报告 GET /api/v1/augmentation/jobs/{id}/report */
export async function getAugmentReport(
  jobId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.AugmentReport; success: boolean }>(
    `/api/v1/augmentation/jobs/${jobId}/report`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 逐条质量得分 GET /api/v1/dataset-versions/{versionId}/stats */
export async function getVersionStats(
  versionId: string,
  params?: { current?: number; pageSize?: number },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.VersionStatsResult>(
    `/api/v1/dataset-versions/${versionId}/stats`,
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 质量分析报告 GET /api/v1/dataset-versions/{versionId}/quality-report */
export async function getQualityReport(
  versionId: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.QualityReport; success: boolean }>(
    `/api/v1/dataset-versions/${versionId}/quality-report`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 算子市场:目录概览 GET /api/v1/operators/catalog/meta */
export async function getOperatorCatalogMeta(options?: {
  [key: string]: any;
}) {
  return request<{ data: DataPlatform.OperatorCatalogMeta; success: boolean }>(
    '/api/v1/operators/catalog/meta',
    { method: 'GET', ...(options || {}) },
  );
}

/** 算子市场:当前环境执行能力 GET /api/v1/operators/capabilities */
export async function getOperatorCapabilities(options?: {
  [key: string]: any;
}) {
  return request<{
    data: DataPlatform.OperatorCapabilities;
    success: boolean;
  }>('/api/v1/operators/capabilities', { method: 'GET', ...(options || {}) });
}

/** 算子市场:目录查询(分面 + 分页) GET /api/v1/operators/catalog */
export async function listOperatorCatalog(
  params?: DataPlatform.OperatorCatalogParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.CatalogOperator>>(
    '/api/v1/operators/catalog',
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 算子市场:单算子详情 GET /api/v1/operators/{name} */
export async function getOperatorDetail(
  name: string,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.CatalogOperator; success: boolean }>(
    `/api/v1/operators/${name}`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 内容安全:新建并执行审核任务 POST /api/v1/content-safety/jobs */
export async function createReviewJob(
  body: DataPlatform.ReviewJobCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Job; success: boolean }>(
    '/api/v1/content-safety/jobs',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/** 内容安全:审核任务列表（type=review）GET /api/v1/content-safety/jobs */
export async function listReviewJobs(
  params?: { current?: number; pageSize?: number },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.Job>>(
    '/api/v1/content-safety/jobs',
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 内容安全:审核报告 GET /api/v1/content-safety/jobs/{id}/report */
export async function getReviewReport(id: string, options?: { [key: string]: any }) {
  return request<{ data: DataPlatform.ReviewReport; success: boolean }>(
    `/api/v1/content-safety/jobs/${id}/report`,
    { method: 'GET', ...(options || {}) },
  );
}

/** 内容安全:命中明细（分页 + 按类别/来源/严重度筛）GET /api/v1/content-safety/jobs/{id}/findings */
export async function listReviewFindings(
  id: string,
  params?: DataPlatform.ReviewFindingListParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.ReviewFinding>>(
    `/api/v1/content-safety/jobs/${id}/findings`,
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 操作审计日志列表（仅管理员）GET /api/v1/audit */
export async function listAuditLogs(
  params?: DataPlatform.AuditLogListParams,
  options?: { [key: string]: any },
) {
  return request<DataPlatform.PageResult<DataPlatform.AuditLog>>('/api/v1/audit', {
    method: 'GET',
    params: { ...params },
    ...(options || {}),
  });
}

/** 文件管理:列出平台 MinIO 的桶（所有登录用户）GET /api/v1/files/buckets（#10） */
export async function listPlatformBuckets(options?: { [key: string]: any }) {
  return request<{ data: string[]; success: boolean }>('/api/v1/files/buckets', {
    method: 'GET',
    ...(options || {}),
  });
}

/** 文件管理:列目录（文件夹 + 对象）GET /api/v1/files（#10） */
export async function listFiles(
  params: { bucket: string; prefix?: string },
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.FileListResult; success: boolean }>(
    '/api/v1/files',
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 文件管理:获取对象下载 presigned URL GET /api/v1/files/download-url（#10） */
export async function getFileDownloadUrl(
  params: { bucket: string; key: string },
  options?: { [key: string]: any },
) {
  return request<{ data: { url: string }; success: boolean }>(
    '/api/v1/files/download-url',
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
}

/** 文件管理:预览对象（可解析格式）GET /api/v1/files/preview（#10） */
export async function previewFile(
  params: { bucket: string; key: string; limit?: number },
  options?: { [key: string]: any },
) {
  return request<{
    data: { columns: string[]; data: Record<string, any>[]; message?: string };
    success: boolean;
  }>('/api/v1/files/preview', {
    method: 'GET',
    params: { ...params },
    ...(options || {}),
  });
}

/** 文件管理:上传对象（仅 admin，multipart）POST /api/v1/files/upload（#10） */
export async function uploadPlatformFile(
  formData: FormData,
  options?: { [key: string]: any },
) {
  return request<{ data: { key: string }; success: boolean }>(
    '/api/v1/files/upload',
    { method: 'POST', data: formData, requestType: 'form', ...(options || {}) },
  );
}

/** 文件管理:新建文件夹（仅 admin）POST /api/v1/files/folder（#10） */
export async function createFolder(
  body: { bucket: string; prefix?: string; name: string },
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>('/api/v1/files/folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/** 文件管理:删除对象（仅 admin；被托管引用返回 409 + message）DELETE /api/v1/files/object（#10） */
export async function deleteFileObject(
  params: { bucket: string; key: string },
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>('/api/v1/files/object', {
    method: 'DELETE',
    params: { ...params },
    // skipErrorHandler:交由调用方 catch 展示后端「被 N 个托管数据集引用」message
    skipErrorHandler: true,
    ...(options || {}),
  });
}

/** 文件管理:递归删除文件夹（仅 admin；被托管引用返回 409 + message）POST /api/v1/files/delete-folder（#10） */
export async function deleteFolder(
  body: { bucket: string; prefix: string },
  options?: { [key: string]: any },
) {
  return request<{ data: { deleted: number }; success: boolean }>(
    '/api/v1/files/delete-folder',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      // skipErrorHandler:交由调用方 catch 展示后端 409 message
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

// LLM 供应商管理

/** 获取 LLM 供应商列表 GET /api/v1/llm-providers */
export async function listLlmProviders() {
  return request<{ data: DataPlatform.LlmProvider[]; success: boolean }>(
    '/api/v1/llm-providers',
  );
}

/** 新建 LLM 供应商 POST /api/v1/llm-providers */
export async function createLlmProvider(body: DataPlatform.LlmProviderCreate) {
  return request<{ data: DataPlatform.LlmProvider; success: boolean }>(
    '/api/v1/llm-providers',
    {
      method: 'POST',
      data: body,
      skipErrorHandler: true,
    },
  );
}

/** 更新 LLM 供应商 PUT /api/v1/llm-providers/{id} */
export async function updateLlmProvider(
  id: string,
  body: DataPlatform.LlmProviderUpdate,
) {
  return request<{ data: DataPlatform.LlmProvider; success: boolean }>(
    `/api/v1/llm-providers/${id}`,
    {
      method: 'PUT',
      data: body,
      skipErrorHandler: true,
    },
  );
}

/** 删除 LLM 供应商 DELETE /api/v1/llm-providers/{id} */
export async function deleteLlmProvider(id: string) {
  return request<{ success: boolean }>(`/api/v1/llm-providers/${id}`, {
    method: 'DELETE',
  });
}

/** 激活 LLM 供应商 POST /api/v1/llm-providers/{id}/activate */
export async function activateLlmProvider(id: string) {
  return request<{ data: DataPlatform.LlmProvider; success: boolean }>(
    `/api/v1/llm-providers/${id}/activate`,
    {
      method: 'POST',
      skipErrorHandler: true,
    },
  );
}

/** 测试 LLM 供应商连通性 POST /api/v1/llm-providers/{id}/test */
export async function testLlmProvider(id: string) {
  return request<{ data: DataPlatform.LlmTestResult; success: boolean }>(
    `/api/v1/llm-providers/${id}/test`,
    {
      method: 'POST',
      skipErrorHandler: true,
    },
  );
}

/** 用未保存的配置测试 LLM 连通性 POST /api/v1/llm-providers/test */
export async function testLlmProviderConfig(
  body: DataPlatform.LlmProviderTest,
) {
  return request<{ data: DataPlatform.LlmTestResult; success: boolean }>(
    '/api/v1/llm-providers/test',
    {
      method: 'POST',
      data: body,
      skipErrorHandler: true,
    },
  );
}

/** 下发明文 API Key（仅管理员，供编辑回填） GET /api/v1/llm-providers/{id}/reveal */
export async function revealLlmProviderKey(id: string) {
  return request<{ data: { apiKey: string }; success: boolean }>(
    `/api/v1/llm-providers/${id}/reveal`,
    { skipErrorHandler: true },
  );
}

/** 获取 LLM 用量统计 GET /api/v1/llm-providers/usage */
export async function getLlmUsage(days = 7) {
  return request<{ data: DataPlatform.LlmUsageSummary; success: boolean }>(
    '/api/v1/llm-providers/usage',
    {
      params: { days },
    },
  );
}

/** 列出供应商可选模型 GET /api/v1/llm-providers/{id}/models */
export async function listProviderModels(providerId: string) {
  return request<{ data: DataPlatform.LlmModel[]; success: boolean }>(
    `/api/v1/llm-providers/${providerId}/models`,
  );
}

/** 拉取供应商模型清单(调供应商 /models)并入库 POST /api/v1/llm-providers/{id}/fetch-models */
export async function fetchProviderModels(providerId: string) {
  return request<{
    data: DataPlatform.LlmFetchModelsResult;
    success: boolean;
  }>(`/api/v1/llm-providers/${providerId}/fetch-models`, {
    method: 'POST',
    skipErrorHandler: true,
  });
}

/** 手动添加一个模型 POST /api/v1/llm-providers/{id}/models */
export async function addProviderModel(providerId: string, model: string) {
  return request<{ data: DataPlatform.LlmModel[]; success: boolean }>(
    `/api/v1/llm-providers/${providerId}/models`,
    {
      method: 'POST',
      data: { model },
      skipErrorHandler: true,
    },
  );
}

/** 删除一个模型 DELETE /api/v1/llm-providers/{id}/models/{modelId} */
export async function deleteProviderModel(
  providerId: string,
  modelId: string,
) {
  return request<{ success: boolean }>(
    `/api/v1/llm-providers/${providerId}/models/${modelId}`,
    {
      method: 'DELETE',
      skipErrorHandler: true,
    },
  );
}

/** 设为当前生效模型 POST /api/v1/llm-providers/{id}/select-model */
export async function selectProviderModel(providerId: string, model: string) {
  return request<{ data: DataPlatform.LlmProvider; success: boolean }>(
    `/api/v1/llm-providers/${providerId}/select-model`,
    {
      method: 'POST',
      data: { model },
      skipErrorHandler: true,
    },
  );
}
