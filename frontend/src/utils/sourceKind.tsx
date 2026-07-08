import {
  ApiOutlined,
  CloudServerOutlined,
  ClusterOutlined,
  DatabaseOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type { ReactNode } from 'react';

/** 数据集来源/接入方式(类型三轴之一)的统一元数据。
 *  列表「来源」列、详情页统一从这里取标签/颜色/图标,避免各处漂移。
 *  对齐后端 source_kind 受控枚举(见 docs/superpowers/specs/2026-06-22-dataset-type-redesign-design.md)。 */
export type SourceKindMeta = {
  label: string;
  /** antd Tag color(预设色名) */
  color: string;
  icon: ReactNode;
};

export const SOURCE_KIND_META: Record<string, SourceKindMeta> = {
  database: { label: '数据库', color: 'blue', icon: <DatabaseOutlined /> },
  object_store: {
    label: '对象存储',
    color: 'cyan',
    icon: <CloudServerOutlined />,
  },
  hdfs: { label: 'HDFS', color: 'geekblue', icon: <ClusterOutlined /> },
  local: { label: '本地', color: 'green', icon: <UploadOutlined /> },
  api_push: { label: 'API 推送', color: 'purple', icon: <ApiOutlined /> },
};

/** ProTable 列筛选用的 valueEnum(key → { text }) */
export const SOURCE_KIND_ENUM: Record<string, { text: string }> =
  Object.fromEntries(
    Object.entries(SOURCE_KIND_META).map(([k, v]) => [k, { text: v.label }]),
  );

/** 统一的来源标签:图标 + 中文标签 + 预设色。空值回退 `-`,未知值回退原值。 */
export const SourceKindTag: React.FC<{ kind?: string | null }> = ({ kind }) => {
  if (!kind) return <>-</>;
  const meta = SOURCE_KIND_META[kind];
  if (!meta) return <Tag>{kind}</Tag>;
  return (
    <Tag color={meta.color} icon={meta.icon}>
      {meta.label}
    </Tag>
  );
};
