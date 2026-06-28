import {
  ApartmentOutlined,
  BranchesOutlined,
  CommentOutlined,
  ContainerOutlined,
  DeploymentUnitOutlined,
  EnvironmentOutlined,
  FileTextOutlined,
  HeartOutlined,
  LineChartOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type { ReactNode } from 'react';

/** 数据集语义类型(与 dataType 正交,承载 LLM 数据语义)的统一元数据。
 *  列表列、详情页、按类型分发的数据视图统一从这里取标签/颜色/图标,避免各处漂移。
 *  对齐后端 SemanticType(semantic_registry.py)。 */
export type SemanticTypeMeta = {
  label: string;
  /** antd Tag color(预设色名) */
  color: string;
  icon: ReactNode;
};

export const SEMANTIC_TYPE_META: Record<string, SemanticTypeMeta> = {
  text: { label: '文本', color: 'orange', icon: <FileTextOutlined /> },
  structured: { label: '结构化', color: 'geekblue', icon: <TableOutlined /> },
  unstructured: {
    label: '非结构化',
    color: 'lime',
    icon: <ContainerOutlined />,
  },
  multimodal: { label: '多模态', color: 'purple', icon: <ApartmentOutlined /> },
  cot: { label: 'COT 思维链', color: 'blue', icon: <BranchesOutlined /> },
  qa: { label: 'QA 问答对', color: 'cyan', icon: <CommentOutlined /> },
  preference: { label: '偏好', color: 'magenta', icon: <HeartOutlined /> },
  timeseries: { label: '时序', color: 'green', icon: <LineChartOutlined /> },
  gis: { label: 'GIS 位置', color: 'volcano', icon: <EnvironmentOutlined /> },
  fusion: { label: '融合', color: 'gold', icon: <DeploymentUnitOutlined /> },
};

/** ProTable 列筛选用的 valueEnum(key → { text }) */
export const SEMANTIC_TYPE_ENUM: Record<string, { text: string }> =
  Object.fromEntries(
    Object.entries(SEMANTIC_TYPE_META).map(([k, v]) => [k, { text: v.label }]),
  );

/** 统一的语义类型标签:图标 + 中文标签 + 预设色。未知类型回退原值。 */
export const SemanticTypeTag: React.FC<{
  type?: string | null;
  /** 仅图标不显示文字(紧凑场景) */
  iconOnly?: boolean;
}> = ({ type, iconOnly }) => {
  if (!type) return <>-</>;
  const meta = SEMANTIC_TYPE_META[type];
  if (!meta) return <Tag>{type}</Tag>;
  return (
    <Tag color={meta.color} icon={meta.icon}>
      {iconOnly ? null : meta.label}
    </Tag>
  );
};
