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
