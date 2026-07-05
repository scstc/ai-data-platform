import { Checkbox, Space, Table, type TableColumnsType, theme } from 'antd';
import { type FC, useMemo } from 'react';

/** 一行 = 一条「一级菜单 × 二级菜单」路径,权限列内联该二级菜单下的按钮(F)。 */
type MatrixRow = {
  key: string;
  l1: System.Menu;
  l2: System.Menu | null;
  perms: System.Menu[];
  l1Span: number; // 一级菜单单元格 rowSpan(0 = 被上一行合并)
};

type Props = {
  menus: System.Menu[];
  /** 已授权菜单 id 集合(含 M/C/F 各级,路由与按钮权限都靠它) */
  value: string[];
  onChange: (ids: string[]) => void;
};

const isPerm = (m: System.Menu) => m.menuType === 'F';

/** 子树全部 id(含自身)。 */
const subtreeIds = (node: System.Menu): string[] => {
  const out = [node.id];
  for (const c of node.children ?? []) out.push(...subtreeIds(c));
  return out;
};

/** 扁平化整棵树,建 id→父 id 映射,用于回溯祖先。 */
const buildParentMap = (
  menus: System.Menu[],
): Record<string, string | undefined> => {
  const parent: Record<string, string | undefined> = {};
  const walk = (nodes: System.Menu[], pid?: string) => {
    for (const n of nodes) {
      parent[n.id] = pid;
      if (n.children?.length) walk(n.children, n.id);
    }
  };
  walk(menus);
  return parent;
};

/** 权限按钮短标签:F 的 name 形如「用户管理查询」,去掉所属二级菜单前缀留「查询」。 */
const permLabel = (perm: System.Menu, owner: System.Menu | null): string => {
  const prefix = owner?.name;
  if (prefix && perm.name.startsWith(prefix))
    return perm.name.slice(prefix.length) || perm.name;
  return perm.name;
};

/**
 * 分配菜单矩阵:参照 ACE 权限表布局(一级菜单 / 二级菜单 / 权限)。
 *
 * 勾选语义(适配后端 build_router_tree + get_user_perms):
 * - 勾选某节点 ⇒ 勾选其整棵子树 + 自动补齐所有祖先(祖先入库路由才可见)。
 * - 取消某节点 ⇒ 仅清掉其子树;祖先保留(菜单可授权但不含任何按钮,如「公共镜像」)。
 */
const MenuPermMatrix: FC<Props> = ({ menus, value, onChange }) => {
  const { token } = theme.useToken();
  const parentMap = useMemo(() => buildParentMap(menus), [menus]);
  const sel = useMemo(() => new Set(value), [value]);

  const allIds = useMemo(() => menus.flatMap(subtreeIds), [menus]);

  const rows = useMemo<MatrixRow[]>(() => {
    const out: MatrixRow[] = [];
    for (const top of menus) {
      const kids = top.children ?? [];
      const menuKids = kids.filter((k) => !isPerm(k));
      const directPerms = kids.filter(isPerm);
      const group: { l2: System.Menu | null; perms: System.Menu[] }[] = [];
      if (directPerms.length) group.push({ l2: null, perms: directPerms });
      for (const c of menuKids) {
        group.push({ l2: c, perms: (c.children ?? []).filter(isPerm) });
      }
      if (group.length === 0) group.push({ l2: null, perms: [] });
      group.forEach((g, i) => {
        out.push({
          key: `${top.id}/${g.l2?.id ?? '_'}`,
          l1: top,
          l2: g.l2,
          perms: g.perms,
          l1Span: i === 0 ? group.length : 0,
        });
      });
    }
    return out;
  }, [menus]);

  const toggle = (node: System.Menu, checked: boolean) => {
    const next = new Set(sel);
    if (checked) {
      for (const id of subtreeIds(node)) next.add(id);
      let pid = parentMap[node.id];
      while (pid) {
        next.add(pid);
        pid = parentMap[pid];
      }
    } else {
      for (const id of subtreeIds(node)) next.delete(id);
    }
    onChange([...next]);
  };

  /** 单个菜单/按钮复选框:勾选时文字高亮为主色,呼应参考图。 */
  const cell = (node: System.Menu, label?: string) => {
    const checked = sel.has(node.id);
    return (
      <Checkbox
        checked={checked}
        onChange={(e) => toggle(node, e.target.checked)}
      >
        <span style={{ color: checked ? token.colorPrimary : token.colorText }}>
          {label ?? node.name}
        </span>
      </Checkbox>
    );
  };

  const dash = <span style={{ color: token.colorTextQuaternary }}>—</span>;

  const columns: TableColumnsType<MatrixRow> = [
    {
      title: '一级菜单',
      dataIndex: 'l1',
      width: 180,
      onCell: (row) => ({ rowSpan: row.l1Span }),
      render: (_, row) => cell(row.l1),
    },
    {
      title: '二级菜单',
      dataIndex: 'l2',
      width: 180,
      render: (_, row) => (row.l2 ? cell(row.l2) : dash),
    },
    {
      title: '权限',
      dataIndex: 'perms',
      render: (_, row) =>
        row.perms.length ? (
          <Space size={[12, 8]} wrap>
            {row.perms.map((p) => (
              <span key={p.id}>{cell(p, permLabel(p, row.l2 ?? row.l1))}</span>
            ))}
          </Space>
        ) : (
          dash
        ),
    },
  ];

  const allChecked = allIds.length > 0 && allIds.every((id) => sel.has(id));
  const anyChecked = allIds.some((id) => sel.has(id));

  return (
    <Table<MatrixRow>
      bordered
      size="small"
      rowKey="key"
      pagination={false}
      columns={columns}
      dataSource={rows}
      title={() => (
        <Checkbox
          checked={allChecked}
          indeterminate={anyChecked && !allChecked}
          onChange={(e) => onChange(e.target.checked ? [...allIds] : [])}
        >
          全选
        </Checkbox>
      )}
    />
  );
};

export default MenuPermMatrix;
