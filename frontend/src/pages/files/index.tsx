import {
  FolderAddOutlined,
  FolderOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { Access, useAccess } from '@umijs/max';
import type { UploadProps } from 'antd';
import {
  Breadcrumb,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Table,
  Upload,
} from 'antd';
import dayjs from 'dayjs';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createFolder,
  deleteFileObject,
  deleteFolder,
  getFileDownloadUrl,
  listFiles,
  listPlatformBuckets,
  previewFile,
  uploadPlatformFile,
} from '@/services/data-platform';

/** 字节数转人类可读（与数据集列表保持一致） */
const fmtSize = (n?: number) => {
  if (!n && n !== 0) return '-';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

/** 从后端错误对象里取 message（409 等业务错误，后端返回 {success,message}） */
const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

/** 单元格值渲染：对象转 JSON，其余转字符串 */
const cellText = (v: unknown) =>
  v === null || v === undefined
    ? ''
    : typeof v === 'object'
      ? JSON.stringify(v)
      : String(v);

/** 表格行：文件夹或文件（folders 在前） */
type Row =
  | { kind: 'folder'; name: string }
  | { kind: 'file'; entry: DataPlatform.FileEntry };

type PreviewState = {
  columns: string[];
  data: Record<string, any>[];
  message?: string;
};

const FilesPage: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const [buckets, setBuckets] = useState<string[]>([]);
  const [bucket, setBucket] = useState<string>();
  const [prefix, setPrefix] = useState<string>('');
  const [folderOpen, setFolderOpen] = useState(false);
  const [folderForm] = Form.useForm<{ name: string }>();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState<PreviewState>();
  const [previewName, setPreviewName] = useState<string>();

  const loadBuckets = useCallback(async () => {
    try {
      const res = await listPlatformBuckets();
      const list = res.data ?? [];
      setBuckets(list);
      setBucket((cur) => cur ?? list[0]);
    } catch {
      // 静默：未配置平台存储时整体不可用，由列表区给出提示
    }
  }, []);

  useEffect(() => {
    loadBuckets();
  }, [loadBuckets]);

  const goPrefix = (next: string) => {
    setPrefix(next);
    actionRef.current?.reload();
  };

  const handleDownload = async (key: string) => {
    try {
      const res = await getFileDownloadUrl({ bucket: bucket as string, key });
      window.open(res.data.url, '_blank');
    } catch {
      messageApi.error('获取下载链接失败，请重试');
    }
  };

  const handlePreview = async (entry: DataPlatform.FileEntry) => {
    setPreviewName(entry.name);
    setPreviewOpen(true);
    setPreview(undefined);
    setPreviewLoading(true);
    try {
      const res = await previewFile({
        bucket: bucket as string,
        key: entry.key,
        limit: 50,
      });
      setPreview(res.data);
    } catch {
      setPreview({ columns: [], data: [], message: '预览失败，文件可能无法解析' });
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleDeleteObject = async (key: string) => {
    try {
      await deleteFileObject({ bucket: bucket as string, key });
      messageApi.success('删除成功');
      actionRef.current?.reload();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '删除失败，请重试'));
    }
  };

  const handleDeleteFolder = async (name: string) => {
    try {
      const res = await deleteFolder({
        bucket: bucket as string,
        prefix: `${prefix}${name}/`,
      });
      messageApi.success(`已删除文件夹，共 ${res?.data?.deleted ?? 0} 个对象`);
      actionRef.current?.reload();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '删除文件夹失败，请重试'));
    }
  };

  const handleCreateFolder = async () => {
    const values = await folderForm.validateFields();
    try {
      await createFolder({ bucket: bucket as string, prefix, name: values.name });
      messageApi.success('已创建文件夹');
      setFolderOpen(false);
      folderForm.resetFields();
      actionRef.current?.reload();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '创建文件夹失败，请重试'));
    }
  };

  // 上传到当前 bucket/prefix（仅 admin）
  const customRequest: NonNullable<UploadProps['customRequest']> = async (
    options,
  ) => {
    const { file, onSuccess, onError } = options;
    const formData = new FormData();
    formData.append('bucket', bucket as string);
    formData.append('prefix', prefix);
    formData.append('file', file as File);
    try {
      const res = await uploadPlatformFile(formData);
      onSuccess?.(res);
      messageApi.success(`${(file as File).name} 上传成功`);
      actionRef.current?.reload();
    } catch (err) {
      onError?.(err as Error);
      messageApi.error(`${(file as File).name} 上传失败`);
    }
  };

  // 面包屑：根目录 + 各 prefix 分段（点击回退到该段）
  const segments = prefix.split('/').filter(Boolean);
  const breadcrumbItems = [
    {
      title: <a onClick={() => goPrefix('')}>根目录</a>,
      key: '__root__',
    },
    ...segments.map((seg, i) => {
      const next = `${segments.slice(0, i + 1).join('/')}/`;
      const isLast = i === segments.length - 1;
      return {
        key: next,
        title: isLast ? seg : <a onClick={() => goPrefix(next)}>{seg}</a>,
      };
    }),
  ];

  const columns: ProColumns<Row>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_, row) =>
        row.kind === 'folder' ? (
          <a onClick={() => goPrefix(`${prefix}${row.name}/`)}>
            <FolderOutlined style={{ marginRight: 6 }} />
            {row.name}
          </a>
        ) : (
          <span>{row.entry.name}</span>
        ),
    },
    {
      title: '大小',
      width: 120,
      render: (_, row) => (row.kind === 'folder' ? '-' : fmtSize(row.entry.size)),
    },
    {
      title: '修改时间',
      width: 200,
      render: (_, row) =>
        row.kind === 'folder' || !row.entry.lastModified
          ? '-'
          : dayjs(row.entry.lastModified).format('YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 220,
      render: (_, row) =>
        row.kind === 'folder'
          ? [
              <a
                key="enter"
                onClick={() => goPrefix(`${prefix}${row.name}/`)}
              >
                进入
              </a>,
              access.canAdmin ? (
                <Popconfirm
                  key="del-folder"
                  title="确认删除该文件夹？"
                  description="将递归删除该文件夹下的全部对象，不可恢复；被托管数据集引用的对象会被拦截。"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDeleteFolder(row.name)}
                >
                  <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>
                    删除文件夹
                  </a>
                </Popconfirm>
              ) : null,
            ]
          : [
              <a key="download" onClick={() => handleDownload(row.entry.key)}>
                下载
              </a>,
              <a key="preview" onClick={() => handlePreview(row.entry)}>
                预览
              </a>,
              access.canAdmin ? (
                <Popconfirm
                  key="delete"
                  title="确认删除该文件？"
                  description="被托管数据集引用的对象将被拦截。"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDeleteObject(row.entry.key)}
                >
                  <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
                </Popconfirm>
              ) : null,
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
      {contextHolder}
      <Space style={{ marginBottom: 16 }} wrap>
        <span>存储桶：</span>
        <Select
          style={{ width: 240 }}
          placeholder="选择存储桶"
          value={bucket}
          options={buckets.map((b) => ({ label: b, value: b }))}
          onChange={(v) => {
            setBucket(v);
            goPrefix('');
          }}
        />
        <Breadcrumb items={breadcrumbItems} />
      </Space>

      <ProTable<Row>
        headerTitle="文件列表"
        actionRef={actionRef}
        rowKey={(row) => (row.kind === 'folder' ? `d:${row.name}` : `f:${row.entry.key}`)}
        search={false}
        pagination={false}
        options={{ reload: true, setting: false, density: false }}
        params={{ bucket, prefix }}
        toolBarRender={() => [
          <Access key="upload" accessible={!!access.canAdmin}>
            <Upload
              showUploadList={false}
              customRequest={customRequest}
              disabled={!bucket}
            >
              <Button icon={<UploadOutlined />} disabled={!bucket}>
                上传
              </Button>
            </Upload>
          </Access>,
          <Access key="new-folder" accessible={!!access.canAdmin}>
            <Button
              icon={<FolderAddOutlined />}
              disabled={!bucket}
              onClick={() => setFolderOpen(true)}
            >
              新建文件夹
            </Button>
          </Access>,
        ]}
        request={async () => {
          if (!bucket) return { data: [], success: true };
          const res = await listFiles({ bucket, prefix });
          const rows: Row[] = [
            ...(res.data.folders ?? []).map(
              (name) => ({ kind: 'folder', name }) as Row,
            ),
            ...(res.data.files ?? []).map(
              (entry) => ({ kind: 'file', entry }) as Row,
            ),
          ];
          return { data: rows, success: res.success };
        }}
        columns={columns}
      />

      <Modal
        title="新建文件夹"
        open={folderOpen}
        destroyOnHidden
        onOk={handleCreateFolder}
        onCancel={() => {
          setFolderOpen(false);
          folderForm.resetFields();
        }}
      >
        <Form form={folderForm} layout="vertical">
          <Form.Item
            name="name"
            label="文件夹名称"
            rules={[
              { required: true, message: '请输入文件夹名称' },
              {
                pattern: /^[^/]+$/,
                message: '名称不能包含「/」',
              },
            ]}
          >
            <Input placeholder="请输入文件夹名称" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        width={900}
        open={previewOpen}
        title={`预览：${previewName ?? ''}`}
        onClose={() => {
          setPreviewOpen(false);
          setPreview(undefined);
          setPreviewName(undefined);
        }}
      >
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
              description={preview?.message ?? '暂无可预览数据'}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )}
        </Spin>
      </Drawer>
    </PageContainer>
  );
};

export default FilesPage;
