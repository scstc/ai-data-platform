import {
  type ActionType,
  ModalForm,
  PageContainer,
  ProCard,
  type ProColumns,
  ProDescriptions,
  ProFormDependency,
  ProFormRadio,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { history, useParams } from '@umijs/max';
import { Button, Empty, message, Space, Spin, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import { type FC, useEffect, useRef, useState } from 'react';
import {
  extractLakeToDataset,
  getDataLakeDetail,
  listDatasets,
  listLakeObjects,
} from '@/services/data-platform';
import type { ExtractItem } from './components/extractItem';
import LakeSnapshotPreview from './components/LakeSnapshotPreview';
import MergeObjectsModal from './components/MergeObjectsModal';
import VersionHistoryDrawer from './components/VersionHistoryDrawer';

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
 * 数据湖详情页 —— 元信息 + 文件列表(三层模型:湖 → 文件 → 版本)。
 *
 * 文件 DataLakeObject = 一张表 / 一个对象的稳定身份,按 identity_key 判重;
 * 同一文件多次采集追加新版本,而非各自散落的快照。血缘链路:
 * 数据集 → 版本快照 → 文件 → 数据湖 → 数据源。见 docs/数据湖文件版本模型整改.md。
 */
const DataLakeDetailPage: FC = () => {
  const { id } = useParams<{ id: string }>();
  const [meta, setMeta] = useState<DataPlatform.DataLake | null>(null);
  const [loading, setLoading] = useState(true);
  const actionRef = useRef<ActionType | null>(null);

  const [selectedObjects, setSelectedObjects] = useState<
    DataPlatform.DataLakeObject[]
  >([]);
  const [extractItems, setExtractItems] = useState<ExtractItem[] | null>(null);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [historyObject, setHistoryObject] =
    useState<DataPlatform.DataLakeObject | null>(null);
  const [previewSnapshot, setPreviewSnapshot] =
    useState<DataPlatform.DataLakeSnapshot | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    getDataLakeDetail(id)
      .then(setMeta)
      .catch(() => setMeta(null))
      .finally(() => setLoading(false));
  }, [id]);

  const reloadObjects = () => actionRef.current?.reload();

  const mergeableSelected =
    selectedObjects.length >= 2 &&
    selectedObjects.every((o) => o.storageFormat === 'parquet');

  const objectColumns: ProColumns<DataPlatform.DataLakeObject>[] = [
    {
      title: '文件名',
      dataIndex: 'displayName',
      width: 220,
      ellipsis: true,
      render: (_, r) => (
        <Space size={4}>
          <Text>{r.displayName}</Text>
          {r.origin === 'merged' && <Tag color="purple">合并</Tag>}
        </Space>
      ),
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
      render: (_, r) =>
        r.storageFormat ? <Tag color="default">{r.storageFormat}</Tag> : '-',
    },
    {
      title: '最新版本',
      dataIndex: 'latestVersionNo',
      width: 90,
      render: (_, r) => <Tag color="blue">v{r.latestVersionNo}</Tag>,
    },
    {
      title: '版本数',
      dataIndex: 'versionCount',
      width: 90,
      align: 'right' as const,
    },
    {
      title: '行数',
      dataIndex: 'latestRows',
      width: 90,
      align: 'right' as const,
      render: (_, r) =>
        r.latestRows != null ? r.latestRows.toLocaleString() : '-',
    },
    {
      title: '大小',
      dataIndex: 'totalSize',
      width: 100,
      align: 'right' as const,
      render: (_, r) => formatSize(r.totalSize),
    },
    {
      title: '最近归档时间',
      dataIndex: 'updatedAt',
      width: 168,
      render: (_, r) => dayjs(r.updatedAt).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      width: 120,
      fixed: 'right' as const,
      render: (_, record) => (
        <Button
          type="link"
          size="small"
          onClick={() => setHistoryObject(record)}
        >
          版本历史
        </Button>
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

  if (!meta) {
    return (
      <PageContainer>
        <Empty description="数据湖不存在或已删除" />
      </PageContainer>
    );
  }

  return (
    <PageContainer
      header={{
        title: meta.name,
        subTitle: <Tag color="blue">多源汇聚</Tag>,
      }}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <ProCard title="基本信息">
          <ProDescriptions column={2}>
            <ProDescriptions.Item label="数据湖 ID">
              <Text code copyable>
                {meta.id}
              </Text>
            </ProDescriptions.Item>
            <ProDescriptions.Item label="所有者">
              {meta.owner}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="创建人">
              {meta.creator}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="创建时间">
              {dayjs(meta.createdAt).format('YYYY-MM-DD HH:mm:ss')}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="更新时间">
              {dayjs(meta.updatedAt).format('YYYY-MM-DD HH:mm:ss')}
            </ProDescriptions.Item>
            <ProDescriptions.Item label="描述" span={2}>
              {meta.description ?? '-'}
            </ProDescriptions.Item>
          </ProDescriptions>
        </ProCard>

        <ProCard
          title="文件列表"
          tooltip="文件 = 一张表/一个对象的稳定身份,按身份键判重;同一文件多次采集追加新版本"
          extra={
            <Space>
              <Button
                disabled={!mergeableSelected}
                onClick={() => setMergeOpen(true)}
              >
                数据合并({selectedObjects.length})
              </Button>
              <Button
                type="primary"
                disabled={selectedObjects.length === 0}
                onClick={() =>
                  setExtractItems(
                    selectedObjects
                      .filter((o) => !!o.latestSnapshotId)
                      .map((o) => ({
                        snapshotId: o.latestSnapshotId as string,
                        displayName: o.displayName,
                        dataCategory: o.dataCategory,
                        storageFormat: o.storageFormat,
                      })),
                  )
                }
              >
                抽取生成数据集({selectedObjects.length})
              </Button>
            </Space>
          }
        >
          <ProTable<DataPlatform.DataLakeObject>
            actionRef={actionRef}
            columns={objectColumns}
            rowKey="id"
            search={false}
            pagination={{ pageSize: 20 }}
            options={false}
            rowSelection={{
              selectedRowKeys: selectedObjects.map((o) => o.id),
              onChange: (_keys, rows) =>
                setSelectedObjects(rows as DataPlatform.DataLakeObject[]),
            }}
            request={async (params) => {
              if (!id) return { data: [], total: 0, success: true };
              const res = await listLakeObjects(id, {
                page: params.current,
                pageSize: params.pageSize,
              });
              return {
                data: res.data ?? [],
                total: res.total ?? 0,
                success: res.success ?? true,
              };
            }}
            locale={{
              emptyText: (
                <Empty description="该数据湖暂无文件,等待接入任务写入" />
              ),
            }}
          />
        </ProCard>
      </Space>

      <ModalForm<{
        targetMode: 'new' | 'existing';
        datasetId?: string;
        datasetName?: string;
        description?: string;
        fieldMappings?: Record<string, string>;
      }>
        title="从湖文件抽取生成数据集"
        open={!!extractItems}
        onOpenChange={(open) => {
          if (!open) setExtractItems(null);
        }}
        width={720}
        modalProps={{ destroyOnHidden: true }}
        initialValues={{ targetMode: 'new' }}
        onFinish={async (values) => {
          if (!id || !extractItems) return false;
          const hide = message.loading('正在抽取...', 0);
          try {
            const fieldMapping = values.fieldMappings
              ? Object.fromEntries(
                  Object.entries(values.fieldMappings).filter(([, template]) =>
                    template?.trim(),
                  ),
                )
              : undefined;

            const res = await extractLakeToDataset(id, {
              snapshotIds: extractItems.map((s) => s.snapshotId),
              datasetId:
                values.targetMode === 'existing' ? values.datasetId : undefined,
              datasetName:
                values.targetMode === 'new' ? values.datasetName : undefined,
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
            setExtractItems(null);
            setSelectedObjects([]);
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
          将从 <b>{extractItems?.length ?? 0}</b> 个版本抽取数据,
          每个版本作为一个表成员落进目标数据集(表名 = source_version)。
          血缘字段自动透传。
        </div>
        <ProFormRadio.Group
          name="targetMode"
          label="目标数据集"
          options={[
            { label: '新建数据集', value: 'new' },
            { label: '选择已有数据集', value: 'existing' },
          ]}
        />
        <ProFormDependency name={['targetMode']}>
          {({ targetMode }) =>
            targetMode === 'existing' ? (
              <ProFormSelect
                name="datasetId"
                label="选择数据集"
                placeholder="搜索并选择已有数据集(抽取结果作为新表成员追加)"
                rules={[{ required: true, message: '请选择目标数据集' }]}
                showSearch
                fieldProps={{ filterOption: false }}
                request={async ({ keyWords }) => {
                  const res = await listDatasets({
                    name: keyWords || undefined,
                    pageSize: 50,
                  });
                  return (res.data ?? []).map((d) => ({
                    label: d.name,
                    value: d.id,
                  }));
                }}
              />
            ) : (
              <ProFormText
                name="datasetName"
                label="数据集名称"
                rules={[{ required: true, message: '请填写数据集名称' }]}
                placeholder="如:2026-Q3 财务数据"
              />
            )
          }
        </ProFormDependency>
        <ProFormDependency name={['targetMode']}>
          {({ targetMode }) =>
            targetMode === 'existing' ? null : (
              <ProFormTextArea
                name="description"
                label="描述"
                placeholder="选填"
                fieldProps={{ rows: 3 }}
              />
            )
          }
        </ProFormDependency>

        {/* 逐文件配置字段映射 */}
        {(extractItems ?? []).filter(
          (s) => s.dataCategory === 'database' || s.dataCategory === 'tabular',
        ).length > 0 && (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>
              字段映射配置（可选）
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              为表格类版本配置字段映射模板，使用 {'{字段名}'} 占位符拼接多字段为
              text
            </Typography.Text>
            <div style={{ marginTop: 12 }}>
              {(extractItems ?? [])
                .filter(
                  (s) =>
                    s.dataCategory === 'database' ||
                    s.dataCategory === 'tabular',
                )
                .map((item) => (
                  <div
                    key={item.snapshotId}
                    style={{
                      marginBottom: 12,
                      padding: 12,
                      border: '1px solid #d9d9d9',
                      borderRadius: 4,
                    }}
                  >
                    <div style={{ marginBottom: 8 }}>
                      <Space>
                        <Text strong>{item.displayName}</Text>
                        <Tag>{DATA_CATEGORY_LABEL[item.dataCategory]}</Tag>
                        {item.storageFormat && (
                          <Tag color="default">{item.storageFormat}</Tag>
                        )}
                      </Space>
                    </div>
                    <ProFormTextArea
                      name={['fieldMappings', item.snapshotId]}
                      placeholder="用户提问：{question}，客服回答：{answer}"
                      fieldProps={{ rows: 2, maxLength: 500 }}
                    />
                  </div>
                ))}
            </div>
          </>
        )}
      </ModalForm>

      {id && (
        <MergeObjectsModal
          lakeId={id}
          open={mergeOpen}
          onOpenChange={setMergeOpen}
          selected={selectedObjects}
          onSuccess={() => {
            setMergeOpen(false);
            setSelectedObjects([]);
            reloadObjects();
          }}
        />
      )}

      <VersionHistoryDrawer
        object={historyObject}
        onClose={() => setHistoryObject(null)}
        onPreview={(snapshot) => setPreviewSnapshot(snapshot)}
        onExtract={(items) => {
          setHistoryObject(null);
          setExtractItems(items);
        }}
      />

      <LakeSnapshotPreview
        snapshotId={previewSnapshot?.id ?? ''}
        filename={historyObject?.displayName}
        storageFormat={previewSnapshot?.storageFormat}
        open={!!previewSnapshot}
        onClose={() => setPreviewSnapshot(null)}
      />
    </PageContainer>
  );
};

export default DataLakeDetailPage;
