// @ts-ignore
/* eslint-disable */

declare namespace DataPlatform {
  /** 受控分类（扁平单层，跨实体共享，#15） */
  type Category = {
    id: string;
    name: string;
    note?: string | null;
    creator: string;
    createdAt: string;
    /** 三实体引用该分类的总数（供删除守卫与管理页展示） */
    usageCount: number;
  };

  /** 新建分类入参 */
  type CategoryCreate = {
    name: string;
    note?: string;
  };

  /** 更新分类入参 */
  type CategoryUpdate = {
    name?: string;
    note?: string;
  };

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
    categoryId?: string | null;
    categoryName?: string | null;
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
    /** 版本展示标签:v2026.6.16 (#5)（后端按创建日期+内部版本号生成） */
    versionLabel?: string;
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
    categoryId?: string | null;
    categoryName?: string | null;
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
    state: 'pending' | 'running' | 'success' | 'failed' | 'cancelled';
    progress: number;
    error?: string;
    configYaml?: string;
    createdAt: string;
    startedAt?: string;
    finishedAt?: string;
    output?: IngestOutput;
    /** 输入数据集版本（通过 job_input 血缘反查；quality 任务 output 为空） */
    input?: IngestOutput;
    /** 是否可重跑（存有原始执行规格；早于重跑特性的任务为 false） */
    canRerun?: boolean;
  };

  /** 新建加工任务入参 */
  type JobCreate = {
    name: string;
    type?: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
    /** 产物去向:version=写回原数据集新版本(默认);new_dataset=另存为新数据集 */
    outputMode?: 'version' | 'new_dataset';
    /** outputMode=new_dataset 时的新数据集名称 */
    outputDatasetName?: string;
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
    /** 版本展示标签:v2026.6.16 (#5)（后端计算字段） */
    versionLabel: string;
    storageUri: string;
    statsUri?: string;
    format: string;
    rows?: number;
    size?: number;
    origin: string;
    producedByJobId?: string;
    /** 外部 S3 托管(origin=hosted)版本据此找 S3 凭证；受管版本为空 */
    sourceDatasourceId?: string;
    note?: string;
    /** 安全扫描结论(#4 发布门):unscanned=未扫描 / passed=通过 / failed=未通过 */
    scanVerdict?: 'unscanned' | 'passed' | 'failed';
    /** 结论来源:auto=自动判定 / manual=人工覆盖 */
    verdictSource?: 'auto' | 'manual';
    /** 人工覆盖理由 */
    verdictNote?: string;
    /** 发布状态:draft=草稿 / published=已发布 / unpublished=已下架 */
    publishStatus?: 'draft' | 'published' | 'unpublished';
    publishedAt?: string;
    createdAt: string;
  };

  /** 数据集（元信息） */
  type Dataset = {
    id: string;
    name: string;
    description?: string;
    dataType?: string;
    sensitivityLevel?: string;
    categoryId?: string | null;
    categoryName?: string | null;
    owner: string;
    creator: string;
    lastModifier?: string;
    validUntil?: string;
    /** 是否含外部 S3 托管版本(#18)：true 时隐藏删除、改显「取消托管」并打「S3 托管」徽标 */
    hosted?: boolean;
    createdAt: string;
    updatedAt: string;
  };

  /** 外部 S3 桶内对象（列对象接口返回项，#18） */
  type S3Object = {
    key: string;
    size: number;
    lastModified: string | null;
  };

  /** 托管 S3 数据入参（#18） */
  type HostS3Params = {
    datasourceId: string;
    bucket: string;
    keys: string[];
    name?: string;
    dataType?: string;
    categoryId?: string;
  };

  /** 文件管理零拷贝接入入参（数据接入）：把平台 MinIO 对象登记为受管数据集，不下载 */
  type PlatformHostParams = {
    bucket: string;
    keys: string[];
    name?: string;
    dataType?: string;
    categoryId?: string;
  };

  /** 数据集详情（含版本列表） */
  type DatasetDetail = Dataset & { versions: DatasetVersion[] };

  /** manifest 数据集的成员文件(一个媒体对象) */
  type DatasetMember = {
    name: string;
    key: string;
    bucket: string;
    format: string;
    size?: number;
  };

  /** 数据集元数据更新入参 */
  type DatasetUpdate = {
    name?: string;
    // 可清空字段:显式传 null 才能把旧值置空(后端 exclude_unset 保留显式 null)
    description?: string | null;
    dataType?: string | null;
    sensitivityLevel?: string | null;
    // 受控分类:显式传 null 才能清空(后端 exclude_unset)
    categoryId?: string | null;
    validUntil?: string | null;
  };

  /** 数据集列表查询参数 */
  type DatasetListParams = {
    current?: number;
    pageSize?: number;
    name?: string;
    dataType?: string;
    creator?: string;
    categoryId?: string;
    createdStart?: string;
    createdEnd?: string;
    /** 发布状态过滤：publishStatus=published 只返回含已发布版本的数据集（算法工程师消费视图） */
    publishStatus?: 'draft' | 'published' | 'unpublished';
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
    categoryId?: string;
  };

  /** 采集任务列表查询参数 */
  type IngestTaskListParams = {
    current?: number;
    pageSize?: number;
    name?: string;
    status?: IngestTask['status'];
    categoryId?: string;
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
    categoryId?: string;
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
    categoryId?: string;
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

  /** 审计日志（写操作记录，camelCase） */
  type AuditLog = {
    id: string;
    username: string;
    action: string;
    method: string;
    path: string;
    target?: string;
    statusCode: number;
    createdAt: string;
  };

  /** 审计日志列表查询参数 */
  type AuditLogListParams = {
    current?: number;
    pageSize?: number;
    username?: string;
    action?: string;
    method?: string;
    createdStart?: string;
    createdEnd?: string;
  };

  /** 内容安全:审核类别 */
  type ReviewCategory =
    | 'porn'
    | 'gambling'
    | 'drugs'
    | 'politics'
    | 'terrorism'
    | 'pii'
    | 'other';

  /** 内容安全:命中严重度 */
  type ReviewSeverity = 'high' | 'medium' | 'low';

  /** 内容安全:命中来源 */
  type ReviewSource = 'keyword' | 'regex' | 'flagged_words' | 'llm' | 'pii';

  /** 内容安全:自定义正则项 */
  type ReviewCustomRegex = {
    name: string;
    pattern: string;
  };

  /** 内容安全:审核任务配置 */
  type ReviewJobConfig = {
    categories: ReviewCategory[];
    customWords: string[];
    customRegex: ReviewCustomRegex[];
    useLlm: boolean;
    usePii: boolean;
    useFlaggedWords: boolean;
    sampleLimit?: number;
  };

  /** 内容安全:新建审核任务入参 */
  type ReviewJobCreate = {
    datasetVersionId: string;
    name?: string;
    config: ReviewJobConfig;
  };

  /** 内容安全:审核报告统计体 */
  type ReviewReportBody = {
    totalRows: number;
    scannedRows: number;
    flaggedRows: number;
    sampleLimitApplied: boolean;
    byCategory: Record<string, number>;
    bySeverity: Record<string, number>;
    bySource: Record<string, number>;
    warnings?: string[];
  };

  /** 内容安全:审核报告响应内层(后端 {data,success} 信封的 data 体) */
  type ReviewReport = {
    jobId?: string;
    name?: string;
    state: DataPlatform.Job['state'];
    error?: string;
    reviewReport?: ReviewReportBody;
    taggedVersionId?: string;
  };

  /** 内容安全:逐条命中记录 */
  type ReviewFinding = {
    rowIndex: number;
    category: ReviewCategory;
    severity: ReviewSeverity;
    source: ReviewSource;
    detail?: string;
    snippet?: string;
  };

  /** 内容安全:命中明细列表查询参数 */
  type ReviewFindingListParams = {
    current?: number;
    pageSize?: number;
    category?: ReviewCategory;
    source?: ReviewSource;
    severity?: ReviewSeverity;
  };

  /** 文件管理:对象条目（#10） */
  type FileEntry = {
    key: string;
    name: string;
    size: number;
    lastModified: string | null;
  };

  /** 文件管理:列目录结果（folders 在前，files 在后）（#10） */
  type FileListResult = {
    folders: string[];
    files: FileEntry[];
  };
}
