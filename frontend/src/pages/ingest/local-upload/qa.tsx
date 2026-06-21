import {
  EditOutlined,
  ExportOutlined,
  FilterOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Button,
  Card,
  Input,
  message,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useState } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import ScenarioImportCard from './ImportCard';

const { Text } = Typography;

/** 问答对数据配置(设计稿高保真脚手架)。
 *  对齐校验规则、答案质量校验、样本数据均为本地状态 / 静态演示;
 *  真实校验与保存逻辑待后端接入逻辑确定后接通,各操作暂以提示占位。 */

/** 状态 -> Tag 颜色 */
const STATUS_COLOR: Record<string, string> = {
  已验证: 'green',
  待复核: 'blue',
  被驳回: 'red',
};

type Sample = {
  id: string;
  prompt: string;
  answer: string;
  status: string;
  score: string;
};

const SAMPLES: Sample[] = [
  {
    id: '#081',
    prompt: '如何解释量子纠缠给五岁的孩子听?',
    answer: '想象你有两只神奇的袜子,无论它们离多远...',
    status: '已验证',
    score: '0.94',
  },
  {
    id: '#082',
    prompt: '编写一个Python函数来计算斐波那契数列。',
    answer: 'def fib(n): a, b = 0, 1; while a < n: yield a...',
    status: '待复核',
    score: '0.87',
  },
  {
    id: '#083',
    prompt: '推荐三本关于存在主义哲学的入门书籍。',
    answer: '1. 《西绪福斯神话》阿尔贝·加缪; 2. 《存在与虚无》...',
    status: '被驳回',
    score: '0.42',
  },
  {
    id: '#084',
    prompt: '分析2024全球半导体供应链的趋势。',
    answer: '在2024年,我们预计供应链将呈现出去中心化...',
    status: '已验证',
    score: '0.91',
  },
];

/** 答案质量校验规则项 */
const QUALITY_RULES: { key: string; title: string; on: boolean }[] = [
  { key: 'noCompetitor', title: '禁止包含竞争对手名称', on: true },
  { key: 'disclaimer', title: '必须包含免责声明', on: false },
  { key: 'markdown', title: 'Markdown 格式规范检查', on: true },
];

const QaIngestPage: React.FC = () => {
  const [similarity, setSimilarity] = useState(0.88);
  const [knowledgeBase, setKnowledgeBase] = useState('企业百科全书 v4.0');
  const [strictFact, setStrictFact] = useState(true);
  const [minLen, setMinLen] = useState('100');
  const [maxLen, setMaxLen] = useState('500');
  const [quality, setQuality] = useState<Record<string, boolean>>({
    noCompetitor: true,
    disclaimer: false,
    markdown: true,
  });

  const toggleQuality = (key: string) =>
    setQuality((p) => ({ ...p, [key]: !p[key] }));

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      render: (v: string) => <span style={{ color: '#8c8c8c' }}>{v}</span>,
    },
    {
      title: '提示词 (Prompt)',
      dataIndex: 'prompt',
      key: 'prompt',
    },
    {
      title: '标准回答 (Answer)',
      dataIndex: 'answer',
      key: 'answer',
      ellipsis: true,
      render: (v: string) => <span style={{ color: '#1677ff' }}>{v}</span>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v: string) => <Tag color={STATUS_COLOR[v]}>{v}</Tag>,
    },
    {
      title: '评分',
      dataIndex: 'score',
      key: 'score',
      render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span>,
    },
    {
      title: '操作',
      key: 'action',
      render: () => (
        <a onClick={() => message.info('编辑样本待接入')}>
          <EditOutlined />
        </a>
      ),
    },
  ];

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据', path: '/ingest/local-upload/scenario' },
        { title: '问答对数据配置' },
      ])}
      title="问答对数据配置"
      content="管理知识库问答对的对齐验证、自动校验规则及样本数据质量。"
      onBack={() => history.push('/ingest/local-upload/scenario')}
      extra={[
        <Button
          key="export"
          icon={<ExportOutlined />}
          data-testid="qa-export"
          onClick={() => message.info('导出待接入')}
        >
          导出
        </Button>,
        <Button
          key="save"
          type="primary"
          icon={<SaveOutlined />}
          data-testid="qa-save"
          onClick={() => message.info('接入逻辑待定,确定后开放保存配置')}
        >
          保存配置
        </Button>,
      ]}
    >
      <ScenarioImportCard semanticType="qa" />
      {/* ROW 1:对齐校验规则 + 答案质量校验规则 */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* 左:问答对齐与校验规则 */}
        <Card
          title={
            <span>
              <SwapOutlined style={{ color: '#1677ff' }} /> 问答对齐与校验规则
            </span>
          }
          styles={{ body: { padding: 24 } }}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 16,
            }}
          >
            {/* 语义相似度阈值 */}
            <div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <Text strong style={{ fontSize: 13 }}>
                  语义相似度阈值
                </Text>
                <Text strong style={{ color: '#1677ff', fontSize: 14 }}>
                  {similarity.toFixed(2)}
                </Text>
              </div>
              <Slider
                min={0}
                max={1}
                step={0.01}
                value={similarity}
                onChange={(v) => setSimilarity(v as number)}
                data-testid="qa-similarity"
                style={{ marginTop: 8 }}
              />
            </div>

            {/* 关联知识库 */}
            <div>
              <Text
                strong
                style={{ fontSize: 13, display: 'block', marginBottom: 8 }}
              >
                关联知识库
              </Text>
              <Select
                value={knowledgeBase}
                onChange={setKnowledgeBase}
                data-testid="qa-knowledge-base"
                style={{ width: '100%' }}
                options={[
                  { label: '企业百科全书 v4.0', value: '企业百科全书 v4.0' },
                  { label: '产品手册库 v2.1', value: '产品手册库 v2.1' },
                  { label: '法务合规库', value: '法务合规库' },
                ]}
              />
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 16,
              marginTop: 16,
            }}
          >
            {/* 严格事实校验 */}
            <div
              style={{
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                padding: 12,
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <Text strong style={{ fontSize: 13 }}>
                  严格事实校验
                </Text>
                <Switch
                  checked={strictFact}
                  onChange={setStrictFact}
                  data-testid="qa-strict-fact"
                />
              </div>
              <div style={{ fontSize: 12, color: '#8c8c8c', marginTop: 4 }}>
                对比知识库原文进行事实核查
              </div>
            </div>

            {/* 回答长度限制 */}
            <div>
              <Text
                strong
                style={{ fontSize: 13, display: 'block', marginBottom: 8 }}
              >
                回答长度限制
              </Text>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  value={minLen}
                  onChange={(e) => setMinLen(e.target.value)}
                  data-testid="qa-min-len"
                  style={{ textAlign: 'center' }}
                />
                <Input
                  value={maxLen}
                  onChange={(e) => setMaxLen(e.target.value)}
                  data-testid="qa-max-len"
                  style={{ textAlign: 'center' }}
                />
              </Space.Compact>
            </div>
          </div>
        </Card>

        {/* 右:答案质量校验规则 */}
        <Card
          title={
            <span>
              <SafetyCertificateOutlined style={{ color: '#1677ff' }} />{' '}
              答案质量校验规则
            </span>
          }
          styles={{ body: { padding: 24 } }}
        >
          {QUALITY_RULES.map((r) => (
            <div
              key={r.key}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px 0',
              }}
            >
              <Text style={{ fontSize: 13 }}>{r.title}</Text>
              <Switch
                checked={quality[r.key]}
                onChange={() => toggleQuality(r.key)}
                data-testid={`qa-quality-${r.key}`}
              />
            </div>
          ))}
          <Button
            type="dashed"
            block
            data-testid="qa-add-rule"
            onClick={() => message.info('添加自定义校验逻辑待接入')}
            style={{ marginTop: 12 }}
          >
            + 添加自定义校验逻辑
          </Button>
        </Card>
      </div>

      {/* ROW 2:样本数据编辑 */}
      <Card
        title={
          <span>
            <EditOutlined style={{ color: '#1677ff' }} /> 样本数据编辑
          </span>
        }
        extra={
          <Space>
            <Button
              icon={<FilterOutlined />}
              data-testid="qa-filter"
              onClick={() => message.info('筛选待接入')}
            >
              筛选
            </Button>
            <Button
              type="primary"
              data-testid="qa-add-sample"
              onClick={() => message.info('新增样本待接入')}
            >
              + 新增样本
            </Button>
          </Space>
        }
        style={{ marginTop: 16 }}
        styles={{ body: { padding: 0 } }}
      >
        <Table<Sample>
          rowKey="id"
          columns={columns}
          dataSource={SAMPLES}
          pagination={{
            pageSize: 4,
            total: 245,
            current: 1,
            showSizeChanger: false,
            showQuickJumper: false,
            showTotal: (total, range) =>
              `显示 ${range[0]}-${range[1]} 条,共 ${total} 样本`,
          }}
        />
      </Card>
    </PageContainer>
  );
};

export default QaIngestPage;
