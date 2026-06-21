import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { Button, Drawer, message, Popconfirm } from 'antd';
import dayjs from 'dayjs';
import { type FC, useRef, useState } from 'react';
import {
  createCategory,
  deleteCategory,
  listCategories,
  updateCategory,
} from '@/services/data-platform';

interface CategoryPanelProps {
  /** 当前用户是否为管理员：决定新增/编辑/删除是否可用 */
  canAdmin?: boolean;
  /** 分类发生增/改/删后回调（供宿主页刷新分类筛选选项与列表） */
  onChanged?: () => void;
}

interface CategoryManagerProps extends CategoryPanelProps {
  open: boolean;
  onClose: () => void;
}

/** 从后端错误对象里取 message（409 等业务错误，后端返回 {success,message}） */
const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

/**
 * 分类 CRUD 面板（无外壳，可嵌入抽屉或独立页面，#15）。
 * 列表所有登录用户可见；新增/编辑/删除仅 admin（canAdmin）。
 * 删除被引用项时按后端 409 message 提示用量。
 */
export const CategoryPanel: FC<CategoryPanelProps> = ({
  canAdmin,
  onChanged,
}) => {
  const actionRef = useRef<ActionType | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<DataPlatform.Category>();

  const reload = () => {
    actionRef.current?.reload();
    onChanged?.();
  };

  const openCreate = () => {
    setEditing(undefined);
    setEditOpen(true);
  };

  const openEdit = (record: DataPlatform.Category) => {
    setEditing(record);
    setEditOpen(true);
  };

  const handleDelete = async (record: DataPlatform.Category) => {
    try {
      await deleteCategory(record.id);
      message.success('分类已删除');
      reload();
    } catch (err) {
      message.error(pickErrMsg(err, '删除失败，请重试'));
    }
  };

  const columns: ProColumns<DataPlatform.Category>[] = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    {
      title: '备注',
      dataIndex: 'note',
      ellipsis: true,
      render: (_, r) => r.note || '-',
    },
    { title: '创建人', dataIndex: 'creator', width: 100 },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 180,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '用量',
      dataIndex: 'usageCount',
      width: 80,
      render: (_, r) => `${r.usageCount} 处`,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 120,
      // 新增/编辑/删除仅 admin（后端 require_admin 双层防护）；非 admin 此列为空
      render: (_, record) =>
        canAdmin
          ? [
              <a key="edit" onClick={() => openEdit(record)}>
                编辑
              </a>,
              <Popconfirm
                key="delete"
                title="确认删除该分类？"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(record)}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>,
            ]
          : [<span key="readonly">-</span>],
    },
  ];

  return (
    <>
      <ProTable<DataPlatform.Category>
        actionRef={actionRef}
        rowKey="id"
        search={false}
        options={{ reload: true, density: false, setting: false }}
        pagination={false}
        toolBarRender={() =>
          canAdmin
            ? [
                <Button key="create" type="primary" onClick={openCreate}>
                  新增分类
                </Button>,
              ]
            : []
        }
        request={async () => {
          const res = await listCategories();
          return { data: res.data, success: res.success };
        }}
        columns={columns}
      />

      <ModalForm<DataPlatform.CategoryCreate>
        title={editing ? '编辑分类' : '新增分类'}
        width={420}
        open={editOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setEditOpen}
        initialValues={
          editing
            ? { name: editing.name, note: editing.note ?? undefined }
            : undefined
        }
        onFinish={async (values) => {
          try {
            if (editing) {
              await updateCategory(editing.id, values);
              message.success('已保存');
            } else {
              await createCategory(values);
              message.success('分类已创建');
            }
            setEditOpen(false);
            reload();
            return true;
          } catch (err) {
            message.error(
              pickErrMsg(
                err,
                editing ? '保存失败，请重试' : '创建失败，请重试',
              ),
            );
            return false;
          }
        }}
      >
        <ProFormText
          name="name"
          label="名称"
          placeholder="如 金融风控 / 营销活动"
          rules={[{ required: true, message: '请输入分类名称' }]}
        />
        <ProFormTextArea
          name="note"
          label="备注"
          placeholder="可选"
          fieldProps={{ rows: 3 }}
        />
      </ModalForm>
    </>
  );
};

/** 分类管理抽屉（受控词表 CRUD，#15）。内容复用 CategoryPanel，外壳为抽屉。 */
const CategoryManager: FC<CategoryManagerProps> = ({
  open,
  onClose,
  canAdmin,
  onChanged,
}) => (
  <Drawer width={720} open={open} title="分类管理" onClose={onClose}>
    <CategoryPanel canAdmin={canAdmin} onChanged={onChanged} />
  </Drawer>
);

export default CategoryManager;
