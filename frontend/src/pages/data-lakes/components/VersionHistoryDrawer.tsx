import {
  type ActionType,
  ProCard,
  type ProColumns,
  ProTable,
} from '@ant-design/pro-components';
import { Button, Drawer, message, Space, Tag } from 'antd';
import dayjs from 'dayjs';
import { type FC, useRef } from 'react';
import {
  getSnapshotPresignedUrl,
  listObjectVersions,
} from '@/services/data-platform';
import { UploadChannelTag } from '@/utils/uploadChannel';
import type { ExtractItem } from './extractItem';

/** 字节数人类可读,与 detail.tsx 的 formatSize 保持一致 */
const formatSize = (bytes: number | null): string => {
  if (bytes === null || bytes === undefined) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

export type VersionHistoryDrawerProps = {
  object: DataPlatform.DataLakeObject | null;
  onClose: () => void;
  onPreview: (snapshot: DataPlatform.DataLakeSnapshot) => void;
  onExtract: (items: ExtractItem[]) => void;
};

/**
 * 文件版本历史抽屉 —— 主表「版本历史」操作打开,按 versionNo 倒序列出该文件的
 * 全部归档版本(v1..vN)。预览/下载复用快照粒度接口,单版本也可直接抽取。
 */
const VersionHistoryDrawer: FC<VersionHistoryDrawerProps> = ({
  object,
  onClose,
  onPreview,
  onExtract,
}) => {
  const actionRef = useRef<ActionType | null>(null);

  const handleDownload = async (snapshot: DataPlatform.DataLakeSnapshot) => {
    try {
      const res = await getSnapshotPresignedUrl(snapshot.id);
      window.open(res.url, '_blank');
    } catch (e: any) {
      message.error(
        e?.info?.errorMessage || e?.response?.data?.message || '下载失败',
      );
    }
  };

  const columns: ProColumns<DataPlatform.DataLakeSnapshot>[] = [
    {
      title: '版本',
      dataIndex: 'versionNo',
      width: 80,
      render: (_, r) => <Tag color="blue">v{r.versionNo ?? '-'}</Tag>,
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
      title: '来源渠道',
      dataIndex: 'uploadChannel',
      width: 110,
      render: (_, r) => <UploadChannelTag channel={r.uploadChannel} />,
    },
    {
      title: '归档时间',
      dataIndex: 'createdAt',
      width: 168,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      width: 180,
      fixed: 'right' as const,
      render: (_, record) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => onPreview(record)}>
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
            onClick={() =>
              onExtract([
                {
                  snapshotId: record.id,
                  displayName: object?.displayName ?? record.id,
                  dataCategory: record.dataCategory,
                  storageFormat: record.storageFormat,
                },
              ])
            }
          >
            抽取
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Drawer
      title={object ? `版本历史 · ${object.displayName}` : '版本历史'}
      open={!!object}
      onClose={onClose}
      width={800}
      destroyOnHidden
    >
      {object && (
        <ProCard ghost>
          <ProTable<DataPlatform.DataLakeSnapshot>
            actionRef={actionRef}
            columns={columns}
            rowKey="id"
            search={false}
            options={false}
            pagination={{ pageSize: 20 }}
            request={async (params) => {
              const res = await listObjectVersions(object.id, {
                page: params.current,
                pageSize: params.pageSize,
              });
              return {
                data: res.data ?? [],
                total: res.total ?? 0,
                success: res.success ?? true,
              };
            }}
          />
        </ProCard>
      )}
    </Drawer>
  );
};

export default VersionHistoryDrawer;
