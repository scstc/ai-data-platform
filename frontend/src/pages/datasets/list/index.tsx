import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProDescriptions,
  ProFormDatePicker,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import type { TableColumnsType } from 'antd';
import {
  Button,
  Drawer,
  Empty,
  message,
  Popconfirm,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { useRef, useState } from 'react';
import {
  batchDeleteDatasets,
  deleteDataset,
  getDataset,
  listDatasets,
  previewDatasetVersion,
  updateDataset,
} from '@/services/data-platform';

/** 数据集类型枚举（列表搜索 + 编辑表单复用） */
const DATA_TYPE_ENUM = {
  text: { text: 'text' },
  multimodal: { text: 'multimodal' },
  qa: { text: 'qa' },
  cot: { text: 'cot' },
  preference: { text: 'preference' },
  timeseries: { text: 'timeseries' },
  gis: { text: 'gis' },
};

/** 字节数转人类可读 */
const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

/** 单元格值渲染：对象转 JSON，其余转字符串 */
const cellText = (v: unknown) =>
  v === null || v === undefined
    ? ''
    : typeof v === 'object'
      ? JSON.stringify(v)
      : String(v);

const DatasetsList: React.FC = () => {
  const actionRef = useRef<ActionType | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [activeVersion, setActiveVersion] = useState<string>();
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [editOpen, setEditOpen] = useState(false);

  const handleBatchDelete = async () => {
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteDatasets(selectedRowKeys);
      hide();
      message.success(
        `已删除 ${res?.data?.deleted ?? selectedRowKeys.length} 个数据集`,
      );
      setSelectedRowKeys([]);
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('批量删除失败，请重试');
    }
  };

  const loadPreview = async (versionId: string) => {
    setActiveVersion(versionId);
    setPreviewLoading(true);
    try {
      const res = await previewDatasetVersion(versionId, { limit: 50 });
      setPreview(res);
    } finally {
      setPreviewLoading(false);
    }
  };

  const openDetail = async (id: string) => {
    const res = await getDataset(id);
    if (res?.success) {
      setDetail(res.data);
      setDetailOpen(true);
      const latest = res.data.versions[res.data.versions.length - 1];
      setPreview(undefined);
      setActiveVersion(undefined);
      if (latest) await loadPreview(latest.id);
    }
  };

  const handleDelete = async (id: string) => {
    const hide = message.loading('正在删除…', 0);
    try {
      await deleteDataset(id);
      hide();
      message.success('删除成功');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('删除失败，请重试');
    }
  };

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (dom, record) => (
        <a
          onClick={(e) => {
            e.preventDefault();
            openDetail(record.id);
          }}
        >
          {dom}
        </a>
      ),
    },
    {
      title: '类型',
      dataIndex: 'dataType',
      valueType: 'select',
      valueEnum: DATA_TYPE_ENUM,
      render: (_, r) => (r.dataType ? <Tag>{r.dataType}</Tag> : '-'),
    },
    { title: '描述', dataIndex: 'description', search: false, ellipsis: true },
    { title: '创建人', dataIndex: 'creator' },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      search: false,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAtRange',
      valueType: 'dateRange',
      hideInTable: true,
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      render: (_, record) => [
        <a key="detail" onClick={() => openDetail(record.id)}>
          详情
        </a>,
        <Popconfirm
          key="delete"
          title="确认删除该数据集？"
          description="将删除其全部版本与产物文件，不可恢复。"
          okText="删除"
          okButtonProps={{ danger: true }}
          onConfirm={() => handleDelete(record.id)}
        >
          <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
        </Popconfirm>,
      ],
    },
  ];

  const versionColumns: TableColumnsType<DataPlatform.DatasetVersion> = [
    { title: '版本', dataIndex: 'versionNo', render: (_, v) => `v${v.versionNo}` },
    { title: '行数', dataIndex: 'rows', render: (_, v) => v.rows ?? '-' },
    { title: '大小', dataIndex: 'size', render: (_, v) => fmtSize(v.size) },
    {
      title: '来源',
      dataIndex: 'origin',
      render: (_, v) => (
        <Tag color={v.origin === 'managed' ? 'green' : 'gold'}>{v.origin}</Tag>
      ),
    },
    {
      title: '操作',
      render: (_, v) => (
        <a
          onClick={() => loadPreview(v.id)}
          style={{
            fontWeight: activeVersion === v.id ? 600 : undefined,
          }}
        >
          预览
        </a>
      ),
    },
  ];

  const previewColumns = (preview?.columns ?? []).map((c) => ({
    title: c,
    dataIndex: c,
    key: c,
    ellipsis: true,
    render: (v: unknown) => cellText(v),
  }));

  return (
    <PageContainer>
      <ProTable<DataPlatform.Dataset>
        headerTitle="数据集仓库"
        actionRef={actionRef}
        rowKey="id"
        search={{ labelWidth: 'auto' }}
        options={{ reload: true }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as string[]),
        }}
        tableAlertOptionRender={() => (
          <Popconfirm
            title={`确认删除选中的 ${selectedRowKeys.length} 个数据集？`}
            description="将删除其全部版本与产物文件，不可恢复。"
            okText="删除"
            okButtonProps={{ danger: true }}
            onConfirm={handleBatchDelete}
          >
            <Button type="link" danger>
              批量删除
            </Button>
          </Popconfirm>
        )}
        request={async (params) => {
          const range = params.createdAt as [string, string] | undefined;
          const res = await listDatasets({
            current: params.current,
            pageSize: params.pageSize,
            name: params.name || undefined,
            dataType: params.dataType || undefined,
            creator: params.creator || undefined,
            createdStart: range?.[0] || undefined,
            createdEnd: range?.[1] || undefined,
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />

      <Drawer
        width={900}
        open={detailOpen}
        title={detail?.name}
        extra={
          detail && (
            <Button type="primary" onClick={() => setEditOpen(true)}>
              编辑
            </Button>
          )
        }
        onClose={() => {
          setDetailOpen(false);
          setDetail(undefined);
          setPreview(undefined);
          setActiveVersion(undefined);
        }}
      >
        {detail && (
          <>
            <ProDescriptions<DataPlatform.DatasetDetail>
              column={2}
              dataSource={detail}
              columns={[
                { title: 'ID', dataIndex: 'id' },
                { title: '名称', dataIndex: 'name' },
                {
                  title: '类型',
                  dataIndex: 'dataType',
                  render: (_, r) => r.dataType ?? '-',
                },
                {
                  title: '分级',
                  dataIndex: 'sensitivityLevel',
                  render: (_, r) => r.sensitivityLevel ?? '-',
                },
                {
                  title: '分类',
                  dataIndex: 'businessCategory',
                  render: (_, r) => r.businessCategory ?? '-',
                },
                { title: '归属', dataIndex: 'owner' },
                { title: '创建人', dataIndex: 'creator' },
                {
                  title: '最后变更人',
                  dataIndex: 'lastModifier',
                  render: (_, r) => r.lastModifier ?? '-',
                },
                {
                  title: '创建时间',
                  dataIndex: 'createdAt',
                  valueType: 'dateTime',
                },
                {
                  title: '更新时间',
                  dataIndex: 'updatedAt',
                  valueType: 'dateTime',
                },
                {
                  title: '有效期',
                  dataIndex: 'validUntil',
                  render: (_, r) =>
                    r.validUntil
                      ? dayjs(r.validUntil).format('YYYY-MM-DD')
                      : '-',
                },
                {
                  title: '描述',
                  dataIndex: 'description',
                  span: 2,
                  render: (_, r) => r.description ?? '-',
                },
              ]}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              版本
            </Typography.Title>
            <Table<DataPlatform.DatasetVersion>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={detail.versions}
              columns={versionColumns}
            />

            <Typography.Title level={5} style={{ marginTop: 16 }}>
              数据预览{preview ? `（共 ${preview.total} 行，前 50 行）` : ''}
            </Typography.Title>
            <Spin spinning={previewLoading}>
              {preview && preview.data.length > 0 ? (
                <Table
                  rowKey={(_, i) => String(i)}
                  size="small"
                  scroll={{ x: 'max-content' }}
                  pagination={{ pageSize: 10 }}
                  dataSource={preview.data}
                  columns={previewColumns}
                />
              ) : (
                <Empty
                  description={preview?.message ?? '暂无数据'}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </Spin>
          </>
        )}
      </Drawer>

      <ModalForm<DataPlatform.DatasetUpdate>
        title="编辑数据集元数据"
        width={520}
        open={editOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setEditOpen}
        initialValues={
          detail
            ? {
                name: detail.name,
                description: detail.description,
                dataType: detail.dataType,
                sensitivityLevel: detail.sensitivityLevel,
                businessCategory: detail.businessCategory,
                validUntil: detail.validUntil,
              }
            : undefined
        }
        onFinish={async (values) => {
          if (!detail) return false;
          try {
            const res = await updateDataset(detail.id, {
              ...values,
              validUntil: values.validUntil
                ? dayjs(values.validUntil).toISOString()
                : undefined,
            });
            message.success('已保存');
            setDetail(res.data);
            setEditOpen(false);
            actionRef.current?.reload();
            return true;
          } catch {
            message.error('保存失败，请重试');
            return false;
          }
        }}
      >
        <ProFormText
          name="name"
          label="名称"
          rules={[{ required: true, message: '请输入名称' }]}
        />
        <ProFormTextArea
          name="description"
          label="描述"
          fieldProps={{ rows: 3 }}
        />
        <ProFormSelect name="dataType" label="类型" valueEnum={DATA_TYPE_ENUM} />
        <ProFormSelect
          name="sensitivityLevel"
          label="分级"
          valueEnum={{
            public: { text: 'public' },
            internal: { text: 'internal' },
            confidential: { text: 'confidential' },
          }}
        />
        <ProFormText name="businessCategory" label="分类" />
        <ProFormDatePicker name="validUntil" label="有效期" />
      </ModalForm>
    </PageContainer>
  );
};

export default DatasetsList;
