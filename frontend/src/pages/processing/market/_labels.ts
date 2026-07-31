/**
 * 算子工厂共用常量与中文映射。
 *
 * index.tsx / detail.tsx / upload.tsx 共用,不要在页内重复定义。
 * 字典顺序与后端 `backend/app/services/operator_catalog.py` 字段口径一致。
 */

/** 算子执行要求 → 彩色分类标签 */
export const RUNNABLE_TAG: Record<
  DataPlatform.CatalogOperator['runnable'],
  { label: string; color: string }
> = {
  ready: { label: '可直接执行', color: 'green' },
  needs_api: { label: '需要 AI', color: 'geekblue' },
  needs_media: { label: '需要媒体', color: 'orange' },
  needs_compute: { label: '需要算力', color: 'volcano' },
};

/** 算子资源类 → 中文展示名 */
export const RESOURCE_LABEL: Record<
  DataPlatform.CatalogOperator['resourceClass'],
  string
> = {
  cpu: 'CPU',
  api_llm: 'LLM API',
  hf_model: 'HF 模型',
  gpu: 'GPU',
  vllm: 'vLLM',
};

/** 算子模态 → 中文展示名 */
export const MODALITY_LABEL: Record<string, string> = {
  text: '文本',
  image: '图像',
  video: '视频',
  audio: '音频',
  multimodal: '多模态',
};

/** DJ 原生 8 类(category)→ 中文展示名 */
export const CATEGORY_LABEL: Record<string, string> = {
  aggregator: '聚合器',
  deduplicator: '去重器',
  filter: '过滤器',
  formatter: '格式化器',
  grouper: '分组器',
  mapper: '映射器',
  pipeline: '管线',
  selector: '选择器',
};
