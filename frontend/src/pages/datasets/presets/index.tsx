/**
 * 已发布数据集 — 算法工程师消费视图。
 * 详情抽屉:左侧已发布版本列表(点选高亮) + 右侧版本详情 + 底部数据预览。
 */
import type { ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import {
  Col,
  Descriptions,
  Divider,
  Drawer,
  Flex,
  List,
  message,
  Row,
  Space,
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

const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const DatasetsPresets: React.FC = () => {
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [activeVersionId, setActiveVersionId] = useState<string>();
  const [exportVersion, setExportVersion] =
    useState<DataPlatform.DatasetVersion>();

  const openDetail = async (id: string) => {
    const res = await getDataset(id);
    if (res?.success) {
      setDetail(res.data);
      const published = res.data.versions.filter(
        (v) => v.publishStatus === 'published',
      );
      setActiveVersionId(published[published.length - 1]?.id);
      setDetailOpen(true);
    }
  };

  const publishedVersions =
    detail?.versions.filter((v) => v.publishStatus === 'published') ?? [];

  const activeVer = publishedVersions.find((v) => v.id === activeVersionId);

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
        width={1200}
        open={detailOpen}
        title={
          detail && (
            <Space>
              {detail.name}
              {detail.semanticType && (
                <SemanticTypeTag type={detail.semanticType} />
              )}
              <Tag color="green">已发布 {publishedVersions.length} 个版本</Tag>
            </Space>
          )
        }
        onClose={() => {
          setDetailOpen(false);
          setDetail(undefined);
          setActiveVersionId(undefined);
        }}
      >
        {detail && (
          <>
            {/* 数据集元数据摘要 */}
            <Descriptions size="small" column={{ xs: 1, sm: 3 }} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="来源">
                <SourceKindTag kind={detail.sourceKind} />
              </Descriptions.Item>
              <Descriptions.Item label="格式">
                {detail.sourceFormat?.toUpperCase() ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label="分类">
                {detail.categoryName ?? '-'}
              </Descriptions.Item>
              {detail.description && (
                <Descriptions.Item label="描述" span={3}>
                  {detail.description}
                </Descriptions.Item>
              )}
              {detail.tags?.length ? (
                <Descriptions.Item label="标签" span={3}>
                  {detail.tags.map((t) => (
                    <Tag key={t} color={tagColor(t)}>{t}</Tag>
                  ))}
                </Descriptions.Item>
              ) : null}
            </Descriptions>

            <Divider style={{ margin: '0 0 16px 0' }} />

            {/* 版本主详情:左侧版本列表 + 右侧版本详情 */}
            <Row gutter={16}>
              <Col xs={24} md={8} lg={7}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  已发布版本（{publishedVersions.length}）
                </Typography.Text>
                <List<DataPlatform.DatasetVersion>
                  size="small"
                  bordered
                  rowKey="id"
                  style={{ marginTop: 8, borderRadius: 8 }}
                  dataSource={[...publishedVersions].reverse()}
                  renderItem={(v) => {
                    const selected = activeVersionId === v.id;
                    return (
                      <List.Item
                        onClick={() => setActiveVersionId(v.id)}
                        style={{
                          cursor: 'pointer',
                          paddingInline: 12,
                          background: selected
                            ? 'var(--ant-color-primary-bg)'
                            : undefined,
                          borderInlineStart: `2px solid ${
                            selected
                              ? 'var(--ant-color-primary)'
                              : 'transparent'
                          }`,
                          transition: 'background 0.2s',
                        }}
                      >
                        <Flex vertical gap={4} style={{ width: '100%' }}>
                          <Typography.Text strong={selected}>
                            {v.versionLabel ?? `v${v.versionNo}`}
                          </Typography.Text>
                          <Flex gap={4} wrap="wrap">
                            <Tag
                              color="green"
                              style={{ marginInlineEnd: 0, fontSize: 11 }}
                            >
                              已发布
                            </Tag>
                            {v.verdictSource === 'manual' && (
                              <Tag
                                color="orange"
                                style={{ marginInlineEnd: 0, fontSize: 11 }}
                              >
                                人工接受
                              </Tag>
                            )}
                          </Flex>
                          <Typography.Text
                            type="secondary"
                            style={{ fontSize: 11 }}
                          >
                            {v.rows != null ? `${v.rows} 行` : '-'} ·{' '}
                            {fmtSize(v.size)}
                          </Typography.Text>
                          {v.publishedAt && (
                            <Typography.Text
                              type="secondary"
                              style={{ fontSize: 11 }}
                            >
                              发布于 {formatDateTime(v.publishedAt)}
                            </Typography.Text>
                          )}
                        </Flex>
                      </List.Item>
                    );
                  }}
                />
              </Col>

              {/* 右侧:选中版本详情 */}
              <Col xs={24} md={16} lg={17}>
                {activeVer ? (
                  <div>
                    <Flex justify="space-between" align="center" style={{ marginBottom: 12 }}>
                      <Typography.Title level={5} style={{ margin: 0 }}>
                        {activeVer.versionLabel ?? `v${activeVer.versionNo}`}
                      </Typography.Title>
                      <Space>
                        <a
                          onClick={() =>
                            window.open(
                              `/api/v1/dataset-versions/${activeVer.id}/download`,
                              '_blank',
                            )
                          }
                        >
                          下载
                        </a>
                        <a onClick={() => setExportVersion(activeVer)}>
                          导出到 S3
                        </a>
                      </Space>
                    </Flex>
                    <Descriptions size="small" column={{ xs: 1, sm: 2 }} bordered>
                      <Descriptions.Item label="版本号">
                        {activeVer.versionLabel ?? `v${activeVer.versionNo}`}
                      </Descriptions.Item>
                      <Descriptions.Item label="格式">
                        <Tag>{(activeVer.format ?? '-').toUpperCase()}</Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="行数">
                        {activeVer.rows ?? '-'}
                      </Descriptions.Item>
                      <Descriptions.Item label="大小">
                        {fmtSize(activeVer.size)}
                      </Descriptions.Item>
                      <Descriptions.Item label="来源">
                        <Tag color={activeVer.origin === 'managed' ? 'green' : 'gold'}>
                          {activeVer.origin ?? '-'}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="扫描结论">
                        <Tooltip title={activeVer.verdictNote}>
                          <Tag color="green">
                            通过{activeVer.verdictSource === 'manual' ? '·人工' : ''}
                          </Tag>
                        </Tooltip>
                      </Descriptions.Item>
                      {activeVer.trainType && (
                        <Descriptions.Item label="训练用途">
                          <Tag color="blue">{activeVer.trainType}</Tag>
                        </Descriptions.Item>
                      )}
                      {activeVer.schemaVariant && (
                        <Descriptions.Item label="Schema 变体">
                          <Tag color="cyan">{activeVer.schemaVariant}</Tag>
                        </Descriptions.Item>
                      )}
                      <Descriptions.Item label="发布时间">
                        {formatDateTime(activeVer.publishedAt)}
                      </Descriptions.Item>
                      {activeVer.note && (
                        <Descriptions.Item label="说明" span={2}>
                          {activeVer.note}
                        </Descriptions.Item>
                      )}
                    </Descriptions>
                  </div>
                ) : (
                  <Typography.Text type="secondary">
                    选择左侧版本查看详情
                  </Typography.Text>
                )}
              </Col>
            </Row>

            <Divider style={{ margin: '16px 0' }} />

            {/* 数据预览 */}
            <Typography.Title level={5} style={{ margin: '0 0 8px 0' }}>
              数据预览
              {activeVer && (
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 13, fontWeight: 400, marginLeft: 8 }}
                >
                  {activeVer.versionLabel ?? `v${activeVer.versionNo}`}
                </Typography.Text>
              )}
            </Typography.Title>
            <VersionFilePreview
              versionId={activeVersionId}
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
