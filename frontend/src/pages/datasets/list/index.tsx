import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormDependency,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import { Access, history, useAccess, useLocation } from '@umijs/max';
import { Button, message, Popconfirm, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CategoryManager } from '@/components';
import {
  batchDeleteDatasets,
  deleteDataset,
  hostS3,
  listBuckets,
  listCategories,
  listDataSources,
  listDatasets,
  listObjects,
  unhostDataset,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { SEMANTIC_TYPE_ENUM, SemanticTypeTag } from '@/utils/semanticType';

/** 数据集类型枚举（列表搜索 + 托管表单复用） */
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

const DatasetsList: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [selectedRows, setSelectedRows] = useState<DataPlatform.Dataset[]>([]);
  const [hostOpen, setHostOpen] = useState(false);
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

  // 兼容旧的 ?highlight=<datasetId> 跳转(来自低质过滤等):改为直接进详情页
  const location = useLocation();
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const highlightId = params.get('highlight');
    if (!highlightId) return;
    history.replace(`/datasets/${highlightId}`);
  }, [location.search]);

  const selectedRowKeys = selectedRows.map((r) => r.id);
  // 外部托管数据集不可删除(后端 403 兜底)——批量删除前先拦截给提示
  const hasHostedSelected = selectedRows.some((r) => r.hosted);

  const handleBatchDelete = async () => {
    const hide = message.loading('正在批量删除…', 0);
    try {
      const res = await batchDeleteDatasets(selectedRowKeys);
      hide();
      message.success(
        `已删除 ${res?.data?.deleted ?? selectedRowKeys.length} 个数据集`,
      );
      setSelectedRows([]);
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('批量删除失败，请重试');
    }
  };

  // 取消托管(仅 admin):只移除平台引用，绝不删 S3 源对象
  const handleUnhost = async (id: string) => {
    const hide = message.loading('正在取消托管…', 0);
    try {
      await unhostDataset(id);
      hide();
      message.success('已取消托管（仅移除平台引用，S3 源对象保留）');
      actionRef.current?.reload();
    } catch {
      hide();
      message.error('取消托管失败，请重试');
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

  const goDetail = (id: string) => history.push(`/datasets/${id}`);

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (dom, record) => (
        <span>
          <a
            onClick={(e) => {
              e.preventDefault();
              goDetail(record.id);
            }}
          >
            {dom}
          </a>
          {record.hosted && (
            <Tag color="geekblue" style={{ marginLeft: 8 }}>
              S3 托管
            </Tag>
          )}
        </span>
      ),
    },
    {
      title: '类型',
      dataIndex: 'dataType',
      valueType: 'select',
      valueEnum: DATA_TYPE_ENUM,
      render: (_, r) => (r.dataType ? <Tag>{r.dataType}</Tag> : '-'),
    },
    {
      title: '语义类型',
      dataIndex: 'semanticType',
      valueType: 'select',
      valueEnum: SEMANTIC_TYPE_ENUM,
      render: (_, r) => <SemanticTypeTag type={r.semanticType} />,
    },
    {
      title: '分类',
      dataIndex: 'categoryId',
      valueType: 'select',
      fieldProps: { options: categoryOptions, allowClear: true },
      render: (_, r) => r.categoryName || '-',
    },
    { title: '描述', dataIndex: 'description', search: false, ellipsis: true },
    { title: '创建人', dataIndex: 'creator' },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      search: false,
      render: (_, r) => formatDateTime(r.createdAt),
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
        <a key="detail" onClick={() => goDetail(record.id)}>
          详情
        </a>,
        // 外部托管数据集禁止删除(#18)——隐藏「删除」，改显 admin「取消托管」;
        // 受管数据集照旧显示「删除」(仅 admin，后端 require_admin 双层防护)
        record.hosted
          ? access.canAdmin && (
              <Popconfirm
                key="unhost"
                title="确认取消托管该数据集？"
                description="仅移除平台引用，不删除 S3 源对象。"
                okText="取消托管"
                onConfirm={() => handleUnhost(record.id)}
              >
                <a>取消托管</a>
              </Popconfirm>
            )
          : access.canAdmin && (
              <Popconfirm
                key="delete"
                title="确认删除该数据集？"
                description="将删除其全部版本与产物文件，不可恢复。"
                okText="删除"
                okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(record.id)}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>
            ),
      ],
    },
  ];

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
          onChange: (_keys, rows) =>
            setSelectedRows(rows as DataPlatform.Dataset[]),
        }}
        tableAlertOptionRender={() => (
          <Access accessible={!!access.canAdmin}>
            {hasHostedSelected ? (
              <Button
                type="link"
                danger
                onClick={() =>
                  message.warning(
                    '外部托管数据集不支持删除，请对其单独使用「取消托管」',
                  )
                }
              >
                批量删除
              </Button>
            ) : (
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
          </Access>
        )}
        toolBarRender={() => [
          <Access key="category" accessible={!!access.canAdmin}>
            <Button onClick={() => setCategoryOpen(true)}>分类管理</Button>
          </Access>,
          <Button
            key="host-s3"
            type="primary"
            onClick={() => setHostOpen(true)}
          >
            托管 S3 数据
          </Button>,
        ]}
        request={async (params) => {
          const range = params.createdAt as [string, string] | undefined;
          const res = await listDatasets({
            current: params.current,
            pageSize: params.pageSize,
            name: params.name || undefined,
            dataType: params.dataType || undefined,
            semanticType: params.semanticType || undefined,
            creator: params.creator || undefined,
            categoryId: (params.categoryId as string) || undefined,
            // dateRange 给的是纯日期:起取当日 0 点、止取当日 23:59:59,
            // 否则 created_at <= 当日0点 会漏掉当天创建的记录
            createdStart: range?.[0]
              ? dayjs(range[0]).startOf('day').toISOString()
              : undefined,
            createdEnd: range?.[1]
              ? dayjs(range[1]).endOf('day').toISOString()
              : undefined,
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />

      <ModalForm<DataPlatform.HostS3Params>
        title="托管 S3 数据"
        width={640}
        open={hostOpen}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={setHostOpen}
        onFinish={async (values) => {
          try {
            const res = await hostS3(values);
            const n = res?.data?.length ?? values.keys.length;
            message.success(`已托管 ${n} 个对象为受管数据集（未发生下载）`);
            actionRef.current?.reload();
            return true;
          } catch {
            message.error('托管失败，请检查数据源连接与对象选择');
            return false;
          }
        }}
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
          将三方 S3 / MinIO 上的对象登记为受管数据集版本（仅存引用，不拷贝）。
          可对其浏览 / 预览 / 加工 / 质量 /
          审核；删除源对象不提供，仅管理员可取消托管。
        </Typography.Paragraph>
        <ProFormSelect
          name="datasourceId"
          label="S3 数据源"
          placeholder="请选择 s3 类型数据源"
          rules={[{ required: true, message: '请选择 S3 数据源' }]}
          request={async () => {
            const res = await listDataSources({ type: 's3', pageSize: 100 });
            return res.data.map((d) => ({
              label: `${d.name}（${d.status}）`,
              value: d.id,
            }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormDependency name={['datasourceId']}>
          {({ datasourceId }) =>
            datasourceId ? (
              <ProFormSelect
                name="bucket"
                label="桶"
                placeholder="请选择桶"
                rules={[{ required: true, message: '请选择桶' }]}
                params={{ datasourceId }}
                request={async () => {
                  try {
                    const res = await listBuckets(datasourceId);
                    return (res.data ?? []).map((b) => ({
                      label: b,
                      value: b,
                    }));
                  } catch {
                    return [];
                  }
                }}
                fieldProps={{ showSearch: true }}
              />
            ) : null
          }
        </ProFormDependency>
        <ProFormDependency name={['datasourceId', 'bucket']}>
          {({ datasourceId, bucket }) =>
            datasourceId && bucket ? (
              <ProFormSelect
                name="keys"
                label="对象"
                mode="multiple"
                placeholder="勾选一个或多个对象（每个对象各产一个数据集）"
                rules={[{ required: true, message: '请至少选择一个对象' }]}
                params={{ datasourceId, bucket }}
                request={async () => {
                  try {
                    const res = await listObjects(datasourceId, { bucket });
                    return (res.data ?? []).map((o) => ({
                      label: `${o.key}（${fmtSize(o.size)}）`,
                      value: o.key,
                    }));
                  } catch {
                    return [];
                  }
                }}
                fieldProps={{ showSearch: true }}
              />
            ) : null
          }
        </ProFormDependency>
        <ProFormText
          name="name"
          label="数据集名称"
          tooltip="留空则按对象 key 自动命名；多选时作为名称前缀"
          placeholder="可选"
        />
        <ProFormSelect
          name="dataType"
          label="数据类型"
          valueEnum={DATA_TYPE_ENUM}
          placeholder="可选"
          fieldProps={{ allowClear: true }}
        />
        <ProFormSelect
          name="categoryId"
          label="分类"
          placeholder="可选"
          options={categoryOptions}
          fieldProps={{ allowClear: true, showSearch: true }}
        />
      </ModalForm>

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

export default DatasetsList;
