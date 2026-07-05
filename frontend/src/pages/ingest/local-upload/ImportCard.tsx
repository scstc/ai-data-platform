import {
  CloudUploadOutlined,
  DownloadOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import { history, useAccess } from '@umijs/max';
import type { UploadFile, UploadProps } from 'antd';
import {
  Button,
  Card,
  message,
  Segmented,
  Select,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { useCallback, useState } from 'react';
import { listDataLakes, localUploadToLake } from '@/services/data-platform';

const { Text } = Typography;
const { Dragger } = Upload;

/** 场景数据「文件导入」卡片。
 *  按场景类型提供示例文件(public/samples/) + 格式说明,用户准备好文件后
 *  归档到数据湖,后续从湖抽取生成数据集。
 *  - 文本/结构化类(cot/qa/preference/timeseries/gis):jsonl/csv/geojson
 *  - 多模态(multimodal):图像/音频/视频媒体文件
 *  后端按扩展名分派 parquet/raw 归档,不再传 semantic_type(语义在抽取时决定)。 */

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
    // GeoJSON 是 OGC 标准格式(.geojson),落地比 .json 更不被误识别为通用 JSON;
    // 同时仍接受 .json(便于把 FeatureCollection 内容重命名上传)。
    format: 'json,geojson',
    sampleName: 'gis_example.geojson',
    sampleUrl: '/samples/gis_example.geojson',
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
  const access = useAccess();
  const canUpload = access.hasPerm('ingest:upload:upload');
  const cfg = CONFIG[semanticType];
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [lakeId, setLakeId] = useState<string>();
  const [lakeOptions, setLakeOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [lakeLoading, setLakeLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [modality, setModality] = useState(
    cfg?.modalities?.[0]?.key ?? 'image',
  );

  // 目标数据湖下拉:按关键词拉取已有数据湖(本地上传入湖归档,后续从湖抽成数据集)
  const loadLakeOptions = useCallback((keyword?: string) => {
    setLakeLoading(true);
    listDataLakes({ name: keyword || undefined, pageSize: 50 })
      .then((res) =>
        setLakeOptions(
          (res.data ?? []).map((d) => ({ label: d.name, value: d.id })),
        ),
      )
      .catch(() => {
        /* 拉取失败不阻断,留空列表 */
      })
      .finally(() => setLakeLoading(false));
  }, []);

  if (!cfg) return null;

  const mod =
    cfg.modalities?.find((m) => m.key === modality) ?? cfg.modalities?.[0];
  // format 可能是 CSV 列表(如 GIS 的 "json,geojson"),让 accept 与校验同步多值
  const formatList = (cfg.format ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const accept = cfg.media
    ? mod?.accept
    : formatList.map((f) => `.${f}`).join(',');

  // 仅暂存:校验扩展名 + 单文件 200MB,提交时统一发送
  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    const ext = getExt(file.name);
    const ok = cfg.media ? !!mod?.exts.includes(ext) : formatList.includes(ext);
    if (!ok) {
      message.error(
        cfg.media
          ? `「${file.name}」不是${mod?.label}文件,已忽略`
          : `「${file.name}」不是 ${formatList.map((f) => `.${f}`).join(' / ')} 文件,已忽略`,
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
    if (!lakeId) {
      message.warning('请选择目标数据湖');
      return;
    }
    const fd = new FormData();
    fileList.forEach((f) => {
      if (f.originFileObj) fd.append('files', f.originFileObj as File);
    });

    setSubmitting(true);
    const hide = message.loading('正在归档到数据湖…', 0);
    try {
      const res = await localUploadToLake(lakeId, fd, {
        skipErrorHandler: true,
      });
      hide();
      message.success(
        `已归档 ${res.data?.snapshots?.length ?? 0} 个${cfg.label}快照,可到数据湖抽取生成数据集`,
      );
      setFileList([]);
      history.push(`/data-lakes/${lakeId}`);
    } catch (e: any) {
      hide();
      const body = e?.response?.data ?? e?.data;
      message.error(body?.message ?? e?.message ?? '归档失败,请重试');
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
      extra={<Tag color="blue">归档到数据湖</Tag>}
    >
      <div style={{ marginBottom: 12 }}>
        <Text strong>
          目标数据湖 <Text type="danger">*</Text>
        </Text>
        <Select
          style={{ width: '100%', marginTop: 8 }}
          placeholder="选择已有数据湖(文件归档后可抽取生成数据集)"
          value={lakeId}
          onChange={setLakeId}
          showSearch
          allowClear
          filterOption={false}
          onSearch={(kw) => loadLakeOptions(kw)}
          onFocus={() => loadLakeOptions()}
          options={lakeOptions}
          notFoundContent={
            lakeLoading ? '加载中…' : '无匹配数据湖,请先到「数据湖」页新建'
          }
        />
      </div>
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
              选择模态后上传,一文件一快照归档到数据湖(后续可抽取生成数据集)。
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
            : `点击或拖拽 ${formatList.map((f) => `.${f}`).join(' / ')} 文件到此处导入`}
        </p>
        <p className="ant-upload-hint">
          {cfg.label}数据,单文件最大
          200MB;归档到数据湖后可到数据湖详情页抽取生成数据集。
        </p>
      </Dragger>
      {canUpload && (
        <Button
          type="primary"
          icon={<CloudUploadOutlined />}
          style={{ marginTop: 16 }}
          loading={submitting}
          disabled={fileList.length === 0 || !lakeId}
          onClick={onSubmit}
          data-testid={`scenario-import-submit-${semanticType}`}
        >
          上传并归档到数据湖（{fileList.length}）
        </Button>
      )}
    </Card>
  );
};

export default ScenarioImportCard;
