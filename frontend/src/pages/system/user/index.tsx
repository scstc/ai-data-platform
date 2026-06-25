import {
  type ActionType,
  ModalForm,
  PageContainer,
  type ProColumns,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProFormTreeSelect,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Button, Card, message, Popconfirm, Tag } from 'antd';
import dayjs from 'dayjs';
import { type FC, useRef, useState } from 'react';
import {
  createUser,
  deleteUser,
  listDepts,
  listRoles,
  listUsers,
  resetUserPassword,
  updateUser,
} from '@/services/system';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

const STATUS_TAG: Record<string, { text: string; color: string }> = {
  ok: { text: '正常', color: 'green' },
  disabled: { text: '已停用', color: 'default' },
};

/** 把部门树转成 ProFormTreeSelect 的 treeData。 */
const toDeptTreeData = (
  nodes: System.Department[],
): { title: string; value: string; children?: any[] }[] =>
  nodes.map((n) => ({
    title: n.name,
    value: n.id,
    children: n.children?.length ? toDeptTreeData(n.children) : undefined,
  }));

/** 用户管理:CRUD + 分配角色 + 分配部门 + 重置密码 + 启停。
 *  按钮级权限:system:user:{add,edit,remove} / resetPwd 走 system:user:edit。 */
const UserPage: FC = () => {
  const access = useAccess();
  const canEdit = access.hasPerm('system:user:edit');
  const canAdd = access.hasPerm('system:user:add');
  const canRemove = access.hasPerm('system:user:remove');
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<System.User | null>(null);
  const [pwdTarget, setPwdTarget] = useState<System.User | null>(null);
  const [deptTree, setDeptTree] = useState<System.Department[]>([]);
  const [roleOptions, setRoleOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const reload = () => actionRef.current?.reload();

  // 进入/打开表单时加载部门树与角色选项(轻量,本地缓存)
  const ensureMeta = async () => {
    if (deptTree.length === 0) {
      try {
        const d = await listDepts();
        setDeptTree(d.data ?? []);
      } catch {
        /* ignore */
      }
    }
    if (roleOptions.length === 0) {
      try {
        const r = await listRoles({ current: 1, pageSize: 200 });
        setRoleOptions(
          (r.data ?? []).map((x) => ({ label: x.name, value: x.id })),
        );
      } catch {
        /* ignore */
      }
    }
  };

  const columns: ProColumns<System.User>[] = [
    { title: '用户名', dataIndex: 'username', width: 140 },
    { title: '昵称', dataIndex: 'displayName', width: 140 },
    {
      title: '角色',
      dataIndex: 'roles',
      width: 160,
      render: (_, r) => r.roles?.map((k) => <Tag key={k}>{k}</Tag>) ?? '-',
    },
    {
      title: '部门',
      dataIndex: 'deptId',
      width: 120,
      render: (_, r) => r.deptId ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'disabled',
      width: 90,
      render: (_, r) => {
        const s = r.disabled ? STATUS_TAG.disabled : STATUS_TAG.ok;
        return <Tag color={s.color}>{s.text}</Tag>;
      },
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
      width: 200,
      render: (_text, record) =>
        canEdit || canRemove
          ? [
              canEdit && (
                <a
                  key="edit"
                  onClick={async () => {
                    await ensureMeta();
                    setEditTarget(record);
                  }}
                >
                  编辑
                </a>
              ),
              canEdit && (
                <a key="pwd" onClick={() => setPwdTarget(record)}>
                  重置密码
                </a>
              ),
              canRemove && (
                <Popconfirm
                  key="delete"
                  title={`确认删除用户「${record.username}」?`}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await deleteUser(record.id);
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
      title="用户管理"
      content="平台用户的增删改、分配角色与部门、重置密码。"
    >
      <Card>
        <ProTable<System.User>
          actionRef={actionRef}
          rowKey="id"
          search={{ labelWidth: 'auto' }}
          options={{ reload: true, density: false, setting: false }}
          toolBarRender={() =>
            canAdd
              ? [
                  <Button
                    key="create"
                    type="primary"
                    onClick={async () => {
                      await ensureMeta();
                      setCreateOpen(true);
                    }}
                  >
                    新建用户
                  </Button>,
                ]
              : []
          }
          request={async (params) => {
            const res = await listUsers({
              current: params.current,
              pageSize: params.pageSize,
              keyword: params.username,
            });
            return { data: res.data, total: res.total, success: res.success };
          }}
          columns={columns}
        />
      </Card>

      {/* 新建 */}
      <ModalForm<System.UserCreate>
        title="新建用户"
        width={520}
        open={createOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setCreateOpen}
        onFinish={async (values) => {
          try {
            await createUser({
              username: values.username,
              password: values.password,
              displayName: values.displayName,
              deptId: values.deptId,
              roleIds: values.roleIds ?? [],
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
        <ProFormText
          name="username"
          label="用户名"
          rules={[{ required: true, message: '请输入用户名' }]}
        />
        <ProFormText.Password
          name="password"
          label="初始密码"
          rules={[
            { required: true, message: '请输入初始密码' },
            { min: 6, message: '至少 6 位' },
          ]}
        />
        <ProFormText name="displayName" label="昵称" />
        <ProFormTreeSelect
          name="deptId"
          label="部门"
          allowClear
          fieldProps={{
            treeData: toDeptTreeData(deptTree),
            treeDefaultExpandAll: true,
          }}
        />
        <ProFormSelect
          name="roleIds"
          label="角色"
          mode="multiple"
          options={roleOptions}
        />
      </ModalForm>

      {/* 编辑 */}
      <ModalForm<System.UserUpdate>
        title="编辑用户"
        width={520}
        open={!!editTarget}
        modalProps={{ destroyOnHidden: true }}
        initialValues={
          editTarget
            ? {
                displayName: editTarget.displayName,
                deptId: editTarget.deptId,
                roleIds: [],
                disabled: editTarget.disabled,
              }
            : {}
        }
        onOpenChange={(v) => !v && setEditTarget(null)}
        onFinish={async (values) => {
          if (!editTarget) return false;
          try {
            await updateUser(editTarget.id, {
              displayName: values.displayName,
              deptId: values.deptId,
              roleIds: values.roleIds,
              disabled: values.disabled,
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
        <ProFormText name="displayName" label="昵称" />
        <ProFormTreeSelect
          name="deptId"
          label="部门"
          allowClear
          fieldProps={{
            treeData: toDeptTreeData(deptTree),
            treeDefaultExpandAll: true,
          }}
        />
        <ProFormSelect
          name="roleIds"
          label="角色"
          mode="multiple"
          options={roleOptions}
        />
        <ProFormSwitch name="disabled" label="停用" />
      </ModalForm>

      {/* 重置密码 */}
      <ModalForm<{ password: string }>
        title={`重置密码:${pwdTarget?.username ?? ''}`}
        width={420}
        open={!!pwdTarget}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={(v) => !v && setPwdTarget(null)}
        onFinish={async (values) => {
          if (!pwdTarget) return false;
          try {
            await resetUserPassword(pwdTarget.id, values.password);
            message.success('已重置');
            setPwdTarget(null);
            return true;
          } catch (err) {
            message.error(pickErrMsg(err, '重置失败'));
            return false;
          }
        }}
      >
        <ProFormText.Password
          name="password"
          label="新密码"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 6, message: '至少 6 位' },
          ]}
        />
      </ModalForm>
    </PageContainer>
  );
};

export default UserPage;
