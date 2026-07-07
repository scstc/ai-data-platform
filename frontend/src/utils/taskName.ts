// 任务名自动生成工具:统一的 <数据集名>-<任务类型> 格式。
// 用于 governance 下各任务编辑器(cleaning/quality/distillation/make/augment)。
// 用户在编辑器中输入的"任务名"字段会被这个函数实时预填——dataset/任务类型变化时
// 始终重算(用户可编辑,但下次变化会覆盖)。具体算子不进任务名,在任务详情中展示。

const MAX_DATASET_NAME = 40; // 数据集名超长时截断

/** 把任意字符串清洗成文件名安全字符(移除 `/\[](){}<>:;,` 等) */
function sanitize(s: string): string {
  return (s || '').replace(/[\\/[\](){}<>:;,]/g, '_').trim();
}

/** 数据集名过长则保留首尾,中间用 … 省略 */
function truncateDataset(s: string): string {
  if (s.length <= MAX_DATASET_NAME) return s;
  const half = Math.floor((MAX_DATASET_NAME - 1) / 2);
  return `${s.slice(0, half)}…${s.slice(-half)}`;
}

export type TaskType =
  | '数据清洗'
  | '质量评估'
  | '数据蒸馏'
  | '数据合并'
  | '数据增强'
  | '训练集生成'
  | '内容审核';

/** 根据数据集名 + 任务类型生成统一格式任务名。
 *
 * 规则:
 * - 无数据集名 → 仅 `<任务类型>`
 * - 有数据集名 → `<数据集名>-<任务类型>`
 *
 * 算子不进任务名(列表过长且重复),具体算子链在任务详情中展示。
 */
export function suggestTaskName(
  datasetName: string | undefined,
  taskType: TaskType,
): string {
  const ds = truncateDataset(sanitize(datasetName || ''));
  return ds ? `${ds}-${taskType}` : taskType;
}
