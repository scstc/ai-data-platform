import {
  FolderAddOutlined,
  FolderOutlined,
  SearchOutlined,
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
  Modal,
  message,
  Popconfirm,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Upload,
} from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ACCESS_TYPES, getExtension } from '@/pages/ingest/access/constants';
import {
  createFolder,
  deleteFileObject,
  deleteFolder,
  getFileDownloadUrl,
  hostPlatformFiles,
  listDatasets,
  listFiles,
  listPlatformBuckets,
  previewFile,
  uploadPlatformFile,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

/** 文件管理默认桶:平台自有数据桶,固定为 uploads(对应后端 storage_minio_upload_bucket)。 */
const DEFAULT_BUCKET = 'uploads';

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
  const canUpload = access.hasPerm('ingest:file:upload');
  const canAddFolder = access.hasPerm('ingest:file:add');
  const canDownload = access.hasPerm('ingest:file:download');
  const canImport = access.hasPerm('ingest:file:import');
  const canRemove = access.hasPerm('ingest:file:remove');
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
  // 上传进度（null = 空闲；0~100 = 上传中），用于大文件可见反馈
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  // 数据集 id→名称映射:uploads 桶的顶层文件夹是数据集 id(dset-xxx),
  // 用名称关联让用户看懂文件夹对应哪个数据集
  const [datasetNames, setDatasetNames] = useState<Record<string, string>>({});
  // 数据集 id→创建时间:S3 目录前缀本身无时间戳,根目录下的文件夹排序借数据集的
  // createdAt 实现「最新创建的排最前」
  const [datasetCreatedAt, setDatasetCreatedAt] = useState<
    Record<string, string>
  >({});
  // 当前目录关键字过滤(纯前端,按文件夹/文件名 + 数据集名匹配)
  const [keyword, setKeyword] = useState('');

  const loadBuckets = useCallback(async () => {
    try {
      const res = await listPlatformBuckets();
      const list = res.data ?? [];
      setBuckets(list);
      // 默认锁定 uploads 桶(平台自有数据桶);该桶不存在时回退首个
      setBucket(
        (cur) =>
          cur ?? (list.includes(DEFAULT_BUCKET) ? DEFAULT_BUCKET : list[0]),
      );
    } catch {
      // 静默：未配置平台存储时整体不可用，由列表区给出提示
    }
  }, []);

  useEffect(() => {
    loadBuckets();
  }, [loadBuckets]);

  // 拉数据集列表建 id→名称映射(uploads 桶文件夹名是数据集 id,需关联出名称)。
  // 不限 500:文件管理根目录每个文件夹是一个数据集,数量大时不能截断,分页拉全量。
  useEffect(() => {
    let cancelled = false;
    const PAGE = 500;
    const MAX_PAGES = 40; // 安全上限(2 万),防异常 total 死循环
    (async () => {
      try {
        const map: Record<string, string> = {};
        const createdMap: Record<string, string> = {};
        for (let current = 1; current <= MAX_PAGES; current += 1) {
          const res = await listDatasets({ current, pageSize: PAGE });
          for (const d of res.data ?? []) {
            map[d.id] = d.name;
            createdMap[d.id] = d.createdAt;
          }
          const got = res.data?.length ?? 0;
          if (got < PAGE || Object.keys(map).length >= (res.total ?? Infinity))
            break;
        }
        if (!cancelled) {
          setDatasetNames(map);
          setDatasetCreatedAt(createdMap);
        }
      } catch {
        // 静默:名称映射失败不影响浏览
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const goPrefix = (next: string) => {
    setPrefix(next);
    setKeyword('');
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
      setPreview({
        columns: [],
        data: [],
        message: '预览失败，文件可能无法解析',
      });
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
      await createFolder({
        bucket: bucket as string,
        prefix,
        name: values.name,
      });
      messageApi.success('已创建文件夹');
      setFolderOpen(false);
      folderForm.resetFields();
      actionRef.current?.reload();
    } catch (err) {
      messageApi.error(pickErrMsg(err, '创建文件夹失败，请重试'));
    }
  };

  // 零拷贝接入:把平台 MinIO 对象登记为受管数据集(不复制文件），打通文件管理→数据集→数据加工。
  // dataType 仅对命中数据接入分栏的扩展名赋值;其余可接入格式(如 xlsx/doc/html)交后端
  // INGESTABLE_FORMATS 把关——真不支持时透出后端文案,不在前端按栏目白名单误拒。
  const handleHost = async (entry: DataPlatform.FileEntry) => {
    const ext = getExtension(entry.name);
    const t = ACCESS_TYPES.find((a) => a.extensions.includes(ext));
    try {
      await hostPlatformFiles({
        bucket: bucket as string,
        keys: [entry.key],
        dataType: t?.key,
      });
      messageApi.success(
        t
          ? `已接入为数据集（零拷贝），可在「数据接入 · ${t.label}」查看`
          : '已接入为数据集（零拷贝），可在数据集列表 / 数据加工中使用',
      );
    } catch (err) {
      messageApi.error(pickErrMsg(err, '接入为数据集失败，请重试'));
    }
  };

  // 上传到当前 bucket/prefix（仅 admin）
  const customRequest: NonNullable<UploadProps['customRequest']> = async (
    options,
  ) => {
    const { file, onSuccess, onError, onProgress } = options;
    const formData = new FormData();
    formData.append('bucket', bucket as string);
    formData.append('prefix', prefix);
    formData.append('file', file as File);
    setUploadPercent(0);
    try {
      const res = await uploadPlatformFile(formData, {
        onUploadProgress: (e: ProgressEvent) => {
          if (!e.total) return;
          const percent = Math.round((e.loaded / e.total) * 100);
          setUploadPercent(percent);
          onProgress?.({ percent });
        },
      });
      onSuccess?.(res);
      messageApi.success(`${(file as File).name} 上传成功`);
      actionRef.current?.reload();
    } catch (err) {
      onError?.(err as Error);
      messageApi.error(`${(file as File).name} 上传失败`);
    } finally {
      setUploadPercent(null);
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
            {datasetNames[row.name] ? (
              <>
                {row.name}
                <span
                  style={{
                    marginLeft: 8,
                    color: 'var(--ant-color-text-secondary)',
                    fontSize: 12,
                  }}
                >
                  ({datasetNames[row.name]})
                </span>
              </>
            ) : (
              row.name
            )}
          </a>
        ) : (
          <span>{row.entry.name}</span>
        ),
    },
    {
      title: '大小',
      width: 120,
      render: (_, row) =>
        row.kind === 'folder' ? '-' : fmtSize(row.entry.size),
    },
    {
      title: '修改时间',
      width: 200,
      render: (_, row) =>
        row.kind === 'folder' ? '-' : formatDateTime(row.entry.lastModified),
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      width: 220,
      render: (_, row) =>
        row.kind === 'folder'
          ? [
              <a key="enter" onClick={() => goPrefix(`${prefix}${row.name}/`)}>
                进入
              </a>,
              canRemove ? (
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
              canDownload ? (
                <a key="download" onClick={() => handleDownload(row.entry.key)}>
                  下载
                </a>
              ) : null,
              <a key="preview" onClick={() => handlePreview(row.entry)}>
                预览
              </a>,
              canImport ? (
                <Popconfirm
                  key="host"
                  title="接入为数据集？"
                  description="零拷贝登记为受管数据集（不复制文件），随后可在数据接入页查看并用于数据加工。"
                  okText="接入"
                  onConfirm={() => handleHost(row.entry)}
                >
                  <a>接入数据集</a>
                </Popconfirm>
              ) : null,
              canRemove ? (
                <Popconfirm
                  key="delete"
                  title="确认删除该文件？"
                  description="被托管数据集引用的对象将被拦截。"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDeleteObject(row.entry.key)}
                >
                  <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>
                    删除
                  </a>
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
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="过滤当前目录"
          style={{ width: 220 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
      </Space>

      <ProTable<Row>
        headerTitle="文件列表"
        actionRef={actionRef}
        rowKey={(row) =>
          row.kind === 'folder' ? `d:${row.name}` : `f:${row.entry.key}`
        }
        search={false}
        pagination={false}
        options={{ reload: true, setting: false, density: false }}
        params={{ bucket, prefix }}
        postData={(data: Row[]) => {
          // 纯前端过滤:文件夹按 id + 数据集名匹配,文件按文件名;空关键字原样返回
          const k = keyword.trim().toLowerCase();
          if (!k) return data;
          return data.filter((row: Row) => {
            const hay =
              row.kind === 'folder'
                ? `${row.name} ${datasetNames[row.name] ?? ''}`.toLowerCase()
                : row.entry.name.toLowerCase();
            return hay.includes(k);
          });
        }}
        toolBarRender={() => [
          <Access key="upload" accessible={canUpload}>
            <Space>
              <Upload
                showUploadList={false}
                customRequest={customRequest}
                disabled={!bucket || uploadPercent !== null}
              >
                <Button
                  icon={<UploadOutlined />}
                  disabled={!bucket || uploadPercent !== null}
                  loading={uploadPercent !== null}
                >
                  上传
                </Button>
              </Upload>
              {uploadPercent !== null && (
                <Progress
                  percent={uploadPercent}
                  size="small"
                  style={{ width: 140 }}
                  status={uploadPercent < 100 ? 'active' : 'success'}
                />
              )}
            </Space>
          </Access>,
          <Access key="new-folder" accessible={canAddFolder}>
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
          // 根目录下的文件夹 = 数据集 id,借数据集 createdAt 排序(S3 目录前缀本身
          // 无时间戳);非根目录(如某数据集内的 v1/v2/originals)按名称保持原序。
          const folders = [...(res.data.folders ?? [])];
          if (!prefix) {
            folders.sort(
              (a, b) =>
                new Date(datasetCreatedAt[b] ?? 0).getTime() -
                new Date(datasetCreatedAt[a] ?? 0).getTime(),
            );
          }
          // 文件按 lastModified 降序,最新上传的排最前。
          const files = [...(res.data.files ?? [])].sort(
            (a, b) =>
              new Date(b.lastModified ?? 0).getTime() -
              new Date(a.lastModified ?? 0).getTime(),
          );
          const rows: Row[] = [
            ...folders.map((name) => ({ kind: 'folder', name }) as Row),
            ...files.map((entry) => ({ kind: 'file', entry }) as Row),
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
              pagination={{ pageSize: 20 }}
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
