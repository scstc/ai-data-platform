import {
  AudioOutlined,
  ClearOutlined,
  CloseOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  InboxOutlined,
  PlusCircleOutlined,
  ScissorOutlined,
  ThunderboltOutlined,
  TranslationOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Card, Checkbox, message, Switch, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import ScenarioImportCard from './ImportCard';

const { Text, Title } = Typography;

/** 多模态数据接入 —— 文件源与预处理(设计稿高保真脚手架)。
 *  右侧「实时解析预览」与解析统计为静态演示;真实解析/OCR/分割逻辑待后端接入逻辑确定后接通。
 *  交互(开关 / 分割策略 / 清洗项)为本地状态,暂不提交。 */

type FileStatus = 'extracting' | 'uploaded';
const MOCK_FILES: {
  name: string;
  size: string;
  type: string;
  status: FileStatus;
  icon: ReactNode;
  color: string;
}[] = [
  {
    name: 'product_demo_01.mp4',
    size: '124.5 MB',
    type: 'MP4 Video',
    status: 'extracting',
    icon: <VideoCameraOutlined />,
    color: '#1677ff',
  },
  {
    name: 'technical_specs_final.pdf',
    size: '2.1 MB',
    type: 'PDF Document',
    status: 'uploaded',
    icon: <FileTextOutlined />,
    color: '#8c8c8c',
  },
  {
    name: 'interview_audio_v1.mp3',
    size: '15.8 MB',
    type: 'MP3 Audio',
    status: 'uploaded',
    icon: <AudioOutlined />,
    color: '#13c2c2',
  },
];

/** 视频时间轴分段(静态演示;active 段高亮) */
const VIDEO_SEGS = [false, true, false, false, true, false, false].map(
  (active, i) => ({ id: `v${i}`, active }),
);

/** 音频波形条(静态演示;前段为已降噪高亮) */
const WAVE_BARS = [6, 12, 8, 18, 22, 14, 20, 10, 16, 24, 12, 8, 14, 6, 4].map(
  (h, i) => ({ id: `w${i}`, h, active: i < 7 }),
);

/** 章节小标题:图标 + 文案 */
const RuleTitle: React.FC<{ icon: ReactNode; children: string }> = ({
  icon,
  children,
}) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginBottom: 12,
      color: '#595959',
    }}
  >
    <span style={{ fontSize: 14 }}>{icon}</span>
    <Text strong style={{ fontSize: 13 }}>
      {children}
    </Text>
  </div>
);

const MultimodalIngestPage: React.FC = () => {
  const [smartSplit, setSmartSplit] = useState(true);
  const [splitStrategy, setSplitStrategy] = useState<'time' | 'scene'>('time');
  const [ocr, setOcr] = useState(true);
  const [cleansing, setCleansing] = useState<string[]>(['stopwords']);

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据', path: '/ingest/local-upload/scenario' },
        { title: '多模态数据接入' },
      ])}
      title="多模态数据接入 - 文件源与预处理"
      content="配置并优化来自不同媒介的数据导入规则。"
      onBack={() => history.push('/ingest/local-upload/scenario')}
      extra={[
        <Button
          key="cancel"
          onClick={() => history.push('/ingest/local-upload/scenario')}
        >
          取消
        </Button>,
        <Button
          key="save"
          type="primary"
          onClick={() => message.info('接入逻辑待定,确定后开放保存')}
        >
          保存并继续
        </Button>,
      ]}
    >
      <ScenarioImportCard semanticType="multimodal" />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.2fr) minmax(0, 1fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* 左列:文件源上传 + 预处理规则 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card
            title={
              <span>
                <CloudUploadOutlined style={{ color: '#1677ff' }} /> 文件源上传
                (File Source Upload)
              </span>
            }
            extra={
              <a onClick={() => message.info('批量上传待接入')}>
                <PlusCircleOutlined /> 批量上传
              </a>
            }
            styles={{ body: { padding: 16 } }}
          >
            <div
              style={{
                border: '1px dashed #d9d9d9',
                borderRadius: 8,
                padding: '32px 16px',
                textAlign: 'center',
                background: '#fafafa',
                cursor: 'pointer',
              }}
              onClick={() => message.info('浏览本地文件待接入')}
            >
              <InboxOutlined style={{ fontSize: 30, color: '#bfbfbf' }} />
              <div style={{ marginTop: 8 }}>
                拖放文件到此处,或{' '}
                <a onClick={(e) => e.stopPropagation()}>浏览本地文件</a>
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                支持 MP4, MP3, JPG, PDF, TXT(最大 500MB)
              </Text>
            </div>

            <div style={{ marginTop: 12, display: 'grid', gap: 10 }}>
              {MOCK_FILES.map((f) => (
                <div
                  key={f.name}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    border: '1px solid #f0f0f0',
                    borderRadius: 8,
                    padding: '10px 12px',
                  }}
                >
                  <span
                    style={{
                      fontSize: 18,
                      color: f.color,
                      width: 32,
                      height: 32,
                      borderRadius: 6,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      background: `${f.color}14`,
                    }}
                  >
                    {f.icon}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{f.name}</div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {f.size} · {f.type}
                    </Text>
                  </div>
                  {f.status === 'extracting' ? (
                    <span style={{ color: '#52c41a', fontSize: 12 }}>
                      ● Extracting
                    </span>
                  ) : (
                    <Tag color="blue">Uploaded</Tag>
                  )}
                  <DeleteOutlined
                    style={{ color: '#bfbfbf', cursor: 'pointer' }}
                    onClick={() => message.info('删除待接入')}
                  />
                </div>
              ))}
            </div>
          </Card>

          <Card
            title="预处理规则 (Preprocessing Rules)"
            styles={{ body: { padding: 16 } }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 16,
              }}
            >
              <div>
                <RuleTitle icon={<ScissorOutlined />}>视频/音频分割</RuleTitle>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <Text>启用智能分割</Text>
                  <Switch checked={smartSplit} onChange={setSmartSplit} />
                </div>
                <Text
                  type="secondary"
                  style={{
                    fontSize: 12,
                    display: 'block',
                    margin: '10px 0 6px',
                  }}
                >
                  分割策略
                </Text>
                <div style={{ display: 'flex', gap: 8 }}>
                  <Button
                    size="small"
                    type={splitStrategy === 'time' ? 'primary' : 'default'}
                    disabled={!smartSplit}
                    onClick={() => setSplitStrategy('time')}
                  >
                    按时间 (30s)
                  </Button>
                  <Button
                    size="small"
                    type={splitStrategy === 'scene' ? 'primary' : 'default'}
                    disabled={!smartSplit}
                    onClick={() => setSplitStrategy('scene')}
                  >
                    按场景变换
                  </Button>
                </div>
              </div>

              <div
                style={{
                  border: '1px solid #f0f0f0',
                  borderRadius: 8,
                  padding: 12,
                }}
              >
                <RuleTitle icon={<TranslationOutlined />}>
                  OCR 与文本提取
                </RuleTitle>
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <Text>多媒体 OCR 解析</Text>
                  <Switch checked={ocr} onChange={setOcr} />
                </div>
                <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                  <Tag icon={<TranslationOutlined />}>语言:中/英</Tag>
                  <Tag icon={<ThunderboltOutlined />}>高精度模式</Tag>
                </div>
              </div>
            </div>

            <div style={{ marginTop: 16 }}>
              <RuleTitle icon={<ClearOutlined />}>
                数据清洗 (DATA CLEANSING)
              </RuleTitle>
              <Checkbox.Group
                value={cleansing}
                onChange={(v) => setCleansing(v as string[])}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr 1fr',
                  gap: 12,
                }}
              >
                {[
                  { v: 'denoise', t: '音频降噪', d: '去除环境背景杂音' },
                  { v: 'blur', t: '模糊过滤', d: '自动识别并剔除模糊图片' },
                  {
                    v: 'stopwords',
                    t: '停用词处理',
                    d: '清理 NLP 识别冗余文本',
                  },
                ].map((c) => (
                  <div
                    key={c.v}
                    style={{
                      border: '1px solid #f0f0f0',
                      borderRadius: 8,
                      padding: '10px 12px',
                    }}
                  >
                    <Checkbox value={c.v}>
                      <Text strong>{c.t}</Text>
                    </Checkbox>
                    <div
                      style={{
                        fontSize: 12,
                        color: '#8c8c8c',
                        marginTop: 4,
                        marginLeft: 24,
                      }}
                    >
                      {c.d}
                    </div>
                  </div>
                ))}
              </Checkbox.Group>
            </div>
          </Card>
        </div>

        {/* 右列:实时解析预览(静态演示) */}
        <Card
          styles={{ body: { padding: 16 } }}
          title={
            <span style={{ color: '#1677ff' }}>
              <EyeOutlined /> 实时解析预览
            </span>
          }
          extra={
            <CloseOutlined
              style={{ color: '#bfbfbf', cursor: 'pointer' }}
              onClick={() => history.push('/ingest/local-upload/scenario')}
            />
          }
        >
          {/* 视频预览 */}
          <div
            style={{
              position: 'relative',
              borderRadius: 8,
              height: 180,
              background:
                'linear-gradient(135deg, #1f2733 0%, #2b3a2e 60%, #3a3326 100%)',
            }}
          >
            <span
              style={{
                position: 'absolute',
                left: 10,
                bottom: 8,
                color: '#fff',
                fontSize: 12,
                fontFamily: 'monospace',
              }}
            >
              00:14 / 03:45
            </span>
            <span
              style={{
                position: 'absolute',
                right: 10,
                bottom: 8,
                color: '#fff',
                fontSize: 12,
              }}
            >
              Scene Change Detected
            </span>
          </div>

          {/* 同步解析时间轴 */}
          <div style={{ marginTop: 14 }}>
            <Text strong style={{ fontSize: 12 }}>
              同步解析时间轴 (Timeline Sync)
            </Text>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                margin: '10px 0 4px',
              }}
            >
              <Text type="secondary" style={{ fontSize: 11 }}>
                <VideoCameraOutlined /> VIDEO
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                60 FPS
              </Text>
            </div>
            <div style={{ position: 'relative', display: 'flex', gap: 3 }}>
              {VIDEO_SEGS.map((s) => (
                <div
                  key={s.id}
                  style={{
                    flex: 1,
                    height: 26,
                    borderRadius: 3,
                    background: s.active ? '#bcd0f7' : '#eef2fb',
                  }}
                />
              ))}
              <div
                style={{
                  position: 'absolute',
                  left: '26%',
                  top: -2,
                  bottom: -2,
                  width: 2,
                  background: '#ff4d4f',
                }}
              />
            </div>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                margin: '12px 0 4px',
              }}
            >
              <Text type="secondary" style={{ fontSize: 11 }}>
                <AudioOutlined /> AUDIO
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                Noise Reduced
              </Text>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-end',
                gap: 2,
                height: 28,
                background: '#f5f7fa',
                borderRadius: 4,
                padding: '0 6px',
              }}
            >
              {WAVE_BARS.map((w) => (
                <div
                  key={w.id}
                  style={{
                    width: 4,
                    height: w.h,
                    borderRadius: 2,
                    background: w.active ? '#1677ff' : '#c7d4e8',
                  }}
                />
              ))}
            </div>
          </div>

          {/* 文本转写 */}
          <div style={{ marginTop: 14 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginBottom: 6,
              }}
            >
              <Text strong style={{ fontSize: 12 }}>
                TEXT TRANSCRIPT
              </Text>
              <Text style={{ fontSize: 11, color: '#1677ff' }}>
                Live Parsing…
              </Text>
            </div>
            <div
              style={{
                background: '#f5f7fa',
                borderRadius: 6,
                padding: 12,
                fontSize: 12,
                color: '#595959',
                lineHeight: 1.7,
              }}
            >
              <div>
                [00:12] "Next-generation data infrastructure requires seamless{' '}
                <Text mark>multimodal</Text> integration."
              </div>
              <div style={{ color: '#bfbfbf' }}>
                [00:14] "Detecting key frame features for vector embedding…"
              </div>
            </div>
          </div>

          {/* 解析效率统计 */}
          <div
            style={{
              marginTop: 14,
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 12,
              background: '#fafafa',
              borderRadius: 8,
              padding: 12,
            }}
          >
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                文本置信度
              </Text>
              <Title level={4} style={{ margin: '4px 0 0', color: '#1677ff' }}>
                98.4%
              </Title>
            </div>
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                处理延迟
              </Text>
              <Title level={4} style={{ margin: '4px 0 0', color: '#1677ff' }}>
                1.2s
              </Title>
            </div>
          </div>

          <Button
            block
            icon={<DownloadOutlined />}
            style={{ marginTop: 14 }}
            onClick={() => message.info('导出解析日志待接入')}
          >
            导出解析日志 (.json)
          </Button>
        </Card>
      </div>
    </PageContainer>
  );
};

export default MultimodalIngestPage;
