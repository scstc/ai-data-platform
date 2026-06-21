/** 数据源类型 / 数据库品牌的中文映射与选项常量 */

/** 数据源类型 → 中文名 + Tag 颜色 */
export const TYPE_META: Record<
  DataPlatform.DataSourceType,
  { label: string; color: string }
> = {
  s3: { label: 'S3 对象存储', color: 'geekblue' },
  hdfs: { label: 'HDFS', color: 'cyan' },
  database: { label: '数据库', color: 'purple' },
  api: { label: 'API 推送', color: 'gold' },
};

/**
 * 数据库品牌 → 中文名
 *
 * 分组说明（对应后端连接器档位，见 docs/plan/14 §4.1）：
 *   可真连：postgresql（asyncpg 真测）、goldendb（asyncmy，有本地 MySQL 可测）
 *   品牌承诺级：hologres / kingbase / gaussdb（PG 线协议，代码路径同 postgresql；需真库验证）
 *   结构就绪：dameng / sequoiadb / hive / doris（驱动懒加载，未装驱动时诚实返回 not-ready）
 */
export const DB_KIND_LABEL: Record<DataPlatform.DbKind, string> = {
  // ── 可真连 ──────────────────────────────────────────────────────────────
  postgresql: 'PostgreSQL',
  goldendb: 'GoldenDB（MySQL 兼容）',
  // ── 品牌承诺级（PG 线协议，需真库验证）────────────────────────────────
  hologres: '阿里 Hologres（承诺级）',
  kingbase: '人大金仓 KingbaseES（承诺级）',
  gaussdb: '华为 GaussDB（承诺级）',
  // ── 结构就绪（驱动未装时返回明确提示，不伪造成功）────────────────────
  dameng: '达梦 DM（结构就绪）',
  sequoiadb: '巨杉 SequoiaDB（结构就绪）',
  hive: 'Apache Hive（结构就绪）',
  doris: 'Apache Doris（结构就绪）',
};

/** 数据库品牌下拉选项 */
export const DB_KIND_OPTIONS = (
  Object.keys(DB_KIND_LABEL) as DataPlatform.DbKind[]
).map((value) => ({ value, label: DB_KIND_LABEL[value] }));

/** 状态 → Badge 文案与状态色 */
export const STATUS_META: Record<
  DataPlatform.DataSource['status'],
  { label: string; status: 'success' | 'error' | 'default' }
> = {
  connected: { label: '已连接', status: 'success' },
  failed: { label: '失败', status: 'error' },
  pending: { label: '待验证', status: 'default' },
};

/** 新建向导四张类型卡片 */
export const TYPE_CARDS: {
  type: DataPlatform.DataSourceType;
  title: string;
  desc: string;
}[] = [
  {
    type: 's3',
    title: 'S3 兼容对象存储',
    desc: '对接 AWS S3 / MinIO 等对象存储桶',
  },
  { type: 'hdfs', title: 'HDFS', desc: '对接 Hadoop 分布式文件系统' },
  {
    type: 'database',
    title: '数据库直连',
    desc: '达梦 / Hive / Doris 等 8 种数据库',
  },
  { type: 'api', title: 'API 推送', desc: '由外部系统主动推送数据到平台' },
];

// ---------------------------------------------------------------------------
// 接入方式选择落地页(/ingest/datasources/new):按类目分组的卡片
// ---------------------------------------------------------------------------

/** S3 兼容对象存储的厂商档(后端同为 type=s3,仅前端呈现/默认 Endpoint 不同) */
export type S3Provider = 's3' | 'minio' | 'oss' | 'obs';

export const S3_PROVIDERS: Record<
  S3Provider,
  {
    title: string;
    endpointPlaceholder: string;
    /** AK/SK 字段标签(OBS 习惯用 AK/SK 叫法) */
    akLabel: string;
    skLabel: string;
  }
> = {
  s3: {
    title: '配置 S3 对象存储连接',
    endpointPlaceholder: 'https://s3.amazonaws.com',
    akLabel: 'Access Key ID',
    skLabel: 'Secret Key',
  },
  minio: {
    title: '配置 MinIO 连接',
    endpointPlaceholder: 'http://minio.internal:9000',
    akLabel: 'Access Key',
    skLabel: 'Secret Key',
  },
  oss: {
    title: '配置阿里云 OSS 连接',
    endpointPlaceholder: 'oss-cn-hangzhou.aliyuncs.com',
    akLabel: 'Access Key ID',
    skLabel: 'Access Key Secret',
  },
  obs: {
    title: '配置华为云 OBS 连接',
    endpointPlaceholder: 'obs.cn-north-4.myhuaweicloud.com',
    akLabel: 'Access Key ID (AK)',
    skLabel: 'Secret Access Key (SK)',
  },
};

/** 云 / 分布式存储 — 大卡片(S3 / 阿里云 OSS / 华为云 OBS 分开,均落 s3 配置页) */
export const STORAGE_CARDS: {
  key: S3Provider | 'hdfs';
  route: string;
  title: string;
  desc: string;
  tag: string;
}[] = [
  {
    key: 's3',
    route: '/ingest/datasources/new/s3',
    title: 'Amazon S3',
    desc: '通用 S3 协议对象存储,适合分布式数据集与日志的零拷贝接入。',
    tag: 'S3 COMPATIBLE',
  },
  {
    key: 'minio',
    route: '/ingest/datasources/new/s3?provider=minio',
    title: 'MinIO',
    desc: '自建 S3 兼容对象存储,适合私有化部署的数据湖。',
    tag: 'SELF-HOSTED',
  },
  {
    key: 'oss',
    route: '/ingest/datasources/new/s3?provider=oss',
    title: '阿里云 OSS',
    desc: '阿里云对象存储服务,S3 兼容协议接入。',
    tag: 'ALIYUN',
  },
  {
    key: 'obs',
    route: '/ingest/datasources/new/s3?provider=obs',
    title: '华为云 OBS',
    desc: '华为云对象存储服务,S3 兼容协议接入。',
    tag: 'HUAWEI',
  },
  {
    key: 'hdfs',
    route: '/ingest/datasources/new/hdfs',
    title: 'HDFS',
    desc: '直接从 Hadoop 分布式文件系统集群接入。',
    tag: 'BIG DATA',
  },
];

/** 数据库连接 — 网格小卡片(承接 DB_KIND_LABEL 全部品牌) */
export const DB_KIND_CARDS: { kind: DataPlatform.DbKind; title: string }[] = [
  { kind: 'dameng', title: 'DM(达梦)' },
  { kind: 'goldendb', title: 'GoldenDB' },
  { kind: 'kingbase', title: 'Kingbase(金仓)' },
  { kind: 'gaussdb', title: 'GaussDB' },
  { kind: 'hologres', title: 'Hologres' },
  { kind: 'sequoiadb', title: 'SequoiaDB(巨杉)' },
  { kind: 'hive', title: 'Hive' },
  { kind: 'doris', title: 'Doris' },
  { kind: 'postgresql', title: 'PostgreSQL' },
];

/** 配置页标题(按类型) */
export const CONFIG_TITLE: Record<DataPlatform.DataSourceType, string> = {
  s3: '配置 S3 对象存储连接',
  hdfs: '配置 HDFS 连接',
  database: '配置数据库连接',
  api: 'API 推送接入配置',
};
