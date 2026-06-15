// @ts-ignore
/* eslint-disable */
import { request } from '@umijs/max';

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

/** 列出数据源库内的表 GET /api/v1/datasources/{id}/tables（仅 PostgreSQL） */
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
  params?: { limit?: number; offset?: number },
  options?: { [key: string]: any },
) {
  return request<DataPlatform.DatasetPreview>(
    `/api/v1/dataset-versions/${versionId}/preview`,
    { method: 'GET', params: { ...params }, ...(options || {}) },
  );
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
