import {
  ModalForm,
  PageContainer,
  ProCard,
  type ProColumns,
  ProDescriptions,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { history, useParams } from '@umijs/max';
import {
  Button,
  Empty,
  message,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { type FC, useEffect, useState } from 'react';
import {
  extractLakeToDataset,
  getDataLakeDetail,
  getSnapshotPresignedUrl,
  renameLakeSnapshot,
} from '@/services/data-platform';
import { UploadChannelTag } from '@/utils/uploadChannel';
import LakeSnapshotPreview from './components/LakeSnapshotPreview';

const { Text } = Typography;

const DATA_CATEGORY_LABEL: Record<DataPlatform.DataLakeDataCategory, string> = {
  database: '数据库',
  tabular: '表格数据',
  document: '文档',
  image: '图片',
  audio: '音频',
  video: '视频',
  text: '文本',
};

/**
 * 快照展示文件名。与后端 lake_extract 取名优先级一致:
 * original_filename(上传原文件名/用户改名)> db_table(DB 采集来源表名)。
 */
const snapshotFilename = (
  r: DataPlatform.DataLakeSnapshot,
): string | undefined =>
  (r.sourceMetadata?.original_filename ?? r.sourceMetadata?.db_table) as
    | string
    | undefined;

/** 字节数人类可读 */
const formatSize = (bytes: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

/**
 * 数据湖详情页 —— 元信息 + 快照列表。
 *
 * 快照 = 不可变的 source_v 版本归档,一次接入产生一个快照。
 * 血缘链路:数据集 → snapshot.sourceVersion → 数据湖 → 数据源。
 */
const DataLakeDetailPage: FC = () => {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<DataPlatform.DataLakeDetail | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [selectedSnapshots, setSelectedSnapshots] = useState<
    DataPlatform.DataLakeSnapshot[]
  >([]);
  const [extractOpen, setExtractOpen] = useState(false);
  const [previewSnapshot, setPreviewSnapshot] =
    useState<DataPlatform.DataLakeSnapshot | null>(null);
  const [renameTarget, setRenameTarget] =
    useState<DataPlatform.DataLakeSnapshot | null>(null);

  const reload = () => {
    if (!id) return;
    setLoading(true);
    getDataLakeDetail(id)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  };

  const handleDownload = async (snapshot: DataPlatform.DataLakeSnapshot) => {
    try {
      const res = await getSnapshotPresignedUrl(snapshot.id);
      const { url } = res;

      // 直接打开 presigned URL(浏览器会根据文件类型自动下载)
      window.open(url, '_blank');
    } catch (e: any) {
      message.error(
        e?.info?.errorMessage || e?.response?.data?.message || '下载失败',
      );
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const snapshotColumns: ProColumns<DataPlatform.DataLakeSnapshot>[] = [
    {
      title: '文件名',
      dataIndex: 'sourceMetadata',
      width: 200,
      ellipsis: true,
      render: (_, r) => {
        const filename = snapshotFilename(r);
        return filename ? (
          <Tooltip title={filename}>
            <Text>{filename}</Text>
          </Tooltip>
        ) : (
          '-'
        );
      },
    },
    {
      title: '数据类型',
      dataIndex: 'dataCategory',
      width: 90,
      render: (_, r) => (
        <Tag>{DATA_CATEGORY_LABEL[r.dataCategory] ?? r.dataCategory}</Tag>
      ),
    },
    {
      title: '存储格式',
      dataIndex: 'storageFormat',
      width: 100,
      render: (_, r) => <Tag color="default">{r.storageFormat}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'uploadChannel',
      width: 100,
      render: (_, r) => <UploadChannelTag channel={r.uploadChannel} />,
    },
    {
      title: '行数',
      dataIndex: 'rows',
      width: 90,
      align: 'right' as const,
      render: (_, r) => (r.rows != null ? r.rows.toLocaleString() : '-'),
    },
    {
      title: '大小',
      dataIndex: 'size',
      width: 100,
      align: 'right' as const,
      render: (_, r) => formatSize(r.size),
    },
    {
      title: '归档时间',
      dataIndex: 'createdAt',
      width: 168,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      width: 200,
      fixed: 'right' as const,
      render: (_, record) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            onClick={() => setPreviewSnapshot(record)}
          >
            预览
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => handleDownload(record)}
          >
            下载
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => setRenameTarget(record)}
          >
            改名
          </Button>
        </Space>
      ),
    },
  ];

  if (loading) {
    return (
      <PageContainer>
        <div style={{ padding: 48, textAlign: 'center' }}>
          <Spin size="large" />
        </div>
      </PageContainer>
    );
  }

  if (!detail) {
    return (
      <PageContainer>
        <Empty description="数据湖不存在或已删除" />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      header={{
        title: detail.name,
        subTitle: <Tag color="blue">多源汇聚</Tag>,
      }}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <ProCard title="基本信息">
          <ProDescriptions column={2}>
            <ProDescriptions.Item label="数据湖 ID">
              <Text code copyable>
                {detail.id}
              </Text>
            </ProDescriptions.Item>
            <ProDescriptions.Item label="所有者">
              {detail.owner}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="创建人">
              {detail.creator}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="创建时间">
              {dayjs(detail.createdAt).format('YYYY-MM-DD HH:mm:ss')}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="更新时间">
              {dayjs(detail.updatedAt).format('YYYY-MM-DD HH:mm:ss')}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="描述" span={2}>
              {detail.description ?? '-'}
            </ProDescriptions.Item>
          </ProDescriptions>
        </ProCard>

        <ProCard
          title={
            <Space>
              <span>文件列表</span>
              <Tag>{detail.snapshots.length} 个</Tag>
            </Space>
          }
          tooltip="快照 = 一次数据接入的不可变版本归档,永久固化、可追溯"
          extra={
            <Button
              type="primary"
              disabled={selectedSnapshots.length === 0}
              onClick={() => setExtractOpen(true)}
            >
              抽取生成数据集({selectedSnapshots.length})
            </Button>
          }
        >
          <ProTable<DataPlatform.DataLakeSnapshot>
            columns={snapshotColumns}
            dataSource={detail.snapshots}
            rowKey="id"
            search={false}
            pagination={{ pageSize: 20 }}
            options={false}
            rowSelection={{
              selectedRowKeys: selectedSnapshots.map((s) => s.id),
              onChange: (_keys, rows) =>
                setSelectedSnapshots(rows as DataPlatform.DataLakeSnapshot[]),
            }}
            locale={{
              emptyText: (
                <Empty description="该数据湖暂无快照,等待接入任务写入" />
              ),
            }}
          />
        </ProCard>
      </Space>

      <ModalForm<{
        datasetName: string;
        description?: string;
        fieldMappings?: Record<string, string>;
      }>
        title="从湖快照抽取生成数据集"
        open={extractOpen}
        onOpenChange={setExtractOpen}
        width={720}
        modalProps={{ destroyOnHidden: true }}
        onFinish={async (values) => {
          if (!id) return false;
          const hide = message.loading('正在抽取...', 0);
          try {
            // 过滤掉空模板
            const fieldMapping = values.fieldMappings
              ? Object.fromEntries(
                  Object.entries(values.fieldMappings).filter(
                    ([, template]) => template && template.trim(),
                  ),
                )
              : undefined;

            const res = await extractLakeToDataset(id, {
              snapshotIds: selectedSnapshots.map((s) => s.id),
              datasetName: values.datasetName,
              description: values.description,
              fieldMapping:
                fieldMapping && Object.keys(fieldMapping).length > 0
                  ? fieldMapping
                  : undefined,
            });
            hide();
            message.success(
              `已生成数据集: ${res?.data?.datasetName ?? values.datasetName}`,
            );
            setSelectedSnapshots([]);
            if (res?.data?.datasetId) {
              history.push(`/datasets/${res.data.datasetId}`);
            }
            return true;
          } catch (err) {
            hide();
            const e = err as {
              response?: { data?: { detail?: string; message?: string } };
            };
            message.error(
              e?.response?.data?.detail ??
                e?.response?.data?.message ??
                '抽取失败',
            );
            return false;
          }
        }}
      >
        <div style={{ marginBottom: 16, color: '#666' }}>
          将从 <b>{selectedSnapshots.length}</b> 个快照抽取数据,
          每个快照作为一个表成员落进新数据集(表名 = source_version)。
          血缘字段自动透传。
        </div>
        <ProFormText
          name="datasetName"
          label="数据集名称"
          rules={[{ required: true, message: '请填写数据集名称' }]}
          placeholder="如:2026-Q3 财务数据"
        />
        <ProFormTextArea
          name="description"
          label="描述"
          placeholder="选填"
          fieldProps={{ rows: 3 }}
        />

        {/* 逐文件配置字段映射 */}
        {selectedSnapshots.filter(
          (s) => s.dataCategory === 'database' || s.dataCategory === 'tabular',
        ).length > 0 && (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>
              字段映射配置（可选）
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              为表格类快照配置字段映射模板，使用 {'{字段名}'} 占位符拼接多字段为
              text
            </Typography.Text>
            <div style={{ marginTop: 12 }}>
              {selectedSnapshots
                .filter(
                  (s) =>
                    s.dataCategory === 'database' ||
                    s.dataCategory === 'tabular',
                )
                .map((snapshot) => (
                  <div
                    key={snapshot.id}
                    style={{
                      marginBottom: 12,
                      padding: 12,
                      border: '1px solid #d9d9d9',
                      borderRadius: 4,
                    }}
                  >
                    <div style={{ marginBottom: 8 }}>
                      <Space>
                        <Text strong>
                          {snapshotFilename(snapshot) || snapshot.id}
                        </Text>
                        <Tag>{DATA_CATEGORY_LABEL[snapshot.dataCategory]}</Tag>
                        <Tag color="default">{snapshot.storageFormat}</Tag>
                      </Space>
                    </div>
                    <ProFormTextArea
                      name={['fieldMappings', snapshot.id]}
                      placeholder="用户提问：{question}，客服回答：{answer}"
                      fieldProps={{ rows: 2, maxLength: 500 }}
                    />
                  </div>
                ))}
            </div>
          </>
        )}
      </ModalForm>

      <ModalForm<{ filename: string }>
        title="修改文件名"
        open={!!renameTarget}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null);
        }}
        width={420}
        modalProps={{ destroyOnHidden: true }}
        initialValues={{
          filename: renameTarget ? snapshotFilename(renameTarget) : undefined,
        }}
        onFinish={async (values) => {
          if (!renameTarget) return false;
          try {
            await renameLakeSnapshot(renameTarget.id, {
              filename: values.filename.trim(),
            });
            message.success('改名成功');
            reload();
            return true;
          } catch (err) {
            const e = err as {
              response?: { data?: { detail?: string; message?: string } };
            };
            message.error(
              e?.response?.data?.detail ??
                e?.response?.data?.message ??
                '改名失败',
            );
            return false;
          }
        }}
      >
        <div style={{ marginBottom: 16, color: '#666' }}>
          仅修改展示文件名,不影响已归档的物理文件与血缘字段。
        </div>
        <ProFormText
          name="filename"
          label="文件名"
          rules={[
            { required: true, whitespace: true, message: '请填写文件名' },
            {
              pattern: /^[^/\\]+$/,
              message: '文件名不能包含路径分隔符',
            },
          ]}
        />
      </ModalForm>

      <LakeSnapshotPreview
        snapshotId={previewSnapshot?.id ?? ''}
        filename={
          previewSnapshot ? snapshotFilename(previewSnapshot) : undefined
        }
        storageFormat={previewSnapshot?.storageFormat}
        open={!!previewSnapshot}
        onClose={() => setPreviewSnapshot(null)}
      />
    </PageContainer>
  );
};

export default DataLakeDetailPage;
