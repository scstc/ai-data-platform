import {
  CloudUploadOutlined,
  DownloadOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import { history } from '@umijs/max';
import type { UploadFile, UploadProps } from 'antd';
import {
  Button,
  Card,
  message,
  Segmented,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { useState } from 'react';
import {
  uploadBatchDataset,
  uploadMediaDataset,
} from '@/services/data-platform';

const { Text } = Typography;
const { Dragger } = Upload;

/** 场景数据「文件导入」卡片。
 *  文本/结构化类(cot/qa/preference/timeseries/gis):按示例格式准备一批同格式文件
 *  → POST /api/v1/datasets/upload-batch(携带 semantic_type),后端合并成 data.jsonl
 *  + 登记受管数据集;每类提供可下载示例文件(public/samples/)。
 *  多模态(multimodal):选模态(图像/音频/视频)上传媒体文件
 *  → POST /api/v1/datasets/upload-media,后端建一个 manifest 数据集(自动标记多模态)。 */

const MAX_FILE_BYTES = 200 * 1024 * 1024;

type Modality = { key: string; label: string; accept: string; exts: string[] };

type TypeConfig = {
  label: string;
  // 文本/结构化类(走 upload-batch)
  format?: string; // data_type,同时是唯一允许扩展名
  sampleName?: string;
  sampleUrl?: string;
  schemaNote?: string;
  // 媒体类(走 upload-media):多模态,data_type 由模态决定
  media?: boolean;
  modalities?: Modality[];
};

/** 各语义类型配置;文本类 format 须属后端 LANDABLE_FORMATS,媒体类模态须属 image/audio/video。 */
const CONFIG: Record<string, TypeConfig> = {
  cot: {
    label: 'COT 思维链',
    format: 'jsonl',
    sampleName: 'cot_example.jsonl',
    sampleUrl: '/samples/cot_example.jsonl',
    schemaNote:
      '每行一个 JSON:question 问题 / reasoning 推理过程 / answer 答案',
  },
  qa: {
    label: '问答对',
    format: 'jsonl',
    sampleName: 'qa_example.jsonl',
    sampleUrl: '/samples/qa_example.jsonl',
    schemaNote: '每行一个 JSON:question 问题 / answer 标准答案',
  },
  preference: {
    label: '偏好',
    format: 'jsonl',
    sampleName: 'preference_example.jsonl',
    sampleUrl: '/samples/preference_example.jsonl',
    schemaNote:
      '每行一个 JSON:prompt 提示 / chosen 优选回答 / rejected 次选回答',
  },
  timeseries: {
    label: '时间序列',
    format: 'csv',
    sampleName: 'timeseries_example.csv',
    sampleUrl: '/samples/timeseries_example.csv',
    schemaNote: 'CSV 表头 timestamp,sensor_id,value:时间戳 / 传感器ID / 数值',
  },
  gis: {
    label: '地理空间',
    format: 'json',
    sampleName: 'gis_example.json',
    sampleUrl: '/samples/gis_example.json',
    schemaNote:
      'GeoJSON FeatureCollection:每个 Point 含 [经度,纬度] 坐标与 name/category 属性',
  },
  multimodal: {
    label: '多模态',
    media: true,
    modalities: [
      {
        key: 'image',
        label: '图像',
        accept: '.png,.jpg,.jpeg,.gif,.bmp,.webp',
        exts: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'],
      },
      {
        key: 'audio',
        label: '音频',
        accept: '.mp3,.wav,.flac,.m4a,.aac,.ogg',
        exts: ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg'],
      },
      {
        key: 'video',
        label: '视频',
        accept: '.mp4,.avi,.mov,.mkv,.webm',
        exts: ['mp4', 'avi', 'mov', 'mkv', 'webm'],
      },
    ],
  },
};

type Props = {
  semanticType: string;
  /** 暂存文件变化时回调(供父页做本地预览,如时序图表);仅传 originFileObj。 */
  onFilesChange?: (files: File[]) => void;
};

const getExt = (filename: string): string => {
  const i = filename.lastIndexOf('.');
  return i >= 0 ? filename.slice(i + 1).toLowerCase() : '';
};

const ScenarioImportCard: React.FC<Props> = ({
  semanticType,
  onFilesChange,
}) => {
  const cfg = CONFIG[semanticType];
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [modality, setModality] = useState(
    cfg?.modalities?.[0]?.key ?? 'image',
  );

  if (!cfg) return null;

  const mod =
    cfg.modalities?.find((m) => m.key === modality) ?? cfg.modalities?.[0];
  const accept = cfg.media ? mod?.accept : `.${cfg.format}`;

  // 仅暂存:校验扩展名 + 单文件 200MB,提交时统一发送
  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    const ext = getExt(file.name);
    const ok = cfg.media ? !!mod?.exts.includes(ext) : ext === cfg.format;
    if (!ok) {
      message.error(
        cfg.media
          ? `「${file.name}」不是${mod?.label}文件,已忽略`
          : `「${file.name}」不是 .${cfg.format} 文件,已忽略`,
      );
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
      message.warning('请先选择文件');
      return;
    }
    const fd = new FormData();
    fileList.forEach((f) => {
      if (f.originFileObj) fd.append('files', f.originFileObj as File);
    });
    if (cfg.media) {
      fd.append('data_type', modality); // image / audio / video
    } else {
      fd.append('data_type', cfg.format as string);
      fd.append('semantic_type', semanticType);
    }

    setSubmitting(true);
    const hide = message.loading('正在上传并生成数据集…', 0);
    try {
      const res = cfg.media
        ? await uploadMediaDataset(fd)
        : await uploadBatchDataset(fd);
      hide();
      message.success(
        `已生成数据集「${res.data?.name ?? cfg.label}」,原件已存入内置 MinIO`,
      );
      setFileList([]);
      history.push('/datasets/list');
    } catch (e: any) {
      hide();
      message.error(e?.data?.message ?? e?.message ?? '上传失败,请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card
      style={{ marginBottom: 16 }}
      styles={{ body: { padding: 20 } }}
      title={
        <span>
          <CloudUploadOutlined style={{ color: '#1677ff', marginRight: 8 }} />
          文件导入 · {cfg.label}
        </span>
      }
      extra={<Tag color="blue">上传即生成数据集</Tag>}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 12,
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        {cfg.media ? (
          <>
            <Segmented
              value={modality}
              onChange={(v) => {
                setModality(v as string);
                setFileList([]);
              }}
              options={(cfg.modalities ?? []).map((m) => ({
                label: m.label,
                value: m.key,
              }))}
              data-testid="scenario-modality"
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              选择模态后上传,一批合并为一个 manifest 数据集(自动标记多模态)。
            </Text>
          </>
        ) : (
          <>
            <Text type="secondary" style={{ fontSize: 12 }}>
              按示例格式准备数据:{cfg.schemaNote}
            </Text>
            <a
              href={cfg.sampleUrl}
              download={cfg.sampleName}
              data-testid={`scenario-sample-${semanticType}`}
            >
              <DownloadOutlined /> 下载示例文件({cfg.sampleName})
            </a>
          </>
        )}
      </div>
      <Dragger
        multiple
        fileList={fileList}
        beforeUpload={beforeUpload}
        onChange={({ fileList: fl }) => {
          setFileList(fl);
          onFilesChange?.(
            fl.flatMap((f) => (f.originFileObj ? [f.originFileObj] : [])),
          );
        }}
        accept={accept}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">
          {cfg.media
            ? `点击或拖拽${mod?.label}文件到此处导入`
            : `点击或拖拽 .${cfg.format} 文件到此处导入`}
        </p>
        <p className="ant-upload-hint">
          {cfg.label}数据,单文件最大 200MB;多文件合并生成一个数据集,原件存入内置
          MinIO。
        </p>
      </Dragger>
      <Button
        type="primary"
        icon={<CloudUploadOutlined />}
        style={{ marginTop: 16 }}
        loading={submitting}
        disabled={fileList.length === 0}
        onClick={onSubmit}
        data-testid={`scenario-import-submit-${semanticType}`}
      >
        上传并生成数据集（{fileList.length}）
      </Button>
    </Card>
  );
};

export default ScenarioImportCard;
