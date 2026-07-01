import { Tag } from 'antd';

/** 训练用途枚举元数据，对齐后端 TrainType(semantic_registry.py)。 */
export const TRAIN_TYPE_META: Record<string, { label: string; color: string }> =
  {
    pretrain: { label: '预训练', color: 'orange' },
    sft: { label: '监督微调 SFT', color: 'blue' },
    distill: { label: '蒸馏', color: 'purple' },
    dpo: { label: '偏好对齐 DPO', color: 'magenta' },
    rlhf: { label: '强化学习 RLHF', color: 'volcano' },
    eval: { label: '评估', color: 'cyan' },
    custom: { label: '自定义', color: 'default' },
  };

/** ProTable / ProFormSelect 用的 valueEnum */
export const TRAIN_TYPE_ENUM: Record<string, { text: string }> =
  Object.fromEntries(
    Object.entries(TRAIN_TYPE_META).map(([k, v]) => [k, { text: v.label }]),
  );

/** 统一的训练用途标签，未知值回退原值。 */
export const TrainTypeTag: React.FC<{ type?: string | null }> = ({ type }) => {
  if (!type) return <>-</>;
  const meta = TRAIN_TYPE_META[type];
  if (!meta) return <Tag>{type}</Tag>;
  return <Tag color={meta.color}>{meta.label}</Tag>;
};
