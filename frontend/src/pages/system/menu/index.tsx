import {
  type ActionType,
  ModalForm,
  PageContainer,
  type ProColumns,
  ProForm,
  ProFormDigit,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProFormTreeSelect,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Button, Card, message, Popconfirm, Tag } from 'antd';
import { type FC, useRef, useState } from 'react';
import {
  createMenu,
  deleteMenu,
  listMenus,
  updateMenu,
} from '@/services/system';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

const TYPE_TAG: Record<string, { text: string; color: string }> = {
  M: { text: '目录', color: 'blue' },
  C: { text: '菜单', color: 'geekblue' },
  F: { text: '按钮', color: 'orange' },
};

// 编辑表单值(具名类型,避免在 TSX 泛型里写交叉类型导致解析歧义)
type MenuEditValues = System.MenuUpdate & { name: string; menuType: string };

/** 菜单树 → 上级选择(排除自身及子树防环)。 */
const toParentTreeData = (nodes: System.Menu[], excludeId?: string): any[] =>
  nodes
    .filter((n) => n.id !== excludeId)
    .map((n) => ({
      title: n.name,
      value: n.id,
      disabled: n.menuType === 'F', // 按钮不能作上级
      children: n.children?.length
        ? toParentTreeData(n.children, excludeId)
        : undefined,
    }));

/** 菜单管理:树形 CRUD(M目录/C菜单/F按钮)+ 权限标识(perms)维护。 */
const MenuPage: FC = () => {
  const access = useAccess();
  const canAdd = access.hasPerm('system:menu:add');
  const canEdit = access.hasPerm('system:menu:edit');
  const canRemove = access.hasPerm('system:menu:remove');
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<System.Menu | null>(null);
  const [tree, setTree] = useState<System.Menu[]>([]);

  const reload = () => actionRef.current?.reload();

  const columns: ProColumns<System.Menu>[] = [
    { title: '名称', dataIndex: 'name', width: 200 },
    {
      title: '类型',
      dataIndex: 'menuType',
      width: 80,
      render: (_, r) => {
        const t = TYPE_TAG[r.menuType] ?? {
          text: r.menuType,
          color: 'default',
        };
        return <Tag color={t.color}>{t.text}</Tag>;
      },
    },
    { title: '路径', dataIndex: 'path', width: 160 },
    { title: '组件', dataIndex: 'component', width: 160 },
    { title: '权限标识', dataIndex: 'perms', width: 180 },
    { title: '排序', dataIndex: 'sort', width: 70 },
    {
      title: '操作',
      valueType: 'option',
      width: 160,
      render: (_text, record) =>
        canAdd || canEdit || canRemove
          ? [
              canAdd && record.menuType !== 'F' && (
                <a
                  key="add-child"
                  onClick={() =>
                    setEditTarget({ ...record, _isNewChild: true } as any)
                  }
                >
                  新增子项
                </a>
              ),
              canEdit && (
                <a key="edit" onClick={() => setEditTarget(record)}>
                  编辑
                </a>
              ),
              canRemove && (
                <Popconfirm
                  key="delete"
                  title={`确认删除「${record.name}」?(有子项将拒绝)`}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await deleteMenu(record.id);
                      message.success('已删除');
                      reload();
                    } catch (err) {
                      message.error(pickErrMsg(err, '删除失败'));
                    }
                  }}
                >
                  <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>
                    删除
                  </a>
                </Popconfirm>
              ),
            ].filter(Boolean)
          : [<span key="readonly">-</span>],
    },
  ];

  // M/C 才有 path/component;F 只有 perms。用 Form 显示控制。
  const formType =
    editTarget && (editTarget as any)._isNewChild ? 'C' : editTarget?.menuType;

  return (
    <PageContainer
      title="菜单管理"
      content="菜单即路由 + 按钮权限:M 目录 / C 菜单(路由)/ F 按钮(权限标识)。"
    >
      <Card>
        <ProTable<System.Menu>
          actionRef={actionRef}
          rowKey="id"
          search={false}
          pagination={false}
          options={{ reload: true, density: false, setting: false }}
          toolBarRender={() =>
            canAdd
              ? [
                  <Button
                    key="create"
                    type="primary"
                    onClick={() => setCreateOpen(true)}
                  >
                    新建菜单
                  </Button>,
                ]
              : []
          }
          request={async () => {
            const res = await listMenus();
            setTree(res.data ?? []);
            return { data: res.data ?? [], success: res.success };
          }}
          columns={columns}
        />
      </Card>

      {/* 新建 */}
      <ModalForm<System.MenuCreate>
        title="新建菜单"
        width={560}
        open={createOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setCreateOpen}
        onFinish={async (values) => {
          try {
            await createMenu({
              name: values.name,
              parentId: values.parentId,
              menuType: values.menuType ?? 'C',
              path: values.path,
              component: values.component,
              perms: values.perms,
              icon: values.icon,
              sort: values.sort ?? 0,
              visible: values.visible ? '0' : '1',
            });
            message.success('已创建');
            setCreateOpen(false);
            reload();
            return true;
          } catch (err) {
            message.error(pickErrMsg(err, '创建失败'));
            return false;
          }
        }}
      >
        <ProForm.Group>
          <ProFormText name="name" label="名称" rules={[{ required: true }]} />
          <ProFormSelect
            name="menuType"
            label="类型"
            initialValue="C"
            options={[
              { label: '目录', value: 'M' },
              { label: '菜单', value: 'C' },
              { label: '按钮', value: 'F' },
            ]}
          />
        </ProForm.Group>
        <ProFormTreeSelect
          name="parentId"
          label="上级"
          allowClear
          fieldProps={{
            treeData: toParentTreeData(tree),
            treeDefaultExpandAll: true,
          }}
        />
        <ProForm.Group>
          <ProFormText name="path" label="路径(如 /system/user)" />
          <ProFormText name="component" label="组件(如 system/user)" />
        </ProForm.Group>
        <ProForm.Group>
          <ProFormText name="perms" label="权限标识(如 system:user:add)" />
          <ProFormDigit
            name="sort"
            label="排序"
            initialValue={0}
            fieldProps={{ step: 1 }}
          />
        </ProForm.Group>
      </ModalForm>

      {/* 编辑 / 新增子项 */}
      <ModalForm<MenuEditValues>
        title={
          editTarget && (editTarget as any)._isNewChild
            ? '新增子项'
            : '编辑菜单'
        }
        width={560}
        open={!!editTarget}
        modalProps={{ destroyOnHidden: true }}
        initialValues={
          editTarget
            ? (editTarget as any)._isNewChild
              ? { parentId: editTarget.id, menuType: 'C', sort: 0 }
              : {
                  name: editTarget.name,
                  parentId: editTarget.parentId,
                  menuType: editTarget.menuType,
                  path: editTarget.path,
                  component: editTarget.component,
                  perms: editTarget.perms,
                  sort: editTarget.sort,
                  visible: editTarget.visible === '0',
                }
            : {}
        }
        onOpenChange={(v) => !v && setEditTarget(null)}
        onFinish={async (values) => {
          if (!editTarget) return false;
          try {
            if ((editTarget as any)._isNewChild) {
              await createMenu({
                name: values.name as string,
                parentId: editTarget.id,
                menuType: values.menuType ?? 'C',
                path: values.path,
                component: values.component,
                perms: values.perms,
                sort: values.sort ?? 0,
              });
            } else {
              await updateMenu(editTarget.id, {
                name: values.name,
                parentId: values.parentId,
                menuType: values.menuType,
                path: values.path,
                component: values.component,
                perms: values.perms,
                sort: values.sort,
                visible: values.visible ? '0' : '1',
              });
            }
            message.success('已保存');
            setEditTarget(null);
            reload();
            return true;
          } catch (err) {
            message.error(pickErrMsg(err, '保存失败'));
            return false;
          }
        }}
      >
        <ProForm.Group>
          <ProFormText name="name" label="名称" rules={[{ required: true }]} />
          <ProFormSelect
            name="menuType"
            label="类型"
            disabled={!(editTarget as any)?._isNewChild}
            options={[
              { label: '目录', value: 'M' },
              { label: '菜单', value: 'C' },
              { label: '按钮', value: 'F' },
            ]}
          />
        </ProForm.Group>
        <ProFormTreeSelect
          name="parentId"
          label="上级"
          allowClear
          disabled={(editTarget as any)?._isNewChild}
          fieldProps={{
            treeData: toParentTreeData(tree, editTarget?.id),
            treeDefaultExpandAll: true,
          }}
        />
        <ProForm.Group>
          <ProFormText name="path" label="路径" disabled={formType === 'F'} />
          <ProFormText
            name="component"
            label="组件"
            disabled={formType === 'F'}
          />
        </ProForm.Group>
        <ProForm.Group>
          <ProFormText name="perms" label="权限标识" />
          <ProFormDigit name="sort" label="排序" fieldProps={{ step: 1 }} />
        </ProForm.Group>
        <ProFormSwitch name="visible" label="显示" />
      </ModalForm>
    </PageContainer>
  );
};

export default MenuPage;
