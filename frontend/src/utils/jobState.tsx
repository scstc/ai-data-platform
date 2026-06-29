// 任务状态枚举 + 渲染辅助:从 processing 页抽出来,供 distillation/data-tasks 等模块共用。
// 与后端 Job.state 严格对齐(pending/running/paused/success/failed/cancelled)。
import type { ProColumns } from '@ant-design/pro-components';
import { Tag } from 'antd';

export const STATE_META: Record<
  DataPlatform.Job['state'],
  { text: string; color: string }
> = {
  pending: { text: '待运行', color: 'default' },
  running: { text: '运行中', color: 'processing' },
  paused: { text: '已暂停', color: 'gold' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
  cancelled: { text: '已取消', color: 'warning' },
};

/** 任务状态 Tag 渲染:为 undefined 时落回 'default'。 */
export const renderState = (s: DataPlatform.Job['state']) => {
  const m = STATE_META[s] ?? { text: s, color: 'default' };
  return <Tag color={m.color}>{m.text}</Tag>;
};

/** 任务类型中文标签(与后端 Job.type 对齐)。供列表/详情/筛选共用,保持一致。 */
export const JOB_TYPE_LABEL: Record<string, string> = {
  clean: '数据清洗',
  distillation: '数据蒸馏',
  synthesis: '数据合成',
  augmentation: '数据增强',
  quality: '质量评估',
  review: '内容安全',
};

/** 任务类型 Tag 颜色。 */
export const JOB_TYPE_TAG_COLOR: Record<string, string> = {
  clean: 'blue',
  distillation: 'geekblue',
  synthesis: 'geekblue',
  augmentation: 'geekblue',
  quality: 'purple',
  review: 'magenta',
};

/** 任务类型 Tag 渲染:未知类型落回原始值 + default 色。 */
export const renderJobType = (type: string) => (
  <Tag color={JOB_TYPE_TAG_COLOR[type] ?? 'default'}>
    {JOB_TYPE_LABEL[type] ?? type}
  </Tag>
);

/** 产物版本摘要:`datasetName（rows 行 · datasetId versionLabel）`,空则 '-'。 */
export const renderOutput = (o?: DataPlatform.IngestOutput) =>
  o
    ? `${o.datasetName}（${o.rows ?? '-'} 行 · ${o.datasetId} ${o.versionLabel ?? `v${o.versionNo}`}）`
    : '-';

/** 版本号文案:优先 versionLabel,回落 `v<no>`,空则 '-'。 */
const versionText = (v?: DataPlatform.IngestOutput) =>
  v ? (v.versionLabel ?? `v${v.versionNo}`) : '-';

/** 治理/评估任务列表统一三列:数据集 / 输入版本 / 产物版本。
 *  数据集只显一次(输入/产物同源),版本列只显版本号,不再重复数据集名。
 *  供 data-tasks 与 governance 各任务列表(清洗/蒸馏/合成/增强)共用,保持列一致。 */
export function jobVersionColumns(): ProColumns<DataPlatform.Job>[] {
  return [
    {
      title: '数据集',
      dataIndex: ['input', 'datasetName'],
      width: 180,
      ellipsis: true,
      search: false,
      render: (_, r) => r.input?.datasetName ?? r.output?.datasetName ?? '-',
    },
    {
      title: '输入版本',
      dataIndex: 'input',
      width: 130,
      ellipsis: true,
      search: false,
      render: (_, r) => versionText(r.input),
    },
    {
      title: '产物版本',
      dataIndex: 'output',
      width: 130,
      ellipsis: true,
      search: false,
      render: (_, r) => versionText(r.output),
    },
  ];
}
