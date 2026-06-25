import {
  type ActionType,
  ModalForm,
  PageContainer,
  type ProColumns,
  ProForm,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Button, Card, message, Popconfirm, Tag } from 'antd';
import dayjs from 'dayjs';
import { type FC, type ReactNode, useRef, useState } from 'react';
import {
  batchDeleteTags,
  createTag,
  deleteTag,
  listTags,
  mergeTags,
  updateTag,
} from '@/services/data-platform';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import { tagColor } from '@/utils/tags';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

/** 标签管理:全局标签池 CRUD + 批量删 + 合并(扁平,仅挂数据集)。
 *  列表所有登录用户可见;写操作仅 admin(canAdmin)。颜色按 name 哈希(utils/tags)。 */
const TagsPage: FC = () => {
  const access = useAccess();
  const canAdmin = !!access.canAdmin;
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [allTags, setAllTags] = useState<DataPlatform.Tag[]>([]);

  const reload = () => actionRef.current?.reload();

  const columns: ProColumns<DataPlatform.Tag>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_, r) => <Tag color={tagColor(r.name)}>{r.name}</Tag>,
    },
    {
      title: '使用数据集数',
      dataIndex: 'usageCount',
      width: 120,
      render: (_, r) => `${r.usageCount} 个`,
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
      width: 160,
      render: (_text, record) =>
        canAdmin
          ? [
              <ModalForm<DataPlatform.TagUpdate>
                key="rename"
                title="重命名标签"
                trigger={<a>重命名</a>}
                width={380}
                modalProps={{ destroyOnHidden: true }}
                initialValues={{ name: record.name }}
                onFinish={async (values) => {
                  try {
                    await updateTag(record.id, { name: values.name });
                    message.success('已保存');
                    reload();
                    return true;
                  } catch (err) {
                    message.error(pickErrMsg(err, '保存失败，请重试'));
                    return false;
                  }
                }}
              >
                <ProFormText
                  name="name"
                  label="名称"
                  rules={[{ required: true, message: '请输入名称' }]}
                />
              </ModalForm>,
              <Popconfirm
                key="delete"
                title={`确认删除「${record.name}」?(被数据集引用时将拒绝删除)`}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={async () => {
                  try {
                    await deleteTag(record.id);
                    message.success('已删除');
                    reload();
                  } catch (err) {
                    message.error(pickErrMsg(err, '删除失败，请重试'));
                  }
                }}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>,
            ]
          : [<span key="readonly">-</span>],
    },
  ];

  const tagOptions = allTags.map((t) => ({ label: t.name, value: t.id }));

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据集仓库', path: '/datasets/list' },
        { title: '标签管理' },
      ])}
      title="标签管理"
      content="维护数据集标签(新增 / 重命名 / 删除 / 合并仅管理员)。"
    >
      <Card>
        <ProTable<DataPlatform.Tag>
          actionRef={actionRef}
          rowKey="id"
          search={false}
          options={{ reload: true, density: false, setting: false }}
          pagination={false}
          rowSelection={
            canAdmin
              ? {
                  selectedRowKeys: selected,
                  onChange: (keys) => setSelected(keys as string[]),
                }
              : false
          }
          toolBarRender={() => {
            const btns: ReactNode[] = [];
            if (!canAdmin) return btns;
            if (selected.length > 0) {
              btns.push(
                <Popconfirm
                  key="batch"
                  title={`删除选中的 ${selected.length} 个标签?(任一被引用将拒绝删除)`}
                  okText="删除"
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  onConfirm={async () => {
                    try {
                      await batchDeleteTags({ ids: selected });
                      message.success(`已删除 ${selected.length} 个`);
                      setSelected([]);
                      reload();
                    } catch (err) {
                      message.error(pickErrMsg(err, '删除失败，请重试'));
                    }
                  }}
                >
                  <Button danger>批量删除({selected.length})</Button>
                </Popconfirm>,
              );
            }
            btns.push(
              <Button
                key="create"
                type="primary"
                onClick={() => setCreateOpen(true)}
              >
                新建标签
              </Button>,
            );
            btns.push(
              <Button key="merge" onClick={() => setMergeOpen(true)}>
                合并标签
              </Button>,
            );
            return btns;
          }}
          request={async () => {
            const res = await listTags();
            setAllTags(res.data);
            return { data: res.data, success: res.success };
          }}
          columns={columns}
        />
      </Card>

      <ModalForm<DataPlatform.TagCreate>
        title="新建标签"
        width={380}
        open={createOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setCreateOpen}
        onFinish={async (values) => {
          try {
            await createTag({ name: values.name });
            message.success('已创建');
            setCreateOpen(false);
            reload();
            return true;
          } catch (err) {
            message.error(pickErrMsg(err, '创建失败，请重试'));
            return false;
          }
        }}
      >
        <ProFormText
          name="name"
          label="名称"
          placeholder="如 NLP / 风控 / 高质量"
          rules={[{ required: true, message: '请输入标签名称' }]}
        />
      </ModalForm>

      <ModalForm<DataPlatform.TagMerge>
        title="合并标签"
        width={420}
        open={mergeOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={(v) => {
          setMergeOpen(v);
          if (!v) setSelected([]);
        }}
        onFinish={async (values) => {
          if (!values.sourceId || !values.targetId) {
            message.warning('请选择源标签与目标标签');
            return false;
          }
          if (values.sourceId === values.targetId) {
            message.warning('源标签与目标标签不能相同');
            return false;
          }
          try {
            await mergeTags({
              sourceId: values.sourceId,
              targetId: values.targetId,
            });
            message.success('已合并');
            setMergeOpen(false);
            setSelected([]);
            reload();
            return true;
          } catch (err) {
            message.error(pickErrMsg(err, '合并失败，请重试'));
            return false;
          }
        }}
      >
        <ProForm.Group>
          <ProFormSelect
            name="sourceId"
            label="源标签(将被删除)"
            width="sm"
            options={tagOptions}
            rules={[{ required: true, message: '请选择源标签' }]}
          />
          <ProFormSelect
            name="targetId"
            label="目标标签(保留)"
            width="sm"
            options={tagOptions}
            rules={[{ required: true, message: '请选择目标标签' }]}
          />
        </ProForm.Group>
        <div style={{ color: 'var(--ant-color-text-secondary)', fontSize: 12 }}>
          源标签下所有数据集将改挂到目标标签(同时挂两个的数据集自动去重),源标签随后删除。
        </div>
      </ModalForm>
    </PageContainer>
  );
};

export default TagsPage;
