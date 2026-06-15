// @ts-ignore
/* eslint-disable */

declare namespace DataPlatform {
  /** 数据源类型 */
  type DataSourceType = 's3' | 'hdfs' | 'database' | 'api';

  /** 数据库类型（当 DataSourceType 为 database 时使用） */
  type DbKind =
    | 'postgresql'
    | 'dameng'
    | 'goldendb'
    | 'kingbase'
    | 'gaussdb'
    | 'hologres'
    | 'sequoiadb'
    | 'hive'
    | 'doris';

  /** 数据源 */
  type DataSource = {
    id: string;
    name: string;
    type: DataSourceType;
    dbKind?: DbKind;
    status: 'connected' | 'failed' | 'pending';
    config: Record<string, any>;
    description?: string;
    creator: string;
    createdAt: string;
    updatedAt: string;
  };

  /** 采集任务调度 */
  type IngestSchedule = {
    mode: 'once' | 'cron';
    cron?: string;
  };

  /** 采集对象（拉什么）：勾选的表（每张表一数据集）或 自定义 SQL */
  type IngestExtract = {
    mode: 'table' | 'sql';
    tables?: string[];
    sql?: string;
  };

  /** 采集产物概要（详情接口返回） */
  type IngestOutput = {
    datasetId: string;
    datasetName: string;
    versionId: string;
    versionNo: number;
    rows?: number;
  };

  /** 采集任务 */
  type IngestTask = {
    id: string;
    name: string;
    datasourceId: string;
    datasourceName: string;
    schedule: IngestSchedule;
    extract?: IngestExtract;
    status: 'pending' | 'running' | 'success' | 'failed';
    progress: number;
    runCount?: number;
    createdAt: string;
    lastRunAt?: string;
    logs?: string[];
    output?: IngestOutput[];
  };

  /** 采集运行记录（一次运行明细） */
  type IngestRun = {
    id: string;
    taskId: string;
    status: 'success' | 'failed';
    rows: number;
    datasetCount: number;
    outputs?: IngestOutput[];
    error?: string;
    startedAt: string;
    finishedAt?: string;
  };

  /** 加工算子参数定义 */
  type OperatorParam = {
    name: string;
    label: string;
    type: 'select' | 'number' | 'string';
    default?: any;
    options?: string[];
  };

  /** 加工算子（目录项） */
  type Operator = {
    name: string;
    category: string;
    label: string;
    description: string;
    params: OperatorParam[];
  };

  /** 算子市场:目录项参数(data-juicer 原始参数表一行) */
  type CatalogParam = {
    name: string;
    type: string;
    default: string;
    desc: string;
  };

  /** 算子市场:全量目录算子项 */
  type CatalogOperator = {
    name: string;
    category: string;
    summaryEn: string;
    summaryZh: string;
    descEn?: string;
    descZh?: string;
    modality: string[];
    compute: string | null;
    frameworks: string[];
    stability: string | null;
    resourceClass: 'cpu' | 'api_llm' | 'hf_model' | 'gpu' | 'vllm';
    params: CatalogParam[];
    example?: string | null;
    reference?: string | null;
    detailPage?: string | null;
    scenarioGroup: string;
    zhLabel: string;
    zhUsageTip?: string;
    runnable: 'ready' | 'needs_api' | 'needs_media' | 'needs_compute';
    recommend: boolean;
  };

  /** 流水线步骤:算子名 + 参数 */
  type PipelineStep = {
    name: string;
    params: Record<string, unknown>;
  };

  /** AI 生成流水线入参 */
  type GeneratePipelineParams = {
    goal: string;
    datasetVersionId?: string;
  };

  /** AI 生成流水线结果 */
  type GeneratedPipeline = {
    operators: PipelineStep[];
    explanation: string;
  };

  /** 样例试跑结果 */
  type PreviewResult = {
    before: Record<string, any>[];
    after: Record<string, any>[];
    beforeCount: number;
    afterCount: number;
    columns: string[];
  };

  /** 算子市场:目录查询参数 */
  type OperatorCatalogParams = {
    scenario?: string;
    category?: string;
    modality?: string;
    resourceClass?: string;
    runnable?: string;
    recommend?: boolean;
    keyword?: string;
    current?: number;
    pageSize?: number;
  };

  /** 算子市场:目录概览(各维度分布) */
  type OperatorCatalogMeta = {
    total: number;
    withDetailPage: number;
    recommended: number;
    byCategory: Record<string, number>;
    byResourceClass: Record<string, number>;
    byModality: Record<string, number>;
    byScenario: Record<string, number>;
    byRunnable: Record<string, number>;
  };

  /** 加工任务 */
  type Job = {
    id: string;
    name: string;
    type: string;
    state: 'pending' | 'running' | 'success' | 'failed';
    progress: number;
    error?: string;
    configYaml?: string;
    createdAt: string;
    startedAt?: string;
    finishedAt?: string;
    output?: IngestOutput;
    /** 输入数据集版本（通过 job_input 血缘反查；quality 任务 output 为空） */
    input?: IngestOutput;
  };

  /** 新建加工任务入参 */
  type JobCreate = {
    name: string;
    type?: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
  };

  /** 新建质量评估任务入参 */
  type QualityJobCreate = {
    name: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
  };

  /** 逐条质量得分行 */
  type VersionStatsRow = {
    index: number;
    text: string;
    stats: Record<string, any>;
  };

  /** 逐条质量得分响应 */
  type VersionStatsResult = {
    data: VersionStatsRow[];
    total: number;
    metrics: string[];
    success: boolean;
    message?: string;
  };

  /** 质量报告直方图分桶 */
  type HistogramBucket = {
    x0: number;
    x1: number;
    count: number;
  };

  /** 质量报告单指标统计 */
  type QualityMetric = {
    name: string;
    count: number;
    mean: number;
    min: number;
    max: number;
    p25: number;
    p50: number;
    p75: number;
    histogram: HistogramBucket[];
  };

  /** 质量分析报告 */
  type QualityReport = {
    rows: number;
    metrics: QualityMetric[];
  };

  /** 数据集版本（不可变快照） */
  type DatasetVersion = {
    id: string;
    datasetId: string;
    versionNo: number;
    storageUri: string;
    statsUri?: string;
    format: string;
    rows?: number;
    size?: number;
    origin: string;
    producedByJobId?: string;
    note?: string;
    createdAt: string;
  };

  /** 数据集（元信息） */
  type Dataset = {
    id: string;
    name: string;
    description?: string;
    dataType?: string;
    sensitivityLevel?: string;
    businessCategory?: string;
    owner: string;
    creator: string;
    lastModifier?: string;
    validUntil?: string;
    createdAt: string;
    updatedAt: string;
  };

  /** 数据集详情（含版本列表） */
  type DatasetDetail = Dataset & { versions: DatasetVersion[] };

  /** 数据集元数据更新入参 */
  type DatasetUpdate = {
    name?: string;
    // 可清空字段:显式传 null 才能把旧值置空(后端 exclude_unset 保留显式 null)
    description?: string | null;
    dataType?: string | null;
    sensitivityLevel?: string | null;
    businessCategory?: string | null;
    validUntil?: string | null;
  };

  /** 数据集列表查询参数 */
  type DatasetListParams = {
    current?: number;
    pageSize?: number;
    name?: string;
    dataType?: string;
    creator?: string;
    createdStart?: string;
    createdEnd?: string;
  };

  /** 版本数据预览 */
  type DatasetPreview = {
    data: Record<string, any>[];
    columns: string[];
    total: number;
    success: boolean;
    message?: string;
  };

  /** 上传记录 */
  type UploadRecord = {
    id: string;
    filename: string;
    size: number;
    format: string;
    status: 'done' | 'error';
    uploadedAt: string;
  };

  /** 推断出的字段 */
  type SchemaField = {
    name: string;
    type: string;
    example: string;
    nullable: boolean;
  };

  /** 推断出的 schema */
  type InferredSchema = {
    format: string;
    confidence: number;
    fields: SchemaField[];
    suggestion: string;
    recommendedConfig?: Record<string, any>;
  };

  /** AI 生成的采集任务配置 */
  type GeneratedTaskConfig = {
    name: string;
    datasourceType: DataSourceType;
    schedule: IngestSchedule;
    config: Record<string, any>;
    explanation: string;
  };

  /** 分页查询通用响应 */
  type PageResult<T> = {
    data: T[];
    total: number;
    success: boolean;
  };

  /** 数据源列表查询参数 */
  type DataSourceListParams = {
    current?: number;
    pageSize?: number;
    name?: string;
    type?: DataSourceType;
  };

  /** 采集任务列表查询参数 */
  type IngestTaskListParams = {
    current?: number;
    pageSize?: number;
    name?: string;
    status?: IngestTask['status'];
  };

  /** 上传记录列表查询参数 */
  type UploadListParams = {
    current?: number;
    pageSize?: number;
  };

  /** 新建数据源入参 */
  type DataSourceCreate = {
    name: string;
    type: DataSourceType;
    dbKind?: DbKind;
    config: Record<string, any>;
    description?: string;
  };

  /** 更新数据源入参 */
  type DataSourceUpdate = Partial<DataSourceCreate> & {
    status?: DataSource['status'];
  };

  /** 测试连接入参 */
  type TestConnectionParams = {
    type: DataSourceType;
    dbKind?: DbKind;
    config: Record<string, any>;
  };

  /** 测试连接结果 */
  type TestConnectionResult = {
    success: boolean;
    latencyMs: number;
    message: string;
  };

  /** 新建采集任务入参 */
  type IngestTaskCreate = {
    name: string;
    datasourceId: string;
    schedule: IngestSchedule;
    extract?: IngestExtract;
  };

  /** 单个上传记录响应 */
  type UploadResult = {
    data: UploadRecord;
    success: boolean;
  };

  /** 推断 schema 响应 */
  type InferSchemaResult = {
    data: InferredSchema;
    success: boolean;
  };

  /** 生成采集任务响应 */
  type GenerateTaskResult = {
    data: GeneratedTaskConfig;
    success: boolean;
  };

  /** AI 问答响应 */
  type QaResult = {
    data: { answer: string };
    success: boolean;
  };
}
