/**
 * 已发布数据集 — 算法工程师消费视图。
 * 只展示含已发布(publishStatus=published)版本的数据集，前端只读，无删除/编辑权限。
 */
import type { ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Tag, Tooltip, Typography } from 'antd';
import { getDataset, listDatasets, previewDatasetVersion } from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { useState } from 'react';
import { Drawer, Spin, Table, Empty } from 'antd';
import type { TableColumnsType } from 'antd';

const { Title } = Typography;

const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const cellText = (v: unknown) =>
  v === null || v === undefined
    ? ''
    : typeof v === 'object'
      ? JSON.stringify(v)
      : String(v);

const DatasetsPresets: React.FC = () => {
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [preview, setPreview] = useState<DataPlatform.DatasetPreview>();
  const [previewLoading, setPreviewLoading] = useState(false);

  const openDetail = async (id: string) => {
    const res = await getDataset(id);
    if (res?.success) {
      setDetail(res.data);
      setPreview(undefined);
      setDetailOpen(true);
      // 默认预览第一个已发布版本
      const published = res.data.versions.find((v) => v.publishStatus === 'published');
      if (published) {
        setPreviewLoading(true);
        try {
          const pv = await previewDatasetVersion(published.id, { limit: 50 });
          setPreview(pv);
        } finally {
          setPreviewLoading(false);
        }
      }
    }
  };

  const versionColumns: TableColumnsType<DataPlatform.DatasetVersion> = [
    {
      title: '版本',
      dataIndex: 'versionNo',
      render: (_, v) => v.versionLabel ?? `v${v.versionNo}`,
    },
    { title: '行数', dataIndex: 'rows', render: (_, v) => v.rows ?? '-' },
    { title: '大小', dataIndex: 'size', render: (_, v) => fmtSize(v.size) },
    {
      title: '扫描结论',
      dataIndex: 'scanVerdict',
      render: (_, v) => (
        <Tooltip title={v.verdictNote}>
          <Tag color="green">
            通过{v.verdictSource === 'manual' ? '·人工' : ''}
          </Tag>
        </Tooltip>
      ),
    },
    {
      title: '发布时间',
      dataIndex: 'publishedAt',
      render: (_, v) => formatDateTime(v.publishedAt),
    },
    { title: '说明', dataIndex: 'note', render: (_, v) => v.note ?? '-' },
  ];

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
      valueEnum: {
        text: { text: 'text' },
        multimodal: { text: 'multimodal' },
        qa: { text: 'qa' },
        cot: { text: 'cot' },
        preference: { text: 'preference' },
        timeseries: { text: 'timeseries' },
        gis: { text: 'gis' },
      },
      render: (_, r) => (r.dataType ? <Tag>{r.dataType}</Tag> : '-'),
    },
    { title: '分类', dataIndex: 'categoryName', render: (_, r) => r.categoryName || '-', search: false },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      search: false,
      render: (_, r) => formatDateTime(r.createdAt),
    },
    {
      title: '操作',
      valueType: 'option',
      render: (_, record) => [
        <a key="detail" onClick={() => openDetail(record.id)}>
          详情
        </a>,
      ],
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
        headerTitle="已发布数据集（可训练）"
        rowKey="id"
        search={{ labelWidth: 'auto' }}
        options={{ reload: true }}
        request={async (params) => {
          const res = await listDatasets({
            current: params.current,
            pageSize: params.pageSize,
            name: params.name || undefined,
            dataType: params.dataType || undefined,
            publishStatus: 'published',
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />

      <Drawer
        width={860}
        open={detailOpen}
        title={detail?.name}
        onClose={() => {
          setDetailOpen(false);
          setDetail(undefined);
          setPreview(undefined);
        }}
      >
        {detail && (
          <>
            <Title level={5}>已发布版本</Title>
            <Table<DataPlatform.DatasetVersion>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={detail.versions.filter((v) => v.publishStatus === 'published')}
              columns={versionColumns}
            />

            <Title level={5} style={{ marginTop: 16 }}>
              数据预览{preview ? `（共 ${preview.total} 行，前 50 行）` : ''}
            </Title>
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
    </PageContainer>
  );
};

export default DatasetsPresets;
