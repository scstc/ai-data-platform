// 任务名自动生成工具:统一的 <数据集名>-<任务类型>(<算子列表>) 格式。
// 用于 governance 下 5 个任务编辑器(processing/quality/distillation/make/augment)。
// 用户在编辑器中输入的"任务名"字段会被这个函数实时预填——dataset/operator 变化时
// 始终重算(用户可编辑,但下次 input 变化会覆盖)。

const MAX_TOTAL = 80; // 任务名总长上限(列表列宽容得下)
const MAX_OPS_VISIBLE = 3; // 超过此数用「;共N算子」截断
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

/** 算子中文标签优先,缺则用类名 */
function labelOf(name: string, labelMap?: Record<string, string>): string {
  if (labelMap && labelMap[name]) return labelMap[name];
  return name;
}

export type TaskType =
  | '数据加工'
  | '质量评估'
  | '数据蒸馏'
  | '数据合成'
  | '数据增强';

/** 根据数据集名 + 任务类型 + 算子链,生成统一格式任务名。
 *
 * 规则:
 * - 无数据集名 → 仅 `<任务类型>`
 * - 无算子 → `<数据集名>-<任务类型>`
 * - 1~MAX_OPS_VISIBLE 个算子 → 完整列出
 * - 超过 MAX_OPS_VISIBLE → 列前 MAX_OPS_VISIBLE + "共N算子"
 * - 总长超 MAX_TOTAL → 在算子段截断 + 加 …(数据集段优先保留)
 */
export function suggestTaskName(
  datasetName: string | undefined,
  taskType: TaskType,
  operatorNames: string[],
  labelMap?: Record<string, string>,
): string {
  const ds = truncateDataset(sanitize(datasetName || ''));
  const base = ds ? `${ds}-${taskType}` : taskType;
  if (operatorNames.length === 0) return base;

  // 算子段
  const visible = operatorNames
    .slice(0, MAX_OPS_VISIBLE)
    .map((n) => labelOf(n, labelMap));
  const opsStr =
    operatorNames.length > MAX_OPS_VISIBLE
      ? `${visible.join('、')};共${operatorNames.length}算子`
      : visible.join('、');
  const full = `${base}(${opsStr})`;

  if (full.length <= MAX_TOTAL) return full;
  // 算子段过长,保留数据集段 + 截断算子段
  const reserved = `${base}(`; // 必然留下
  const rest = MAX_TOTAL - reserved.length - 1; // 留 1 字符给 … 和 )
  if (rest <= 4) {
    // 极端情况,数据集名本身就接近上限 → 硬截数据集段
    return `${base.slice(0, MAX_TOTAL - 3)}…`;
  }
  return `${reserved}${opsStr.slice(0, rest)}…)`;
}
