// @ts-ignore
/* eslint-disable */

declare namespace DataPlatform {
  /** 当前登录用户的自助资料(GET/PUT /api/v1/profile) */
  type Profile = {
    id: string;
    username: string;
    displayName: string;
    role: string;
  };

  /** 受控分类（多级树，邻接表 parentId，根为 null；跨实体共享，#15） */
  type Category = {
    id: string;
    name: string;
    parentId?: string | null;
    note?: string | null;
    creator: string;
    createdAt: string;
    /** 三实体引用该分类的总数（供删除守卫与管理页展示） */
    usageCount: number;
    /** 直接子分类（GET /categories 返回嵌套树时填充） */
    children?: Category[];
  };

  /** 新建分类入参（parentId 省略/为 null → 根分类） */
  type CategoryCreate = {
    name: string;
    parentId?: string | null;
    note?: string;
  };

  /** 更新分类入参 */
  type CategoryUpdate = {
    name?: string;
    parentId?: string | null;
    note?: string;
  };

  /** 标签（全局标签池,扁平,仅挂数据集多对多） */
  type Tag = {
    id: string;
    name: string;
    /** 引用该标签的数据集数（管理页展示） */
    usageCount: number;
    createdAt: string;
  };
  type TagCreate = { name: string };
  type TagUpdate = { name: string };
  type TagMerge = { sourceId: string; targetId: string };
  type TagBatchDelete = { ids: string[] };

  /** 数据源类型 */
  type DataSourceType = 's3' | 'hdfs' | 'database' | 'api';

  /** 语义类型（与 dataType 功能键正交，承载 10 类 LLM 数据语义，见 docs/plan/14） */
  type SemanticType =
    | 'text'
    | 'structured'
    | 'unstructured'
    | 'multimodal'
    | 'cot'
    | 'qa'
    | 'preference'
    | 'timeseries'
    | 'gis'
    | 'fusion';

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

  /** 采集对象（拉什么）：勾选的表（每张表一数据集）、自定义 SQL 或路径/Glob（s3/hdfs） */
  type IngestExtract = {
    mode: 'table' | 'sql' | 'path';
    tables?: string[];
    sql?: string;
    /** mode='path' 时：显式路径列表（s3 key 或 hdfs 路径） */
    paths?: string[];
    /** mode='path' 时：glob 匹配模式（与 paths 二选一或叠加） */
    glob?: string;
    /** table 模式勾选的列(裁剪落地);仅整表模式 */
    columns?: string[];
  };

  /** 采集质量门结论(切片 B)：skipped=未配置策略 / passed=通过 / failed=未通过 */
  type QualityVerdict = 'skipped' | 'passed' | 'failed';

  /** 任务级采集质量策略(切片 B)：空值率阈值 + schema 漂移阻断，均可空 */
  type QualityPolicy = {
    /** 单列最大允许 null 率，闭区间 [0,1]；undefined 表示不做此项检查 */
    maxNullRate?: number;
    /** 与历史 schema 快照比较出现 drift 时是否阻断（true=阻断/标 failed） */
    blockOnSchemaDrift?: boolean;
  };

  /** 增量采集配置(切片 C)：两种互斥形二选一(与后端 Incremental 同形)。
   *  - 库形: {column, type}        按 DB 列水位推进(timestamp/integer)
   *  - 文件形: {by}                 按 S3/HDFS 对象 mtime 或 name 推进
   *  undefined = 全量采集(无增量)。前端按 datasource.type 决定渲染哪种形。
   *  后端 model_validator 拒绝混合 / 全空;前端 UI 只暴露其中一种,避免误填。 */
  type Incremental =
    | { column: string; type: 'timestamp' | 'integer' }
    | { by: 'mtime' | 'name' };

  /** 增量水位快照(切片 C,后端写回 task.watermark JSONB;前端只读展示)。
   *  - value: 当前高水位字符串(DB 列值 / 对象 mtime ISO / 对象 key)
   *  - updatedAt: 后端推进水位的时刻(UTC ISO)
   *  ⚠️ 后端 IngestTaskRead 当前未暴露 watermark,本字段为前向兼容占位:
   *     backend 补读模型字段前,展示层会自然降级为「-」。 */
  type Watermark = {
    value: string;
    updatedAt?: string;
  };

  /** 采集质量统计·单列条目（与后端 ingest_quality.compute_quality_stats 对齐） */
  type QualityStatColumn = {
    name: string;
    type: string;
    null_rate: number;
  };

  /** 采集质量统计（版本级聚合） */
  type QualityStats = {
    rows: number;
    columns: QualityStatColumn[];
  };

  /** 采集时表结构快照条目（用于后续 schema drift 比对） */
  type SchemaSnapshotEntry = {
    name: string;
    type: string;
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
    /** 质量门结论(切片 B)：undefined 视为 skipped（兼容老数据） */
    qualityVerdict?: QualityVerdict;
    /** 列空值率统计(仅 verdict≠skipped 时由后端填充) */
    qualityStats?: QualityStats;
    /** 采集时的列结构快照(用于漂移比对；前端据此渲染 drift 提示) */
    schemaSnapshot?: SchemaSnapshotEntry[];
    /** 该版本所在运行的触发来源(切片 C):manual=rerun 手动 / cron=调度器自动。
     *  undefined 兼容老数据(后端 IngestRunRead 未暴露 trigger 时降级为不显示)。 */
    trigger?: 'manual' | 'cron';
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
    /** 任务级质量策略(切片 B)：未配置时为 undefined */
    qualityPolicy?: QualityPolicy;
    /** 增量采集配置(切片 C)：undefined = 全量采集 */
    incremental?: Incremental;
    /** 当前增量水位(切片 C,后端推进;前端只读展示)。
     *  后端 IngestTaskRead 暂未暴露,backend 补字段前展示层降级为「-」。 */
    watermark?: Watermark;
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
    /** 触发来源(切片 C):manual=rerun 手动 / cron=调度器自动。
     *  undefined 兼容老数据 / 后端未暴露 trigger 时降级为不显示。 */
    trigger?: 'manual' | 'cron';
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

  /** 当前环境探测到的执行能力(决定 GPU/LLM/vLLM/Ray 类算子是否可运行) */
  type OperatorCapabilities = {
    cuda: boolean;
    vllm: boolean;
    ray: boolean;
    llm: boolean;
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

  /** AI 命名入参（据文件名/格式/分类建议数据集名） */
  type SuggestDatasetNameParams = {
    filenames: string[];
    dataType: string;
    category?: string;
  };

  /** AI 命名结果 */
  type SuggestedDatasetName = {
    name: string;
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
    /** 业务桶:cleansing/distillation/make/augment——任务编辑器按此只展示对应算子 */
    bucket?: string;
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
    state:
      | 'pending'
      | 'running'
      | 'paused'
      | 'success'
      | 'failed'
      | 'cancelled';
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
    /** 可暂停(pending/running)——由后端 JobRead.can_pause 派生 */
    canPause?: boolean;
    /** 可继续(仅 paused)——由后端 JobRead.can_resume 派生 */
    canResume?: boolean;
    /** 可停止(pending/running/paused)——由后端 JobRead.can_stop 派生 */
    canStop?: boolean;
  };

  /** 新建加工任务入参 */
  type JobCreate = {
    name: string;
    type?: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
    /** 清洗作用字段(DJ text_keys):留空后端自动探测主文本字段;
     *  显式指定(可多字段)用于脏字符不在标准字段(如 task)的场景 */
    textKeys?: string[];
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

  /** 数据蒸馏目标(任务级参数) */
  type DistillationGoal = {
    keepRatio?: number;
    keepNum?: number;
    scoreField?: string;
    fallbackRandom?: boolean;
    enableDedup?: boolean;
    enableScoreFilter?: boolean;
  };
  /** 新建数据蒸馏任务入参 */
  type DistillationJobCreate = {
    name: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
    goal: DistillationGoal;
    outputDatasetId?: string;
  };
  /** 蒸馏报告(任务跑完后) */
  type DistillationReport = {
    jobId: string;
    inputVersionId: string;
    outputVersionId?: string;
    inputCount: number;
    outputCount?: number;
    keepRatioActual?: number;
    dedupRemoved?: number;
    filterRemoved?: number;
    elapsedSeconds?: number;
    operatorChain: string[];
    warnings: string[];
    raw?: Record<string, any>;
  };

  /** 数据合成(make)目标(任务级参数) */
  type MakeGoal = {
    mode?: 'synthesize' | 'make';
    targetPerSample?: number;
    targetTotal?: number;
    note?: string;
  };
  /** 新建合成任务入参 */
  type MakeJobCreate = {
    name: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
    goal: MakeGoal;
    outputDatasetId?: string;
  };
  /** 合成报告 */
  type MakeReport = {
    jobId: string;
    inputVersionId: string;
    outputVersionId?: string;
    mode: string;
    inputCount: number;
    outputCount?: number;
    expansionRatio?: number;
    elapsedSeconds?: number;
    operatorChain: string[];
    warnings: string[];
    raw?: Record<string, any>;
  };

  /** 数据增强(augment)目标 */
  type AugmentGoal = {
    mode?: 'augment';
    targetPerSample?: number;
    targetTotal?: number;
    note?: string;
  };
  /** 新建增强任务入参 */
  type AugmentJobCreate = {
    name: string;
    datasetVersionId: string;
    operators: { name: string; params?: Record<string, any> }[];
    goal: AugmentGoal;
    outputDatasetId?: string;
  };
  /** 增强报告 */
  type AugmentReport = {
    jobId: string;
    inputVersionId: string;
    outputVersionId?: string;
    mode: string;
    inputCount: number;
    outputCount?: number;
    expansionRatio?: number;
    elapsedSeconds?: number;
    operatorChain: string[];
    warnings: string[];
    raw?: Record<string, any>;
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

  /** dj-analyze 产出的分析报告(analysis/ 目录):overall.csv 聚合表 + PNG 清单 */
  type AnalysisReport = {
    /** overall.csv:首行表头,其余为数据行(首列是指标名) */
    overall: { columns: string[]; rows: string[][] } | null;
    images: { name: string; kind: 'distributions' | 'correlation' | 'other' }[];
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
    /** 语义类型快照(与 dataType 正交,#1/#2/#8) */
    semanticType?: SemanticType;
    /** 多模态模态集合(images/audios/videos/text 子集);仅 multimodal 版本有值 */
    modalities?: string[];
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
    /** 接入/格式功能键(分栏过滤用,free-string;已退出展示层) */
    dataType?: string;
    /** 语义类型(与 dataType 正交,#1/#2/#8) */
    semanticType?: SemanticType;
    /** 展示版本的多模态模态集合(后端聚合填充);前端按其分类显示子标签 + 筛选 */
    modalities?: string[];
    /** 来源/接入方式(类型三轴之一):database|object_store|hdfs|local_upload|api_push */
    sourceKind?: string;
    /** 原始格式(类型三轴之一):txt/docx/csv/jsonl/image… */
    sourceFormat?: string;
    categoryId?: string | null;
    categoryName?: string | null;
    owner: string;
    creator: string;
    lastModifier?: string;
    validUntil?: string;
    /** 是否含外部 S3 托管版本(#18)：true 时隐藏删除、改显「取消托管」并打「S3 托管」徽标 */
    hosted?: boolean;
    /** 最新版本展示标签(如 v2026.6.21 (#1));无版本时为 null */
    latestVersionLabel?: string | null;
    createdAt: string;
    updatedAt: string;
    /** 标签（多对多，自由输入） */
    tags?: string[];
    /** 当前用户对该数据集的 ACL 级别(详情 GET 回填);null/缺失=无权限。
     *  仅 'admin' 时展示「权限管理」入口。后端补齐前可能 undefined,故可选。 */
    myLevel?: AclLevel | null;
  };

  /** 数据集 ACL 授权级别 */
  type AclLevel = 'view' | 'edit' | 'admin';

  /** 数据集 ACL 授权主体类型;'all' 表示组织内所有人 */
  type AclSubjectType = 'user' | 'role' | 'all';

  /** 数据集 ACL 授权条目 */
  type DatasetAcl = {
    id: string;
    datasetId: string;
    subjectType: AclSubjectType;
    subjectId: string;
    /** 显示名:list 端点批量解析回填;all→"组织内所有人",缺失时前端降级用缓存/subjectId */
    subjectName?: string;
    level: AclLevel;
    createdAt: string;
  };

  /** ACL 授权对象候选(模糊搜索结果) */
  type AclCandidate = {
    id: string;
    name: string;
    type: 'user' | 'role';
  };

  /** 新增 ACL 授权入参 */
  type AclCreate = {
    subjectType: AclSubjectType;
    subjectId: string;
    level: AclLevel;
  };

  /** 血缘图节点:版本 或 任务 */
  type LineageNode = {
    id: string;
    kind: 'version' | 'job';
    createdAt: string;
    // version 字段
    datasetId?: string;
    datasetName?: string;
    versionNo?: number;
    versionLabel?: string;
    origin?: string;
    rows?: number;
    scanVerdict?: string;
    publishStatus?: string;
    /** 原始接入(无产出任务) */
    isOriginal?: boolean;
    /** 属于当前选中数据集(前端高亮) */
    isFocus?: boolean;
    // job 字段
    name?: string;
    jobType?: string;
    state?: string;
    /** 该任务执行的算子链(name+params,来自 job.spec.operators);review 类无 */
    operators?: { name: string; params: Record<string, any> }[];
  };

  /** 血缘边:输入版本 --input--> 任务 --output--> 产出版本 */
  type LineageEdge = { from: string; to: string; kind: 'input' | 'output' };

  type LineageGraph = { nodes: LineageNode[]; edges: LineageEdge[] };

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

  /** 导出已发布版本到外部 S3 数据源入参（下载/导出至 S3）：读源、写目标，不回写源 */
  type ExportS3Params = {
    datasourceId: string;
    bucket: string;
    prefix?: string;
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
    /** 语义类型:写入校验为枚举(非法 422);不传不改 */
    semanticType?: SemanticType;
    /** 多模态子类型:反写展示版本 modalities 合成代表值;仅 multimodal 时有意义 */
    modalitySubtype?: 'image' | 'video' | 'audio' | 'cross';
    // 受控分类:显式传 null 才能清空(后端 exclude_unset)
    categoryId?: string | null;
    validUntil?: string | null;
    /** 标签（多对多）；传入即全量替换，不传不动 */
    tags?: string[];
  };

  /** 数据集列表查询参数 */
  type DatasetListParams = {
    current?: number;
    pageSize?: number;
    name?: string;
    dataType?: string;
    semanticType?: SemanticType;
    /** 多模态子分类筛选:image|video|audio|cross(按展示版本 modalities 分类) */
    modality?: 'image' | 'video' | 'audio' | 'cross';
    sourceKind?: string;
    creator?: string;
    categoryId?: string;
    /** 选父含子筛选:逗号分隔的分类 id 列表(选中分类的全部后代 id),后端 IN 查询。*/
    categoryIds?: string;
    createdStart?: string;
    createdEnd?: string;
    /** 发布状态过滤：publishStatus=published 只返回含已发布版本的数据集（算法工程师消费视图） */
    publishStatus?: 'draft' | 'published' | 'unpublished';
    /** 标签过滤（逗号分隔，OR：含任一即命中） */
    tags?: string;
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
    /** 任务级质量策略(切片 B)：undefined/空对象 = 不做质量门检查 */
    qualityPolicy?: QualityPolicy;
    /** 增量采集配置(切片 C)：undefined = 全量采集 */
    incremental?: Incremental;
  };

  /** 源数据预览：单列描述 */
  type IngestPreviewColumn = { name: string; type: string };
  /** 源数据预览响应（采集配置期采样，无副作用） */
  type IngestSourcePreview = {
    columns: IngestPreviewColumn[];
    rows: Record<string, any>[];
    truncated: boolean;
    sampledFrom: string;
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

  /** LLM 供应商配置 */
  interface LlmProvider {
    id: string;
    name: string;
    /** 供应商类型 */
    provider: 'deepseek' | 'glm' | 'minimax' | 'openai' | 'custom';
    baseUrl: string;
    model: string;
    /** 掩码后的 API Key，如 "sk-1…abcd" 或 "未配置" */
    apiKeyMasked: string;
    isActive: boolean;
    createdAt: string;
    updatedAt: string;
  }

  interface LlmProviderCreate {
    name: string;
    provider: 'deepseek' | 'glm' | 'minimax' | 'openai' | 'custom';
    baseUrl: string;
    apiKey: string;
    model: string;
  }

  interface LlmProviderUpdate {
    name?: string;
    provider?: 'deepseek' | 'glm' | 'minimax' | 'openai' | 'custom';
    baseUrl?: string;
    /** 留空则不修改 */
    apiKey?: string;
    model?: string;
  }

  /** 用未保存的配置测试连通性（新建/编辑对话框保存前校验） */
  interface LlmProviderTest {
    baseUrl: string;
    apiKey: string;
    model: string;
  }

  interface LlmTestResult {
    success: boolean;
    latencyMs: number;
    message: string;
    model: string;
  }

  /** 供应商下一个可选模型 */
  interface LlmModel {
    id: string;
    providerId: string;
    model: string;
    /** 来源:fetched=接口拉取 / manual=手动添加 */
    source: 'fetched' | 'manual';
    createdAt: string;
  }

  /** 「获取模型」拉取结果 */
  interface LlmFetchModelsResult {
    /** 拉取是否成功;false 时前端走预置清单兜底 */
    success: boolean;
    message: string;
    /** 本次新增模型数 */
    added: number;
    models: LlmModel[];
  }

  interface LlmUsageByFeature {
    feature: string;
    calls: number;
    tokens: number;
  }

  interface LlmUsageByDay {
    day: string;
    calls: number;
    tokens: number;
  }

  interface LlmUsageRecent {
    feature: string;
    model: string;
    totalTokens: number;
    success: boolean;
    latencyMs: number;
    createdAt: string;
  }

  interface LlmUsageSummary {
    totalCalls: number;
    totalTokens: number;
    promptTokens: number;
    completionTokens: number;
    /** 0..1 */
    successRate: number;
    byFeature: LlmUsageByFeature[];
    byDay: LlmUsageByDay[];
    recent: LlmUsageRecent[];
  }

  /** 站内通知条目 */
  type NotificationItem = {
    id: string;
    level: 'success' | 'error';
    sourceType: 'job' | 'ingest_task';
    sourceId: string;
    title: string;
    body: string | null;
    read: boolean;
    createdAt: string;
    readAt: string | null;
  };
}
