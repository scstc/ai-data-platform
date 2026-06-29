import { PageContainer } from '@ant-design/pro-components';
import { history, useModel, useParams } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Space,
  Spin,
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

const PARAM_COLUMNS = [
  { title: '参数', dataIndex: 'name', width: 160 },
  { title: '类型', dataIndex: 'type', width: 150, ellipsis: true },
  { title: '默认值', dataIndex: 'default', width: 120, ellipsis: true },
  { title: '说明', dataIndex: 'desc', ellipsis: true },
];

const OperatorDetail: React.FC = () => {
  const { name } = useParams<{ name: string }>();
  const { add } = useModel('opCart');
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

  const onAdd = () => {
    if (operator) {
      add(operator.name);
      history.push('/processing/market');
    }
  };

  const tag = operator ? RUNNABLE_TAG[operator.runnable] : null;

  return (
    <PageContainer
      loading={loading}
      title={operator?.zhLabel}
      subTitle={operator?.name}
      extra={
        <Button type="primary" onClick={onAdd}>
          加入加工任务
        </Button>
      }
      onBack={() => history.back()}
    >
      {operator && (
        <Row gutter={24}>
          {/* 左侧主内容 */}
          <Col xs={24} lg={18}>
            <Space
              orientation="vertical"
              size="large"
              style={{ width: '100%' }}
            >
              {/* 基本信息 */}
              <Card title="基本信息">
                {operator.zhUsageTip && (
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 16 }}
                    message="何时使用"
                    description={operator.zhUsageTip}
                  />
                )}
                <Paragraph>{operator.descZh || operator.summaryZh}</Paragraph>
                {operator.descEn && (
                  <Paragraph type="secondary" style={{ fontSize: 12 }}>
                    {operator.descEn}
                  </Paragraph>
                )}
              </Card>

              {/* 参数配置 */}
              <Card title="参数配置">
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

          {/* 右侧侧边栏 */}
          <Col xs={24} lg={6}>
            <Space
              orientation="vertical"
              size="middle"
              style={{ width: '100%' }}
            >
              {/* 运行状态 */}
              <Card size="small" title="运行状态">
                <Space orientation="vertical" style={{ width: '100%' }}>
                  {tag && <Tag color={tag.color}>{tag.label}</Tag>}
                  <Descriptions column={1} size="small">
                    <Descriptions.Item label="资源类型">
                      {RESOURCE_LABEL[operator.resourceClass] ||
                        operator.resourceClass}
                    </Descriptions.Item>
                    {operator.modality && operator.modality.length > 0 && (
                      <Descriptions.Item label="模态">
                        {operator.modality
                          .map((m) => MODALITY_LABEL[m] || m)
                          .join('、')}
                      </Descriptions.Item>
                    )}
                    {operator.scenarioGroup && (
                      <Descriptions.Item label="场景">
                        {operator.scenarioGroup}
                      </Descriptions.Item>
                    )}
                  </Descriptions>
                </Space>
              </Card>

              {/* 用法示例 */}
              {operator.example && (
                <Card size="small" title="用法示例">
                  <pre
                    style={{
                      background: 'var(--ant-color-fill-quaternary, #f5f5f5)',
                      padding: 12,
                      borderRadius: 6,
                      overflow: 'auto',
                      fontSize: 12,
                      margin: 0,
                    }}
                  >
                    {operator.example}
                  </pre>
                </Card>
              )}

              {/* 环境要求提示 */}
              {operator.runnable !== 'ready' && (
                <Alert
                  type="warning"
                  showIcon
                  message={
                    operator.runnable === 'needs_api'
                      ? '需要配置 LLM API'
                      : operator.runnable === 'needs_compute'
                        ? '需要 GPU/算力环境'
                        : '需要多模态数据集'
                  }
                />
              )}
            </Space>
          </Col>
        </Row>
      )}
    </PageContainer>
  );
};

export default OperatorDetail;
