// 任务状态枚举 + 渲染辅助:从 processing 页抽出来,供 distillation 等新模块共用。
// 与后端 Job.state 严格对齐(pending/running/success/failed/cancelled)。
import { Tag } from 'antd';

export const STATE_META: Record<
  DataPlatform.Job['state'],
  { text: string; color: string }
> = {
  pending: { text: '待运行', color: 'default' },
  running: { text: '运行中', color: 'processing' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
  cancelled: { text: '已取消', color: 'warning' },
};

/** 任务状态 Tag 渲染:为 undefined 时落回 'default'。 */
export const renderState = (s: DataPlatform.Job['state']) => {
  const m = STATE_META[s] ?? { text: s, color: 'default' };
  return <Tag color={m.color}>{m.text}</Tag>;
};

/** 产物版本摘要:`datasetName（rows 行 · datasetId versionLabel）`,空则 '-'。 */
export const renderOutput = (o?: DataPlatform.IngestOutput) =>
  o
    ? `${o.datasetName}（${o.rows ?? '-'} 行 · ${o.datasetId} ${o.versionLabel ?? `v${o.versionNo}`}）`
    : '-';
