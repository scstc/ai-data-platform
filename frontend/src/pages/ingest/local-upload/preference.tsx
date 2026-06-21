import {
  BarChartOutlined,
  CheckOutlined,
  CloseOutlined,
  EditOutlined,
  RightOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
  TeamOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Button,
  Card,
  Checkbox,
  Input,
  message,
  Progress,
  Rate,
  Slider,
  Tag,
  Typography,
} from 'antd';
import { useState } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import ScenarioImportCard from './ImportCard';

const { Text } = Typography;

/** 偏好数据配置 —— RLHF 奖励模型参数与标注工作流(设计稿高保真脚手架)。
 *  左侧评分标准 / HITL 抽样为本地状态,右侧「偏好排序编辑器」样本为静态演示;
 *  真实标注生成 / 提交逻辑待后端接入逻辑确定后接通,各操作暂以提示占位。 */

/** 奖励模型评分标准(权重为静态展示文案,滑块值为本地状态) */
const METRICS: { key: string; name: string; weight: string; desc: string }[] = [
  {
    key: 'helpfulness',
    name: 'Helpfulness (有用性)',
    weight: '0.50',
    desc: '模型回答是否直接解决了用户的问题,且信息完整准确。',
  },
  {
    key: 'honesty',
    name: 'Honesty (诚实性)',
    weight: '0.35',
    desc: '模型是否包含虚假事实或误导性信息,是否承认其局限性。',
  },
  {
    key: 'harmlessness',
    name: 'Harmlessness (无害性)',
    weight: '0.15',
    desc: '模型输出是否包含有害内容、歧视、偏见或非法建议。',
  },
];

/** 人工在环抽样(HITL)行(静态演示) */
const HITL_ROWS: { key: string; title: string; subtitle: string }[] = [
  { key: 'expert', title: '专家抽样工作流', subtitle: '当前抽样率: 5%' },
  { key: 'conflict', title: '异常值冲突审核', subtitle: '等待处理: 124 组' },
  { key: 'consistency', title: '一致性验证记录', subtitle: 'Kappa 值: 0.72' },
];

/** 输出样本 A 代码(静态演示) */
const SAMPLE_A_CODE = `import time
import functools

def timer_logger(filename):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            end = time.time()
            duration = end - start
            with open(filename, 'a') as f:
                f.write(f"{func.__name__}: {duration}s")
            return result
        return wrapper
    return decorator`;

/** 输出样本 B 代码(静态演示) */
const SAMPLE_B_CODE = `def time_it(func):
    def wrapper(*args, **kwargs):
        t1 = time.time()
        res = func(*args, **kwargs)
        t2 = time.time()
        print(f"Time: {t2-t1}")
        return res
    return wrapper

# 没写文件写入功能`;

const codeBlockStyle: React.CSSProperties = {
  background: '#f5f7fa',
  borderRadius: 8,
  padding: 12,
  fontSize: 12,
  fontFamily: 'monospace',
  overflow: 'auto',
  maxHeight: 280,
  whiteSpace: 'pre',
  margin: 0,
};

const PreferenceIngestPage: React.FC = () => {
  const [weights, setWeights] = useState<Record<string, number>>({
    helpfulness: 0.5,
    honesty: 0.35,
    harmlessness: 0.15,
  });
  const [autoReflow, setAutoReflow] = useState(true);
  const [chosenScore, setChosenScore] = useState('5');
  const [rejectedScore, setRejectedScore] = useState('1');

  const info = () => message.info('接入逻辑待定,确定后开放');

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据', path: '/ingest/local-upload/scenario' },
        { title: '偏好数据配置' },
      ])}
      title="偏好数据配置"
      content="配置人类反馈强化学习 (RLHF) 的奖励模型参数与标注工作流。"
      onBack={() => history.push('/ingest/local-upload/scenario')}
      extra={[
        <div
          key="progress"
          style={{
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '8px 16px',
            background: '#fff',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 4,
            }}
          >
            <Text type="secondary" style={{ fontSize: 12 }}>
              标注进度
            </Text>
            <Text strong>84%</Text>
          </div>
          <Progress percent={84} showInfo={false} size="small" />
        </div>,
        <div
          key="quality"
          style={{
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: '8px 16px',
            background: '#fff',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 4,
            }}
          >
            <Text type="secondary" style={{ fontSize: 12 }}>
              数据集质量
            </Text>
            <Text strong style={{ color: '#52c41a' }}>
              优良
            </Text>
          </div>
          <Rate disabled value={4} allowHalf={false} size="small" />
        </div>,
      ]}
    >
      <ScenarioImportCard semanticType="preference" />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 0.8fr) minmax(0, 1.4fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* 左列:奖励模型评分标准 + 人工在环抽样 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card
            title={
              <span>
                <BarChartOutlined style={{ color: '#1677ff' }} />{' '}
                奖励模型评分标准
              </span>
            }
            styles={{ body: { padding: 24 } }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
              {METRICS.map((m) => (
                <div key={m.key}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                  >
                    <Text strong style={{ fontSize: 13 }}>
                      {m.name}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      权重: {m.weight}
                    </Text>
                  </div>
                  <Slider
                    min={0}
                    max={1}
                    step={0.01}
                    value={weights[m.key]}
                    onChange={(v) =>
                      setWeights((p) => ({ ...p, [m.key]: v as number }))
                    }
                    data-testid={`preference-weight-${m.key}`}
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {m.desc}
                  </Text>
                </div>
              ))}
            </div>
            <Button
              block
              type="primary"
              style={{ marginTop: 20 }}
              data-testid="preference-update-standard"
              onClick={info}
            >
              更新标准配置
            </Button>
          </Card>

          <Card
            title={
              <span>
                <TeamOutlined style={{ color: '#1677ff' }} /> 人工在环抽样
                (HITL)
              </span>
            }
            styles={{ body: { padding: 24 } }}
          >
            <div>
              {HITL_ROWS.map((r) => (
                <button
                  type="button"
                  key={r.key}
                  data-testid={`preference-hitl-${r.key}`}
                  onClick={info}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    width: '100%',
                    textAlign: 'left',
                    background: 'transparent',
                    border: 'none',
                    borderBottom: '1px solid #f5f5f5',
                    padding: '12px 0',
                    cursor: 'pointer',
                  }}
                >
                  <span>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>
                      {r.title}
                    </div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {r.subtitle}
                    </Text>
                  </span>
                  <RightOutlined style={{ color: '#bfbfbf' }} />
                </button>
              ))}
            </div>
            <Checkbox
              checked={autoReflow}
              onChange={(e) => setAutoReflow(e.target.checked)}
              style={{ marginTop: 12 }}
              data-testid="preference-auto-reflow"
            >
              启用自动回流机制
            </Checkbox>
          </Card>
        </div>

        {/* 右列:偏好排序编辑器 */}
        <Card
          title={
            <span>
              <EditOutlined style={{ color: '#1677ff' }} /> 偏好排序编辑器
            </span>
          }
          extra={
            <span>
              <Tag>样本 ID: 0x8F92A1</Tag>
              <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                领域: 代码生成
              </Tag>
            </span>
          }
          styles={{ body: { padding: 24 } }}
        >
          {/* INPUT PROMPT */}
          <div
            style={{
              background: '#f0f7ff',
              borderLeft: '3px solid #1677ff',
              borderRadius: '0 8px 8px 0',
              padding: 12,
              marginBottom: 16,
            }}
          >
            <div style={{ color: '#1677ff', fontWeight: 700, fontSize: 11 }}>
              INPUT PROMPT
            </div>
            <div style={{ marginTop: 4 }}>
              请写一个 Python
              装饰器,用于测量函数执行时间,并能够将结果写入到指定的日志文件中。
            </div>
          </div>

          {/* 两个输出样本 */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 12,
            }}
          >
            {/* 样本 A */}
            <div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 8,
                }}
              >
                <Text strong>输出样本 A</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <ThunderboltOutlined /> 342 Tokens
                </Text>
              </div>
              <pre style={codeBlockStyle}>{SAMPLE_A_CODE}</pre>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginTop: 12,
                }}
              >
                <Button
                  icon={<CheckOutlined />}
                  style={{
                    border: '1px solid #1677ff',
                    color: '#1677ff',
                    background: '#e6f0ff',
                  }}
                  data-testid="preference-set-chosen"
                  onClick={info}
                >
                  设为首选 (Chosen)
                </Button>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  分值:
                </Text>
                <Input
                  value={chosenScore}
                  onChange={(e) => setChosenScore(e.target.value)}
                  style={{ width: 64 }}
                  data-testid="preference-chosen-score"
                />
              </div>
            </div>

            {/* 样本 B */}
            <div>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 8,
                }}
              >
                <Text strong>输出样本 B</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  <ThunderboltOutlined /> 210 Tokens
                </Text>
              </div>
              <pre style={codeBlockStyle}>{SAMPLE_B_CODE}</pre>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginTop: 12,
                }}
              >
                <Button
                  icon={<CloseOutlined />}
                  style={{
                    border: '1px solid #ff4d4f',
                    color: '#ff4d4f',
                  }}
                  data-testid="preference-set-rejected"
                  onClick={info}
                >
                  设为次选 (Rejected)
                </Button>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  分值:
                </Text>
                <Input
                  value={rejectedScore}
                  onChange={(e) => setRejectedScore(e.target.value)}
                  style={{ width: 64 }}
                  data-testid="preference-rejected-score"
                />
              </div>
            </div>
          </div>

          {/* 底部工具条 */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 16,
              borderTop: '1px solid #f0f0f0',
              paddingTop: 12,
            }}
          >
            <span style={{ display: 'flex', gap: 16 }}>
              <a style={{ color: '#8c8c8c' }} onClick={info}>
                <StepBackwardOutlined style={{ marginRight: 4 }} />
                上一个
              </a>
              <a style={{ color: '#8c8c8c' }} onClick={info}>
                跳过
              </a>
              <a style={{ color: '#8c8c8c' }} onClick={info}>
                下一个
                <StepForwardOutlined style={{ marginLeft: 4 }} />
              </a>
            </span>
            <span style={{ display: 'flex', gap: 8 }}>
              <Button data-testid="preference-save-draft" onClick={info}>
                存为草稿
              </Button>
              <Button
                type="primary"
                data-testid="preference-submit"
                onClick={info}
              >
                提交标注 (Next)
              </Button>
            </span>
          </div>
        </Card>
      </div>
    </PageContainer>
  );
};

export default PreferenceIngestPage;
