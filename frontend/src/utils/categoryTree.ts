/**
 * 分类树适配:把后端嵌套分类(GET /categories 返回 Category.children 嵌套树)
 * 转成 antd TreeSelect / ProFormTreeSelect 的 treeData 格式 {title, value, children}。
 *
 * 用于数据集/数据源/采集任务的分类选择(取代扁平 Select)与分类管理页的"上级分类"选择(#15)。
 */

/** TreeSelect / ProFormTreeSelect 的 treeData 节点形状 */
export interface CategoryTreeNode {
  title: string;
  value: string;
  children?: CategoryTreeNode[];
}

/**
 * 嵌套分类树 → treeData。
 * @param nodes 后端 listCategories 返回的嵌套树(根数组)
 * @param excludeSubtreeOf 编辑某分类选"上级"时传该分类 id,排除自身及子树(防环)
 */
export function toCategoryTreeData(
  nodes: DataPlatform.Category[] | undefined | null,
  excludeSubtreeOf?: string,
): CategoryTreeNode[] {
  if (!nodes?.length) return [];

  const walk = (list: DataPlatform.Category[]): CategoryTreeNode[] =>
    list
      .filter((n) => n.id !== excludeSubtreeOf)
      .map((n) => ({
        title: n.name,
        value: n.id,
        children: n.children?.length ? walk(n.children) : undefined,
      }));

  return walk(nodes);
}

/**
 * 在 treeData(嵌套分类树)中按 value 查找分类路径(如 "父/子"),
 * 用于 AI 命名等需要人类可读分类上下文的场景(传 id 给 LLM 无意义)。
 */
export function findCategoryPath(
  nodes: CategoryTreeNode[] | undefined | null,
  id: string | undefined,
): string | undefined {
  if (!id || !nodes?.length) return undefined;
  const walk = (
    list: CategoryTreeNode[],
    prefix: string[],
  ): string | undefined => {
    for (const n of list) {
      const path = [...prefix, n.title];
      if (n.value === id) return path.join('/');
      if (n.children?.length) {
        const found = walk(n.children, path);
        if (found) return found;
      }
    }
    return undefined;
  };
  return walk(nodes, []);
}

/**
 * 收集 id 及其所有后代 id(含自身),用于"选父含子"筛选(选父分类连带查所有子孙)。
 * 找不到 id(数据陈旧)时退化为 [id](精确匹配),不阻断筛选。
 */
export function collectSubtreeIds(
  nodes: CategoryTreeNode[] | undefined | null,
  id: string | undefined,
): string[] {
  if (!id || !nodes?.length) return [];
  const find = (list: CategoryTreeNode[]): CategoryTreeNode | undefined => {
    for (const n of list) {
      if (n.value === id) return n;
      if (n.children?.length) {
        const f = find(n.children);
        if (f) return f;
      }
    }
    return undefined;
  };
  const collect = (n: CategoryTreeNode): string[] => {
    const out = [n.value];
    if (n.children?.length) {
      for (const c of n.children) out.push(...collect(c));
    }
    return out;
  };
  const node = find(nodes);
  return node ? collect(node) : [id];
}
