/** 数据集分级(#15):敏感级别受控枚举 + 中文映射。
 *
 * 后端 sensitivity_level 为 free string,前端收口为三档受控值 + 中文展示。
 * 复用模式对齐 utils/sourceKind.ts、utils/semanticType.ts。
 */

/** 分级受控枚举(ProFormSelect/ProTable valueEnum 用):英文 key → 中文 text。 */
export const SENSITIVITY_LEVEL_ENUM = {
  public: { text: '公开' },
  internal: { text: '内部' },
  confidential: { text: '机密' },
} as const;

const LEVEL_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(SENSITIVITY_LEVEL_ENUM).map(([k, v]) => [k, v.text]),
);

/** 分级英文值 → 中文标签;未知值原样返回,null/undefined → undefined。 */
export function sensitivityLevelLabel(
  level?: string | null,
): string | undefined {
  if (!level) return undefined;
  return LEVEL_LABEL[level] ?? level;
}

/** 分级 → Tag 颜色(public=绿/internal=蓝/confidential=红);未知值用 default。 */
export const SENSITIVITY_LEVEL_COLOR: Record<string, string> = {
  public: 'green',
  internal: 'blue',
  confidential: 'red',
};

/** 分级 → {color, label} 供 Tag 用;null/undefined → undefined。 */
export function sensitivityLevelTag(
  level?: string | null,
): { color: string; label: string } | undefined {
  if (!level) return undefined;
  return {
    color: SENSITIVITY_LEVEL_COLOR[level] ?? 'default',
    label: sensitivityLevelLabel(level) ?? level,
  };
}
