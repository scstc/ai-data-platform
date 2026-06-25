import {
  type ActionType,
  ModalForm,
  PageContainer,
  type ProColumns,
  ProForm,
  ProFormDigit,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Button, Card, Drawer, message, Popconfirm, Tag, Tree } from 'antd';
import dayjs from 'dayjs';
import { type FC, useRef, useState } from 'react';
import {
  createRole,
  deleteRole,
  getRole,
  listMenus,
  listRoles,
  updateRole,
} from '@/services/system';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

const DATA_SCOPES = [
  { label: '全部数据', value: 'all' },
  { label: '本部门及子部门', value: 'dept_and_child' },
  { label: '仅本部门', value: 'dept' },
  { label: '自定义部门', value: 'custom' },
  { label: '仅本人', value: 'self' },
];

/** 菜单树 → antd Tree treeData(仅展示 M/C,叶子含 F 按钮以做按钮级勾选)。 */
const toMenuTreeData = (nodes: System.Menu[]): any[] =>
  nodes.map((n) => ({
    key: n.id,
    title: `${n.name}${n.perms ? ` (${n.perms})` : ''}`,
    children: n.children?.length ? toMenuTreeData(n.children) : undefined,
  }));

/** 角色:CRUD + 菜单授权(勾选菜单/按钮)。授权 = system:role:edit。 */
const RolePage: FC = () => {
  const access = useAccess();
  const canAdd = access.hasPerm('system:role:add');
  const canEdit = access.hasPerm('system:role:edit');
  const canRemove = access.hasPerm('system:role:remove');
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<System.Role | null>(null);

  // 授权抽屉
  const [authTarget, setAuthTarget] = useState<System.Role | null>(null);
  const [menuTree, setMenuTree] = useState<System.Menu[]>([]);
  const [checkedKeys, setCheckedKeys] = useState<string[]>([]);
  const [authLoading, setAuthLoading] = useState(false);

  const reload = () => actionRef.current?.reload();

  const openAuth = async (role: System.Role) => {
    setAuthTarget(role);
    setCheckedKeys([]);
    try {
      if (menuTree.length === 0) {
        const m = await listMenus();
        setMenuTree(m.data ?? []);
      }
      const detail = await getRole(role.id);
      setCheckedKeys(detail.data.menuIds ?? []);
    } catch (err) {
      message.error(pickErrMsg(err, '加载授权失败'));
    }
  };

  const submitAuth = async () => {
    if (!authTarget) return;
    setAuthLoading(true);
    try {
      await updateRole(authTarget.id, { menuIds: checkedKeys });
      message.success('授权已保存');
      setAuthTarget(null);
    } catch (err) {
      message.error(pickErrMsg(err, '保存失败'));
    } finally {
      setAuthLoading(false);
    }
  };

  const columns: ProColumns<System.Role>[] = [
    { title: '角色', dataIndex: 'name', width: 140 },
    { title: '标识', dataIndex: 'roleKey', width: 140 },
    {
      title: '数据范围',
      dataIndex: 'dataScope',
      width: 140,
      render: (_, r) =>
        DATA_SCOPES.find((s) => s.value === r.dataScope)?.label ?? r.dataScope,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (_, r) =>
        r.status === '0' ? <Tag color="green">正常</Tag> : <Tag>停用</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 168,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 220,
      render: (_text, record) =>
        canEdit || canRemove
          ? [
              canEdit && (
                <a key="edit" onClick={() => setEditTarget(record)}>
                  编辑
                </a>
              ),
              canEdit && (
                <a key="auth" onClick={() => openAuth(record)}>
                  分配菜单
                </a>
              ),
              canRemove && (
                <Popconfirm
                  key="delete"
                  title={`确认删除角色「${record.name}」?(被用户引用/超管将拒绝)`}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await deleteRole(record.id);
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

  return (
    <PageContainer
      title="角色管理"
      content="角色 CRUD + 菜单/按钮授权 + 数据范围。"
    >
      <Card>
        <ProTable<System.Role>
          actionRef={actionRef}
          rowKey="id"
          search={false}
          options={{ reload: true, density: false, setting: false }}
          toolBarRender={() =>
            canAdd
              ? [
                  <Button
                    key="create"
                    type="primary"
                    onClick={() => setCreateOpen(true)}
                  >
                    新建角色
                  </Button>,
                ]
              : []
          }
          request={async () => {
            const res = await listRoles({ current: 1, pageSize: 200 });
            return { data: res.data, total: res.total, success: res.success };
          }}
          columns={columns}
        />
      </Card>

      {/* 新建 */}
      <ModalForm<System.RoleCreate>
        title="新建角色"
        width={520}
        open={createOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setCreateOpen}
        onFinish={async (values) => {
          try {
            await createRole({
              name: values.name,
              roleKey: values.roleKey,
              sort: values.sort ?? 0,
              dataScope: values.dataScope ?? 'self',
              status: values.status ?? '0',
              remark: values.remark,
              menuIds: [],
              deptIds: [],
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
          <ProFormText
            name="name"
            label="角色名"
            rules={[{ required: true }]}
          />
          <ProFormText
            name="roleKey"
            label="标识"
            rules={[{ required: true }]}
          />
        </ProForm.Group>
        <ProForm.Group>
          <ProFormSelect
            name="dataScope"
            label="数据范围"
            options={DATA_SCOPES}
            initialValue="self"
          />
          <ProFormDigit
            name="sort"
            label="排序"
            initialValue={0}
            fieldProps={{ step: 1 }}
          />
        </ProForm.Group>
        <ProFormTextArea name="remark" label="备注" />
      </ModalForm>

      {/* 编辑(基本信息) */}
      <ModalForm<System.RoleUpdate>
        title="编辑角色"
        width={520}
        open={!!editTarget}
        modalProps={{ destroyOnHidden: true }}
        initialValues={editTarget ?? {}}
        onOpenChange={(v) => !v && setEditTarget(null)}
        onFinish={async (values) => {
          if (!editTarget) return false;
          try {
            await updateRole(editTarget.id, {
              name: values.name,
              sort: values.sort,
              dataScope: values.dataScope,
              status: values.status,
              remark: values.remark,
            });
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
          <ProFormText
            name="name"
            label="角色名"
            rules={[{ required: true }]}
          />
          <ProFormSelect
            name="dataScope"
            label="数据范围"
            options={DATA_SCOPES}
          />
        </ProForm.Group>
        <ProForm.Group>
          <ProFormDigit name="sort" label="排序" fieldProps={{ step: 1 }} />
          <ProFormSelect
            name="status"
            label="状态"
            options={[
              { label: '正常', value: '0' },
              { label: '停用', value: '1' },
            ]}
          />
        </ProForm.Group>
        <ProFormTextArea name="remark" label="备注" />
      </ModalForm>

      {/* 分配菜单(授权) */}
      <Drawer
        title={`分配菜单:${authTarget?.name ?? ''}`}
        open={!!authTarget}
        onClose={() => setAuthTarget(null)}
        width={480}
        extra={
          <Button type="primary" loading={authLoading} onClick={submitAuth}>
            保存
          </Button>
        }
      >
        <Tree
          checkable
          defaultExpandAll
          treeData={toMenuTreeData(menuTree)}
          checkedKeys={checkedKeys}
          onCheck={(keys) => setCheckedKeys((keys as string[]) ?? [])}
        />
      </Drawer>
    </PageContainer>
  );
};

export default RolePage;
