import { PlusOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Access, history, useAccess } from '@umijs/max';
import { Badge, Button, message, Popconfirm, Tag } from 'antd';
import { type FC, useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  deleteDataSource,
  listCategories,
  listDataSources,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { DB_KIND_LABEL, STATUS_META, TYPE_META } from './components/constants';
import DataSourceFormDrawer from './components/DataSourceFormDrawer';

const DataSourcesPage: FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingRecord, setEditingRecord] = useState<DataPlatform.DataSource>();
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id })));
    } catch {
      // 静默：分类筛选不可用不应阻断列表
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  // 新建走接入方式选择落地页(新模式);编辑仍用抽屉
  const openCreate = () => history.push('/ingest/datasources/new');

  const openEdit = (record: DataPlatform.DataSource) => {
    setEditingRecord(record);
    setDrawerOpen(true);
  };

  const handleDelete = async (id: string) => {
    try {
      const res = await deleteDataSource(id);
      if (res.success) {
        message.success('数据源已删除');
        actionRef.current?.reload();
      } else {
        message.error('删除失败');
      }
    } catch {
      message.error('删除失败，请重试');
    }
  };

  const columns: ProColumns<DataPlatform.DataSource>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'type',
      valueType: 'select',
      valueEnum: {
        s3: { text: TYPE_META.s3.label },
        hdfs: { text: TYPE_META.hdfs.label },
        database: { text: TYPE_META.database.label },
        api: { text: TYPE_META.api.label },
      },
      render: (_, record) => {
        const meta = TYPE_META[record.type];
        const label =
          record.type === 'database' && record.dbKind
            ? `数据库 · ${DB_KIND_LABEL[record.dbKind]}`
            : meta.label;
        return <Tag color={meta.color}>{label}</Tag>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      search: false,
      render: (_, record) => {
        const meta = STATUS_META[record.status];
        return <Badge status={meta.status} text={meta.label} />;
      },
    },
    {
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'select',
      fieldProps: { options: categoryOptions, allowClear: true },
      render: (_, record) => record.categoryName || '-',
    },
    {
      title: '创建人',
      dataIndex: 'creator',
      search: false,
      width: 100,
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      render: (_, r) => formatDateTime(r.createdAt),
      search: false,
      width: 180,
    },
    {
      title: '操作',
      valueType: 'option',
      width: 140,
      // 编辑/删除仅 admin 可见(后端 require_admin 双层防护);非 admin 此列为空
      render: (_, record) =>
        access.canAdmin
          ? [
              <a
                key="edit"
                onClick={() => {
                  openEdit(record);
                }}
              >
                编辑
              </a>,
              <Popconfirm
                key="delete"
                title="确认删除该数据源？"
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(record.id)}
              >
                <a style={{ color: '#ff4d4f' }}>删除</a>
              </Popconfirm>,
            ]
          : [<span key="readonly">-</span>],
    },
  ];

  return (
    <PageContainer>
      <ProTable<DataPlatform.DataSource, DataPlatform.DataSourceListParams>
        headerTitle="数据源列表"
        actionRef={actionRef}
        rowKey="id"
        search={{ labelWidth: 'auto' }}
        toolBarRender={() => [
          <Access key="category" accessible={!!access.canAdmin}>
            <Button onClick={() => setCategoryOpen(true)}>分类管理</Button>
          </Access>,
          <Access key="create" accessible={!!access.canAdmin}>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建数据源
            </Button>
          </Access>,
        ]}
        request={async (params) => {
          const { current, pageSize, name, type, categoryId } = params;
          const res = await listDataSources({
            current,
            pageSize,
            name,
            type,
            categoryId: categoryId || undefined,
          });
          return {
            data: res.data,
            total: res.total,
            success: res.success,
          };
        }}
        columns={columns}
      />
      <DataSourceFormDrawer
        open={drawerOpen}
        record={editingRecord}
        categoryOptions={categoryOptions}
        onClose={() => setDrawerOpen(false)}
        onSuccess={() => actionRef.current?.reload()}
      />
      <CategoryManager
        open={categoryOpen}
        canAdmin={!!access.canAdmin}
        onClose={() => setCategoryOpen(false)}
        onChanged={() => {
          loadCategories();
          actionRef.current?.reload();
        }}
      />
    </PageContainer>
  );
};

export default DataSourcesPage;
