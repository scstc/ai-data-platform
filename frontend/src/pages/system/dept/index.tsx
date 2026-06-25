import {
  type ActionType,
  ModalForm,
  PageContainer,
  type ProColumns,
  ProForm,
  ProFormDigit,
  ProFormText,
  ProFormTreeSelect,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Button, Card, message, Popconfirm, Tag } from 'antd';
import { type FC, useRef, useState } from 'react';
import {
  createDept,
  deleteDept,
  listDepts,
  updateDept,
} from '@/services/system';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

/** 把部门树展平成「可作父」选项(排除自身及其子树,防环),用于编辑时的上级选择。 */
const toParentTreeData = (
  nodes: System.Department[],
  excludeId?: string,
): { title: string; value: string; children?: any[] }[] =>
  nodes
    .filter((n) => n.id !== excludeId)
    .map((n) => ({
      title: n.name,
      value: n.id,
      children: n.children?.length
        ? toParentTreeData(n.children, excludeId)
        : undefined,
    }));

// 编辑表单值(具名类型,避免 TSX 泛型里写交叉类型导致解析歧义)
type DeptEditValues = System.DeptUpdate & { name: string };

/** 部门管理:树形 CRUD(上级、排序、负责人、状态)。 */
const DeptPage: FC = () => {
  const access = useAccess();
  const canAdd = access.hasPerm('system:dept:add');
  const canEdit = access.hasPerm('system:dept:edit');
  const canRemove = access.hasPerm('system:dept:remove');
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<System.Department | null>(null);
  const [tree, setTree] = useState<System.Department[]>([]);

  const reload = () => actionRef.current?.reload();

  const columns: ProColumns<System.Department>[] = [
    { title: '部门', dataIndex: 'name', width: 200 },
    { title: '负责人', dataIndex: 'leader', width: 120 },
    { title: '排序', dataIndex: 'sort', width: 80 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (_, r) =>
        r.status === '0' ? <Tag color="green">正常</Tag> : <Tag>停用</Tag>,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 200,
      render: (_text, record) =>
        canAdd || canEdit || canRemove
          ? [
              canAdd && (
                <a
                  key="add-child"
                  onClick={() => {
                    setEditTarget({ ...record, _isNewChild: true } as any);
                  }}
                >
                  新增子部门
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
                  title={`确认删除部门「${record.name}」?(有子部门/用户将拒绝)`}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await deleteDept(record.id);
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
      title="部门管理"
      content="组织部门树(数据权限维度):增删改、拖动上下级由编辑上级实现。"
    >
      <Card>
        <ProTable<System.Department>
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
                    新建部门
                  </Button>,
                ]
              : []
          }
          request={async () => {
            const res = await listDepts();
            setTree(res.data ?? []);
            return { data: res.data ?? [], success: res.success };
          }}
          columns={columns}
        />
      </Card>

      {/* 新建(顶级) */}
      <ModalForm<System.DeptCreate>
        title="新建部门"
        width={520}
        open={createOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setCreateOpen}
        onFinish={async (values) => {
          try {
            await createDept({
              name: values.name,
              parentId: values.parentId,
              sort: values.sort ?? 0,
              leader: values.leader,
              status: values.status ?? '0',
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
        <ProFormText name="name" label="名称" rules={[{ required: true }]} />
        <ProFormTreeSelect
          name="parentId"
          label="上级部门"
          allowClear
          fieldProps={{
            treeData: toParentTreeData(tree),
            treeDefaultExpandAll: true,
          }}
        />
        <ProForm.Group>
          <ProFormDigit
            name="sort"
            label="排序"
            initialValue={0}
            fieldProps={{ step: 1 }}
          />
          <ProFormText name="leader" label="负责人" />
        </ProForm.Group>
      </ModalForm>

      {/* 编辑(也用于「新增子部门」:_isNewChild=true 时 parent 固定为当前节点) */}
      <ModalForm<DeptEditValues>
        title={
          editTarget && (editTarget as any)._isNewChild
            ? '新增子部门'
            : '编辑部门'
        }
        width={520}
        open={!!editTarget}
        modalProps={{ destroyOnHidden: true }}
        initialValues={
          editTarget
            ? (editTarget as any)._isNewChild
              ? { parentId: editTarget.id, sort: 0 }
              : {
                  name: editTarget.name,
                  parentId: editTarget.parentId,
                  sort: editTarget.sort,
                  leader: editTarget.leader,
                  status: editTarget.status,
                }
            : {}
        }
        onOpenChange={(v) => !v && setEditTarget(null)}
        onFinish={async (values) => {
          if (!editTarget) return false;
          try {
            if ((editTarget as any)._isNewChild) {
              await createDept({
                name: values.name as string,
                parentId: editTarget.id,
                sort: values.sort ?? 0,
                leader: values.leader,
              });
            } else {
              await updateDept(editTarget.id, {
                name: values.name,
                parentId: values.parentId,
                sort: values.sort,
                leader: values.leader,
                status: values.status,
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
        {(editTarget as any)?._isNewChild && (
          <ProFormText name="name" label="名称" rules={[{ required: true }]} />
        )}
        <ProFormTreeSelect
          name="parentId"
          label="上级部门"
          allowClear
          disabled={(editTarget as any)?._isNewChild}
          fieldProps={{
            treeData: toParentTreeData(tree, editTarget?.id),
            treeDefaultExpandAll: true,
          }}
        />
        <ProForm.Group>
          <ProFormDigit name="sort" label="排序" fieldProps={{ step: 1 }} />
          <ProFormText name="leader" label="负责人" />
        </ProForm.Group>
      </ModalForm>
    </PageContainer>
  );
};

export default DeptPage;
