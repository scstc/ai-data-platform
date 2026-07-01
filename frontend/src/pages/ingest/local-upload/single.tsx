import {
  CodeOutlined,
  FileExcelOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  InboxOutlined,
  PictureOutlined,
  SoundOutlined,
  TableOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import type { UploadFile, UploadProps } from 'antd';
import {
  Button,
  Card,
  message,
  Select,
  Space,
  Typography,
  theme,
  Upload,
} from 'antd';
import { type ReactNode, useCallback, useState } from 'react';
import {
  listDatasets,
  uploadBatchDataset,
  uploadMediaDataset,
} from '@/services/data-platform';
import { buildBreadcrumb } from '@/utils/breadcrumb';

const { Text } = Typography;
const { Dragger } = Upload;

/** 媒体格式集合,对应后端 landing.py IMAGE/AUDIO/VIDEO_FORMATS */
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']);
const AUDIO_EXTS = new Set(['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg']);
const VIDEO_EXTS = new Set(['mp4', 'avi', 'mov', 'mkv', 'webm']);
const MEDIA_EXTS = new Set([...IMAGE_EXTS, ...AUDIO_EXTS, ...VIDEO_EXTS]);

/** 媒体扩展名 → data_type(供 /upload-media 的 dataType 参数) */
const mediaDataType = (ext: string): 'image' | 'audio' | 'video' | null => {
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (AUDIO_EXTS.has(ext)) return 'audio';
  if (VIDEO_EXTS.has(ext)) return 'video';
  return null;
};

/** 支持格式展示(对齐后端 landing.LANDABLE_FORMATS 的非二进制、可规范化格式)。
 *  媒体(图/音/视频)走「多模态」接入,不在此列。
 *  仅作展示,不限制上传——同一批可混合任意支持的格式。 */
const FORMAT_GROUPS: {
  label: string;
  options: { value: string; label: string; icon: ReactNode; color: string }[];
}[] = [
  {
    label: '图像',
    options: [
      { value: 'jpg', label: 'JPEG', icon: <PictureOutlined />, color: '#E5484D' },
      { value: 'png', label: 'PNG', icon: <PictureOutlined />, color: '#E5484D' },
      { value: 'gif', label: 'GIF', icon: <PictureOutlined />, color: '#E5484D' },
      { value: 'webp', label: 'WebP', icon: <PictureOutlined />, color: '#E5484D' },
    ],
  },
  {
    label: '音频',
    options: [
      { value: 'mp3', label: 'MP3', icon: <SoundOutlined />, color: '#8B5CF6' },
      { value: 'wav', label: 'WAV', icon: <SoundOutlined />, color: '#8B5CF6' },
      { value: 'flac', label: 'FLAC', icon: <SoundOutlined />, color: '#8B5CF6' },
      { value: 'ogg', label: 'OGG', icon: <SoundOutlined />, color: '#8B5CF6' },
    ],
  },
  {
    label: '视频',
    options: [
      { value: 'mp4', label: 'MP4', icon: <VideoCameraOutlined />, color: '#0EA5E9' },
      { value: 'avi', label: 'AVI', icon: <VideoCameraOutlined />, color: '#0EA5E9' },
      { value: 'mov', label: 'MOV', icon: <VideoCameraOutlined />, color: '#0EA5E9' },
      { value: 'mkv', label: 'MKV', icon: <VideoCameraOutlined />, color: '#0EA5E9' },
    ],
  },
  {
    label: '表格类',
    options: [
      { value: 'csv', label: 'CSV', icon: <TableOutlined />, color: '#16A34A' },
      { value: 'tsv', label: 'TSV', icon: <TableOutlined />, color: '#16A34A' },
      {
        value: 'xlsx',
        label: 'Excel',
        icon: <FileExcelOutlined />,
        color: '#16A34A',
      },
      {
        value: 'xls',
        label: 'Excel',
        icon: <FileExcelOutlined />,
        color: '#16A34A',
      },
    ],
  },
  {
    label: '文档类',
    options: [
      {
        value: 'pdf',
        label: 'PDF',
        icon: <FilePdfOutlined />,
        color: '#E5484D',
      },
      {
        value: 'doc',
        label: 'Word',
        icon: <FileWordOutlined />,
        color: '#2D7FF9',
      },
      {
        value: 'docx',
        label: 'Word',
        icon: <FileWordOutlined />,
        color: '#2D7FF9',
      },
      {
        value: 'ppt',
        label: 'PPT',
        icon: <FilePptOutlined />,
        color: '#E5701A',
      },
      {
        value: 'pptx',
        label: 'PPT',
        icon: <FilePptOutlined />,
        color: '#E5701A',
      },
      {
        value: 'html',
        label: 'HTML',
        icon: <CodeOutlined />,
        color: '#6E56CF',
      },
    ],
  },
  {
    label: '文本类',
    options: [
      {
        value: 'txt',
        label: '纯文本',
        icon: <FileTextOutlined />,
        color: '#6B7280',
      },
      {
        value: 'log',
        label: '日志',
        icon: <FileTextOutlined />,
        color: '#6B7280',
      },
    ],
  },
  {
    label: '结构化',
    options: [
      {
        value: 'json',
        label: 'JSON',
        icon: <CodeOutlined />,
        color: '#6E56CF',
      },
      {
        value: 'jsonl',
        label: 'JSONL',
        icon: <CodeOutlined />,
        color: '#6E56CF',
      },
    ],
  },
];

const SUPPORTED_EXTS = new Set([
  ...FORMAT_GROUPS.flatMap((g) => g.options.map((o) => o.value)),
  'jpeg', 'm4a', 'aac', 'webm', 'bmp', // aliases not in display list
]);

/** 单文件体积上限,与后端 _MAX_MEDIA_FILE_BYTES(200MB)对齐 */
const MAX_FILE_BYTES = 200 * 1024 * 1024;

const getExt = (filename: string): string => {
  const i = filename.lastIndexOf('.');
  return i >= 0 ? filename.slice(i + 1).toLowerCase() : '';
};

/** 单一数据接入:一批同格式文件 → 原件存内置 MinIO + 合并生成一个 jsonl 数据集。 */
const SingleUploadPage: React.FC = () => {
  const [datasetId, setDatasetId] = useState<string>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [datasetOptions, setDatasetOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [datasetLoading, setDatasetLoading] = useState(false);

  // 目标数据集下拉:按关键词拉取已有数据集(数据集优先流程,上传落入其 draft 版本)
  const loadDatasetOptions = useCallback((keyword?: string) => {
    setDatasetLoading(true);
    listDatasets({ name: keyword || undefined, pageSize: 50 })
      .then((res) =>
        setDatasetOptions(
          (res.data ?? []).map((d) => ({ label: d.name, value: d.id })),
        ),
      )
      .catch(() => {
        /* 拉取失败不阻断,留空列表 */
      })
      .finally(() => setDatasetLoading(false));
  }, []);
  const { token } = theme.useToken();

  // 仅暂存、不自动上传:校验支持的扩展名 + 单文件 200MB,提交时统一发送
  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    if (!SUPPORTED_EXTS.has(getExt(file.name))) {
      message.error(`「${file.name}」暂不支持该格式,已忽略`);
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_FILE_BYTES) {
      message.error(`「${file.name}」超过单文件 200MB 上限`);
      return Upload.LIST_IGNORE;
    }
    return false;
  };

  const onSubmit = async () => {
    if (fileList.length === 0) {
      message.warning('请至少添加一个文件');
      return;
    }
    if (!datasetId) {
      message.warning('请选择目标数据集');
      return;
    }

    const exts = fileList.map((f) => getExt(f.name));
    const mediaTypes = new Set(exts.map((e) => mediaDataType(e)).filter(Boolean));
    const hasMedia = exts.some((e) => MEDIA_EXTS.has(e));
    const hasNonMedia = exts.some((e) => !MEDIA_EXTS.has(e));

    if (hasMedia && hasNonMedia) {
      message.error('媒体文件(图像/音频/视频)请单独上传,不能与其他格式混用');
      return;
    }
    if (hasMedia && mediaTypes.size > 1) {
      message.error('媒体文件每次只能上传同一模态(图像、音频、视频三选一)');
      return;
    }

    const fd = new FormData();
    fileList.forEach((f) => {
      if (f.originFileObj) fd.append('files', f.originFileObj as File);
    });
    fd.append('datasetId', datasetId);

    setSubmitting(true);
    const hide = message.loading('正在上传并落入数据集…', 0);
    try {
      if (hasMedia) {
        const dataType = [...mediaTypes][0] as string;
        fd.append('dataType', dataType);
        const res = await uploadMediaDataset(fd, { skipErrorHandler: true });
        hide();
        message.success(
          `已生成多模态 JSONL 并落入数据集「${res.data?.name ?? ''}」`,
        );
      } else {
        fd.append('safety_check', 'false');
        fd.append('raw', 'true');
        const res = await uploadBatchDataset(fd, { skipErrorHandler: true });
        hide();
        message.success(
          `已上传并作为表成员落入数据集「${res.data?.name ?? ''}」`,
        );
      }
      setFileList([]);
      history.push(`/datasets/detail?id=${datasetId}`);
    } catch (e: any) {
      hide();
      const body = e?.response?.data ?? e?.data;
      message.error(body?.message ?? e?.message ?? '上传失败,请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '单一数据' },
      ])}
      title="单一数据接入"
      content="批量上传文件:原始文件原样存入内置 MinIO,不做内容解析,合并生成一个数据集。"
      onBack={() => history.push('/ingest/local-upload')}
    >
      <Card style={{ maxWidth: 760 }}>
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <div>
            <Text strong>支持格式</Text>
            <div style={{ marginTop: 8 }}>
              {FORMAT_GROUPS.map((group) => (
                <div key={group.label} style={{ marginBottom: 14 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {group.label}
                  </Text>
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 8,
                      marginTop: 6,
                    }}
                  >
                    {group.options.map((opt) => (
                      <div
                        key={opt.value}
                        style={{
                          width: 84,
                          padding: '10px 8px',
                          textAlign: 'center',
                          borderRadius: 8,
                          border: `1px solid ${token.colorBorderSecondary}`,
                          background: token.colorBgContainer,
                        }}
                      >
                        <span style={{ fontSize: 22, color: opt.color }}>
                          {opt.icon}
                        </span>
                        <div
                          style={{
                            marginTop: 4,
                            fontSize: 13,
                            fontWeight: 500,
                          }}
                        >
                          {opt.label}
                        </div>
                        <div
                          style={{
                            fontSize: 11,
                            color: token.colorTextTertiary,
                          }}
                        >
                          .{opt.value}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <Text strong>
              目标数据集 <Text type="danger">*</Text>
            </Text>
            <div style={{ marginTop: 8 }}>
              <Select
                style={{ width: '100%' }}
                placeholder="选择已有数据集(上传的文件将作为表成员加入)"
                value={datasetId}
                onChange={setDatasetId}
                showSearch
                allowClear
                filterOption={false}
                onSearch={(kw) => loadDatasetOptions(kw)}
                onFocus={() => loadDatasetOptions()}
                options={datasetOptions}
                notFoundContent={
                  datasetLoading
                    ? '加载中…'
                    : '无匹配数据集,请先到「数据集」页新建'
                }
              />
            </div>
          </div>
          <Dragger
            multiple
            fileList={fileList}
            beforeUpload={beforeUpload}
            onChange={({ fileList: fl }) => setFileList(fl)}
            accept={Array.from(SUPPORTED_EXTS)
              .map((e) => `.${e}`)
              .join(',')}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此处</p>
            <p className="ant-upload-hint">
              支持多文件批量上传;图像/音频/视频请单独上传同一模态,将自动生成多模态 JSONL;单文件最大 200MB。
            </p>
          </Dragger>
          <Button
            type="primary"
            onClick={onSubmit}
            loading={submitting}
            disabled={fileList.length === 0}
          >
            上传并生成数据集（{fileList.length}）
          </Button>
        </Space>
      </Card>
    </PageContainer>
  );
};

export default SingleUploadPage;
