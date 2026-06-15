import { FolderOutlined } from '@ant-design/icons';
import { Breadcrumb, Empty, message, Select, Space, Spin, Table } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { listFiles, listPlatformBuckets } from '@/services/data-platform';
import type { AccessType } from './constants';
import { formatFileSize, isExtAllowed } from './constants';

export interface PlatformSelection {
  bucket: string;
  keys: string[];
}

interface Props {
  /** 当前类型栏:决定可选文件的扩展名过滤 */
  accessType: AccessType;
  /** 选择变化回调(bucket + 勾选的对象 key 全路径) */
  onChange: (sel: PlatformSelection) => void;
}

type Row =
  | { kind: 'folder'; name: string }
  | { kind: 'file'; key: string; name: string; size?: number };

/**
 * 文件管理对象选择器:浏览平台 MinIO,按类型栏扩展名过滤勾选文件。
 * 选择仅作用于「当前桶 + 当前目录」这一视图;切桶或换目录会清空选择
 * (避免跨目录隐式累积造成提交时桶/对象错配)。
 */
const FileManagerPicker: React.FC<Props> = ({ accessType, onChange }) => {
  const [messageApi, contextHolder] = message.useMessage();
  const [buckets, setBuckets] = useState<string[]>([]);
  const [bucket, setBucket] = useState<string>();
  const [prefix, setPrefix] = useState<string>('');
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [checkedKeys, setCheckedKeys] = useState<string[]>([]);

  useEffect(() => {
    listPlatformBuckets()
      .then((res) => {
        const list = res.data ?? [];
        setBuckets(list);
        setBucket((cur) => cur ?? list[0]);
      })
      .catch(() => messageApi.error('加载存储桶失败,请检查平台存储配置'));
  }, [messageApi]);

  const load = useCallback(async () => {
    if (!bucket) return;
    setLoading(true);
    try {
      const res = await listFiles({ bucket, prefix });
      const folders: Row[] = (res.data.folders ?? []).map((name) => ({
        kind: 'folder',
        name,
      }));
      const files: Row[] = (res.data.files ?? []).map((e) => ({
        kind: 'file',
        key: e.key,
        name: e.name,
        size: e.size,
      }));
      setRows([...folders, ...files]);
    } catch {
      setRows([]);
      messageApi.error('目录加载失败,请重试');
    } finally {
      setLoading(false);
    }
  }, [bucket, prefix, messageApi]);

  useEffect(() => {
    load();
  }, [load]);

  /** 清空当前视图选择并通知宿主(切桶/换目录时调用;nextBucket 用于切桶即时生效) */
  const resetSelection = (nextBucket?: string) => {
    setCheckedKeys([]);
    onChange({ bucket: (nextBucket ?? bucket) as string, keys: [] });
  };

  const goPrefix = (next: string) => {
    setPrefix(next);
    resetSelection();
  };

  const segments = prefix.split('/').filter(Boolean);
  const breadcrumbItems = [
    { key: '__root__', title: <a onClick={() => goPrefix('')}>根目录</a> },
    ...segments.map((seg, i) => {
      const next = `${segments.slice(0, i + 1).join('/')}/`;
      const isLast = i === segments.length - 1;
      return {
        key: next,
        title: isLast ? seg : <a onClick={() => goPrefix(next)}>{seg}</a>,
      };
    }),
  ];

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_: unknown, row: Row) =>
        row.kind === 'folder' ? (
          <a onClick={() => goPrefix(`${prefix}${row.name}/`)}>
            <FolderOutlined style={{ marginRight: 6 }} />
            {row.name}
          </a>
        ) : (
          <span>{row.name}</span>
        ),
    },
    {
      title: '大小',
      width: 120,
      render: (_: unknown, row: Row) =>
        row.kind === 'folder' ? '-' : formatFileSize(row.size ?? 0),
    },
  ];

  return (
    <div>
      {contextHolder}
      <Space style={{ marginBottom: 12 }} wrap>
        <span>存储桶:</span>
        <Select
          style={{ width: 220 }}
          placeholder="选择存储桶"
          value={bucket}
          options={buckets.map((b) => ({ label: b, value: b }))}
          onChange={(v) => {
            setBucket(v);
            setPrefix('');
            resetSelection(v);
          }}
        />
        <Breadcrumb items={breadcrumbItems} />
      </Space>
      <Spin spinning={loading}>
        {rows.length === 0 ? (
          <Empty
            description="该目录为空"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <Table<Row>
            size="small"
            rowKey={(r) => (r.kind === 'folder' ? `d:${r.name}` : `f:${r.key}`)}
            pagination={false}
            scroll={{ y: 300 }}
            dataSource={rows}
            columns={columns}
            rowSelection={{
              // checkedKeys 存原始 key;rowKey 文件为 `f:${key}`,故映射回带前缀
              selectedRowKeys: checkedKeys.map((k) => `f:${k}`),
              getCheckboxProps: (row) => ({
                // 文件夹不可勾;文件按类型栏扩展名过滤
                disabled:
                  row.kind === 'folder' ||
                  !isExtAllowed((row as { name: string }).name, accessType),
              }),
              onChange: (_keys, selectedRows) => {
                const keys = selectedRows
                  .filter((r) => r.kind === 'file')
                  .map((r) => (r as { key: string }).key);
                setCheckedKeys(keys);
                onChange({ bucket: bucket as string, keys });
              },
            }}
          />
        )}
      </Spin>
    </div>
  );
};

export default FileManagerPicker;
