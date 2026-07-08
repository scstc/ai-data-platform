import {
  ApiOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type { ReactNode } from 'react';

/** 数据湖快照上传渠道(文件来源)的统一元数据。
 *  详情页快照表「来源」列从这里取标签/颜色/图标,视觉语言对齐数据集的
 *  SOURCE_KIND_META(见 @/utils/sourceKind),避免各处漂移。
 *  对齐后端 DataLakeSnapshot.upload_channel 受控枚举。 */
export type UploadChannelMeta = {
  label: string;
  /** antd Tag color(预设色名) */
  color: string;
  icon: ReactNode;
};

export const UPLOAD_CHANNEL_META: Record<
  DataPlatform.DataLakeUploadChannel,
  UploadChannelMeta
> = {
  database: { label: '数据库', color: 'blue', icon: <DatabaseOutlined /> },
  s3: { label: 'S3', color: 'cyan', icon: <CloudServerOutlined /> },
  oss: { label: 'OSS', color: 'cyan', icon: <CloudServerOutlined /> },
  obs: { label: 'OBS', color: 'cyan', icon: <CloudServerOutlined /> },
  minio: { label: 'MinIO', color: 'cyan', icon: <CloudServerOutlined /> },
  api: { label: 'API', color: 'purple', icon: <ApiOutlined /> },
  local: { label: '本地', color: 'green', icon: <UploadOutlined /> },
};

/** ProTable 列筛选用的 valueEnum(key → { text }) */
export const UPLOAD_CHANNEL_ENUM: Record<string, { text: string }> =
  Object.fromEntries(
    Object.entries(UPLOAD_CHANNEL_META).map(([k, v]) => [k, { text: v.label }]),
  );

/** 统一的来源标签:图标 + 中文标签 + 预设色。空值回退 `-`,未知值回退原值。 */
export const UploadChannelTag: React.FC<{
  channel?: DataPlatform.DataLakeUploadChannel | string | null;
}> = ({ channel }) => {
  if (!channel) return <>-</>;
  const meta =
    UPLOAD_CHANNEL_META[channel as DataPlatform.DataLakeUploadChannel];
  if (!meta) return <Tag>{channel}</Tag>;
  return (
    <Tag color={meta.color} icon={meta.icon}>
      {meta.label}
    </Tag>
  );
};
