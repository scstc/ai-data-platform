import { ExperimentOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useParams } from '@umijs/max';
import {
  Alert,
  Card,
  Col,
  Divider,
  Row,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useState } from 'react';
import { getOperatorDetail } from '@/services/data-platform';

const { Title, Paragraph, Text } = Typography;

const RUNNABLE_TAG: Record<string, { label: string; color: string }> = {
  ready: { label: '可直接执行', color: 'green' },
  needs_api: { label: '需要 AI', color: 'geekblue' },
  needs_media: { label: '需要媒体', color: 'orange' },
  needs_compute: { label: '需要算力', color: 'volcano' },
};

const RESOURCE_LABEL: Record<string, string> = {
  cpu: 'CPU',
  api_llm: 'LLM API',
  hf_model: 'HF 模型',
  gpu: 'GPU',
  vllm: 'vLLM',
};

const MODALITY_LABEL: Record<string, string> = {
  text: '文本',
  image: '图像',
  video: '视频',
  audio: '音频',
  multimodal: '多模态',
};

/** 参数类型清洗:`<class 'bool'>` → bool;带模块路径取末段。 */
const cleanType = (t?: string): string => {
  if (!t) return '';
  const m = t.match(/<class '(.+?)'>/);
  const raw = m ? m[1] : t;
  return raw.split('.').pop() ?? raw;
};

const PARAM_COLUMNS = [
  { title: '参数', dataIndex: 'name', width: 180 },
  {
    title: '类型',
    dataIndex: 'type',
    width: 120,
    render: (t: string) => <Text code>{cleanType(t)}</Text>,
  },
  { title: '默认值', dataIndex: 'default', width: 120, ellipsis: true },
  { title: '说明', dataIndex: 'desc', ellipsis: true },
];

/** 头部一行带标签的 chips:`label: [tag][tag]` */
const ChipRow: React.FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <Space size={6} align="center">
    <Text type="secondary" style={{ fontSize: 13 }}>
      {label}:
    </Text>
    {children}
  </Space>
);

const OperatorDetail: React.FC = () => {
  const { name } = useParams<{ name: string }>();
  const [operator, setOperator] = useState<DataPlatform.CatalogOperator>();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    getOperatorDetail(name)
      .then((res) => setOperator(res.data))
      .finally(() => setLoading(false));
  }, [name]);

  if (!name) {
    return <div>算子名称缺失</div>;
  }

  const runTag = operator ? RUNNABLE_TAG[operator.runnable] : null;
  const demos = operator?.effectDemo ?? [];

  return (
    <PageContainer
      loading={loading}
      title="算子详情页"
      onBack={() => history.back()}
    >
      {operator && (
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          {/* 身份信息头 */}
          <Card>
            <Space align="start" size="middle">
              <div
                style={{
                  width: 44,
                  height: 44,
                  borderRadius: 10,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background:
                    'linear-gradient(135deg, #6a5cff 0%, #4a8bff 100%)',
                  color: '#fff',
                  fontSize: 22,
                  flex: 'none',
                }}
              >
                <ExperimentOutlined />
              </div>
              <Space orientation="vertical" size={8}>
                <Space align="baseline" size="middle" wrap>
                  <Title level={4} style={{ margin: 0 }}>
                    {operator.zhLabel}
                  </Title>
                  <Text
                    type="secondary"
                    style={{ fontFamily: 'monospace', fontSize: 12 }}
                    copyable={{ text: operator.name }}
                  >
                    算子 ID: {operator.name}
                  </Text>
                </Space>
                <Space size="large" wrap>
                  <ChipRow label="使用方式">
                    <Tag>{operator.usageMode ?? '离线'}</Tag>
                  </ChipRow>
                  {operator.modality && operator.modality.length > 0 && (
                    <ChipRow label="应用场景">
                      {operator.modality.map((m) => (
                        <Tag key={m}>{MODALITY_LABEL[m] ?? m}</Tag>
                      ))}
                    </ChipRow>
                  )}
                  {operator.tags && operator.tags.length > 0 && (
                    <ChipRow label="标签">
                      {operator.tags.map((t) => (
                        <Tag key={t} color="blue">
                          {t}
                        </Tag>
                      ))}
                    </ChipRow>
                  )}
                  <ChipRow label="运行状态">
                    {runTag && <Tag color={runTag.color}>{runTag.label}</Tag>}
                    <Tag>
                      {RESOURCE_LABEL[operator.resourceClass] ??
                        operator.resourceClass}
                    </Tag>
                  </ChipRow>
                </Space>
              </Space>
            </Space>
          </Card>

          <Row gutter={24}>
            {/* 左:描述 + 参数 */}
            <Col xs={24} lg={demos.length ? 16 : 24}>
              <Space
                orientation="vertical"
                size="large"
                style={{ width: '100%' }}
              >
                <Card title="算子描述">
                  {operator.zhUsageTip && (
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 16 }}
                      title="何时使用"
                      description={operator.zhUsageTip}
                    />
                  )}
                  <Paragraph
                    style={{ whiteSpace: 'pre-line', marginBottom: 0 }}
                  >
                    {operator.descZh || operator.summaryZh || '暂无描述'}
                  </Paragraph>
                  {operator.descEn && (
                    <Paragraph
                      type="secondary"
                      style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}
                    >
                      {operator.descEn}
                    </Paragraph>
                  )}
                </Card>

                <Card title="算子参数">
                  {operator.params?.length ? (
                    <Table
                      size="small"
                      rowKey="name"
                      pagination={false}
                      dataSource={operator.params}
                      columns={PARAM_COLUMNS}
                    />
                  ) : (
                    <Text type="secondary">无参数</Text>
                  )}
                </Card>
              </Space>
            </Col>

            {/* 右:效果展示(仅有样例时) */}
            {demos.length > 0 && (
              <Col xs={24} lg={8}>
                <Card title="效果展示">
                  <Space
                    orientation="vertical"
                    size="middle"
                    style={{ width: '100%' }}
                  >
                    {demos.map((d, i) => (
                      <div key={d.before}>
                        <EffectBlock label="处理前" text={d.before} />
                        <EffectBlock label="处理后" text={d.after} highlight />
                        {i < demos.length - 1 && (
                          <Divider style={{ margin: '12px 0 0' }} />
                        )}
                      </div>
                    ))}
                  </Space>
                </Card>
              </Col>
            )}
          </Row>
        </Space>
      )}
    </PageContainer>
  );
};

/** 效果展示单块:灰底标签 + 内容框 */
const EffectBlock: React.FC<{
  label: string;
  text: string;
  highlight?: boolean;
}> = ({ label, text, highlight }) => (
  <div style={{ marginTop: 4 }}>
    <div
      style={{
        background: 'var(--ant-color-fill-tertiary, #f0f0f0)',
        padding: '4px 10px',
        borderRadius: 6,
        fontSize: 12,
        color: 'var(--ant-color-text-secondary, #888)',
        marginBottom: 6,
      }}
    >
      {label}
    </div>
    <div
      style={{
        border: '1px solid var(--ant-color-border-secondary, #f0f0f0)',
        borderRadius: 6,
        padding: '8px 12px',
        fontSize: 13,
        whiteSpace: 'pre-wrap',
        background: highlight
          ? 'var(--ant-color-success-bg, #f6ffed)'
          : undefined,
      }}
    >
      {text}
    </div>
  </div>
);

export default OperatorDetail;
