/**
 * 已发布数据集 — 算法工程师消费视图。
 * 只展示含已发布(publishStatus=published)版本的数据集，前端只读，无删除/编辑权限。
 * 详情抽屉:已发布版本表 + 数据预览(选版本 → 文件清单 + 按文件预览,复用 VersionFilePreview)。
 * 操作列:下载(原) + 导出到 S3(对齐数据集详情页 + 数据源列表的同一实现)。
 */
import type { ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import type { TableColumnsType } from 'antd';
import {
  Drawer,
  message,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useState } from 'react';
import { VersionFilePreview } from '@/components';
import {
  exportVersionToS3,
  getDataset,
  listBuckets,
  listDataSources,
  listDatasets,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import { SEMANTIC_TYPE_ENUM, SemanticTypeTag } from '@/utils/semanticType';
import { SOURCE_KIND_ENUM, SourceKindTag } from '@/utils/sourceKind';
import { tagColor } from '@/utils/tags';

const { Title } = Typography;

const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const DatasetsPresets: React.FC = () => {
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  // 当前预览的版本 id(默认首个已发布版本);文件清单 + 按文件预览交给 VersionFilePreview
  const [previewVersionId, setPreviewVersionId] = useState<string>();
  // 导出到 S3:记录当前要导出的版本(打开 ModalForm);为 undefined 表示未打开
  const [exportVersion, setExportVersion] =
    useState<DataPlatform.DatasetVersion>();

  const openDetail = async (id: string) => {
    const res = await getDataset(id);
    if (res?.success) {
      setDetail(res.data);
      const published = res.data.versions.find(
        (v) => v.publishStatus === 'published',
      );
      setPreviewVersionId(published?.id);
      setDetailOpen(true);
    }
  };

  const publishedVersions =
    detail?.versions.filter((v) => v.publishStatus === 'published') ?? [];

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
    {
      title: '操作',
      key: 'option',
      // 闭环终点:算法工程师取走已发布版本(后端仅 published 放行,见 /download;
      // 导出到 S3 与数据集详情/数据源列表对齐,任一即可任选其一)
      render: (_, v) => (
        <Space size="middle">
          <a
            onClick={() =>
              window.open(`/api/v1/dataset-versions/${v.id}/download`, '_blank')
            }
          >
            下载
          </a>
          <a onClick={() => setExportVersion(v)}>导出到 S3</a>
        </Space>
      ),
    },
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
      title: '来源',
      dataIndex: 'sourceKind',
      valueType: 'select',
      valueEnum: SOURCE_KIND_ENUM,
      render: (_, r) => <SourceKindTag kind={r.sourceKind} />,
    },
    {
      title: '版本',
      dataIndex: 'latestVersionLabel',
      search: false,
      width: 150,
      render: (_, r) =>
        r.latestVersionLabel ? (
          <Tag color="blue">{r.latestVersionLabel}</Tag>
        ) : (
          '-'
        ),
    },
    {
      title: '数据类型',
      dataIndex: 'semanticType',
      valueType: 'select',
      valueEnum: SEMANTIC_TYPE_ENUM,
      render: (_, r) => <SemanticTypeTag type={r.semanticType} />,
    },
    {
      title: '分类',
      dataIndex: 'categoryName',
      search: false,
      render: (_, r) => r.categoryName || <Tag bordered={false}>未设置</Tag>,
    },
    {
      title: '标签',
      dataIndex: 'tags',
      search: false,
      render: (_, r) =>
        r.tags?.length ? (
          r.tags.map((t) => (
            <Tag key={t} color={tagColor(t)}>
              {t}
            </Tag>
          ))
        ) : (
          <Tag bordered={false}>未设置</Tag>
        ),
    },
    { title: '描述', dataIndex: 'description', search: false, ellipsis: true },
    { title: '创建人', dataIndex: 'creator', search: false },
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
            semanticType: params.semanticType || undefined,
            sourceKind: params.sourceKind || undefined,
            publishStatus: 'published',
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        columns={columns}
      />

      <Drawer
        width={1100}
        open={detailOpen}
        title={detail?.name}
        onClose={() => {
          setDetailOpen(false);
          setDetail(undefined);
          setPreviewVersionId(undefined);
        }}
      >
        {detail && (
          <>
            <Title level={5}>已发布版本</Title>
            <Table<DataPlatform.DatasetVersion>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={publishedVersions}
              columns={versionColumns}
            />

            <Title level={5} style={{ marginTop: 16 }}>
              数据预览
            </Title>
            <Space style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary">版本</Typography.Text>
              <Select
                size="small"
                style={{ width: 280 }}
                value={previewVersionId}
                onChange={setPreviewVersionId}
                options={publishedVersions.map((v) => ({
                  label: v.versionLabel ?? `v${v.versionNo}`,
                  value: v.id,
                }))}
              />
            </Space>
            <VersionFilePreview
              versionId={previewVersionId}
              semanticType={detail.semanticType}
            />
          </>
        )}
      </Drawer>
      <ModalForm<DataPlatform.ExportS3Params>
        title="导出到 S3"
        width={520}
        open={!!exportVersion}
        modalProps={{ destroyOnHidden: true }}
        onOpenChange={(o) => {
          if (!o) setExportVersion(undefined);
        }}
        onFinish={async (values) => {
          if (!exportVersion) return false;
          try {
            const res = await exportVersionToS3(exportVersion.id, {
              datasourceId: values.datasourceId,
              bucket: values.bucket,
              prefix: values.prefix || undefined,
            });
            message.success(
              `已导出 ${res.data.exported} 个对象到 ${res.data.target}`,
            );
            setExportVersion(undefined);
            return true;
          } catch (e: any) {
            const msg =
              e?.info?.errorMessage ||
              e?.response?.data?.message ||
              e?.data?.message;
            message.error(msg || '导出失败，请重试');
            return false;
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          把该已发布版本的数据导出到一个已登记的 S3
          数据源（读源、写目标，绝不回写托管源）。
        </Typography.Paragraph>
        <ProFormSelect
          name="datasourceId"
          label="目标 S3 数据源"
          rules={[{ required: true, message: '请选择目标 S3 数据源' }]}
          request={async () => {
            const res = await listDataSources({ type: 's3', pageSize: 200 });
            return (res.data ?? []).map((d) => ({
              label: d.name,
              value: d.id,
            }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormSelect
          name="bucket"
          label="目标桶"
          rules={[{ required: true, message: '请选择目标桶' }]}
          dependencies={['datasourceId']}
          request={async (params) => {
            const dsId = (params as { datasourceId?: string }).datasourceId;
            if (!dsId) return [];
            const res = await listBuckets(dsId);
            return (res.data ?? []).map((b) => ({ label: b, value: b }));
          }}
          fieldProps={{ showSearch: true }}
        />
        <ProFormText
          name="prefix"
          label="目标前缀（可选）"
          placeholder="如 exports/my-dataset；对象将落在「前缀/文件名」"
        />
      </ModalForm>
    </PageContainer>
  );
};

export default DatasetsPresets;
