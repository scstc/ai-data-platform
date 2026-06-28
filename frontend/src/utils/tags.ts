/**
 * 数据集标签颜色:deterministic 哈希(标签名 → antd 预设色)。
 *
 * 零管理(标签库不存 color)、每标签稳定一色(同名同色)。
 * 复用模式对齐 utils/sourceKind.ts。
 */

/** antd v6 Tag 支持的预设色板。 */
export const TAG_PALETTE = [
  'magenta',
  'red',
  'volcano',
  'orange',
  'gold',
  'lime',
  'green',
  'cyan',
  'blue',
  'geekblue',
  'purple',
] as const;

/** 标签名 → 稳定颜色(djb2 变体哈希 → 色板下标)。空名回退蓝色。 */
export function tagColor(name: string | null | undefined): string {
  if (!name) return 'blue';
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return TAG_PALETTE[h % TAG_PALETTE.length];
}
