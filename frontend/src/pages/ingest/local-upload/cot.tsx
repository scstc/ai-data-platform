import {
  ApartmentOutlined,
  BranchesOutlined,
  BulbOutlined,
  CheckCircleFilled,
  CopyOutlined,
  DatabaseOutlined,
  EyeOutlined,
  OrderedListOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Button, Card, message, Slider, Tag, Typography } from 'antd';
import type { CSSProperties, ReactNode } from 'react';
import { useState } from 'react';
import { buildBreadcrumb } from '@/utils/breadcrumb';
import ScenarioImportCard from './ImportCard';

const { Text, Title } = Typography;

/** COT 思维链推理数据配置(设计稿高保真脚手架)。
 *  左侧参数 / 校验规则 / 架构模板为本地状态,右侧「实时推理链预览」为静态演示;
 *  真实推理生成 / 校验逻辑待后端接入逻辑确定后接通,「保存并部署」暂以提示占位。 */

/** 逻辑校验规则项 */
const RULES: {
  key: string;
  title: string;
  icon: ReactNode;
  color: string;
}[] = [
  {
    key: 'causal',
    title: '因果连贯性校验',
    icon: <CheckCircleFilled />,
    color: '#52c41a',
  },
  {
    key: 'math',
    title: '数学符号完整性',
    icon: <SafetyOutlined />,
    color: '#8c8c8c',
  },
  {
    key: 'antiHallucination',
    title: '拒绝幻觉循环',
    icon: <WarningOutlined />,
    color: '#faad14',
  },
];

/** 逻辑架构模板 */
const TEMPLATES: {
  key: string;
  title: string;
  desc: string;
  icon: ReactNode;
}[] = [
  {
    key: 'socratic',
    title: '苏格拉底式提问',
    desc: '引导式推理,适用于复杂学术任务',
    icon: <ApartmentOutlined />,
  },
  {
    key: 'linear',
    title: '线性因果链',
    desc: '标准 A → B → C 推理路径',
    icon: <OrderedListOutlined />,
  },
  {
    key: 'contrast',
    title: '对比批判思维',
    desc: '正反两面论证后再得出结论',
    icon: <BranchesOutlined />,
  },
  {
    key: 'intuition',
    title: '直觉跳跃验证',
    desc: '非线性逻辑,重点在于最终回溯',
    icon: <BulbOutlined />,
  },
];

const chip: CSSProperties = {
  background: '#334155',
  color: '#e2e8f0',
  borderRadius: 4,
  padding: '2px 8px',
  fontSize: 12,
};

const inlineCode: CSSProperties = {
  background: 'transparent',
  color: '#4ade80',
  fontWeight: 600,
};

/** 推理链步骤(静态演示) */
const STEPS: { no: number; title: string; body: ReactNode }[] = [
  {
    no: 1,
    title: '变量提取与等式整理',
    body: (
      <>
        首先,我需要确定已知条件: <code style={chip}>1) X + 5 = 12</code>{' '}
        <code style={chip}>2) Y = X * 2</code>
      </>
    ),
  },
  {
    no: 2,
    title: '求解变量 X',
    body: (
      <>
        利用等式 (1),解出 X 的值:
        <br />X = 12 - 5
        <br />
        因此,<code style={inlineCode}>X = 7</code>
      </>
    ),
  },
  {
    no: 3,
    title: '代入求值',
    body: (
      <>
        将 X = 7 代入等式 (2) 以计算 Y:
        <br />Y = 7 * 2
        <br />
        得出结论:<code style={inlineCode}>Y = 14</code>
      </>
    ),
  },
];

const CotIngestPage: React.FC = () => {
  const [depth, setDepth] = useState<[number, number]>([3, 8]);
  const [checked, setChecked] = useState<Record<string, boolean>>({
    causal: true,
    math: false,
    antiHallucination: true,
  });
  const [template, setTemplate] = useState('socratic');

  const toggle = (key: string) => setChecked((p) => ({ ...p, [key]: !p[key] }));

  return (
    <PageContainer
      breadcrumb={buildBreadcrumb([
        { title: '数据接入', path: '/ingest/datasources' },
        { title: '本地上传', path: '/ingest/local-upload' },
        { title: '场景数据', path: '/ingest/local-upload/scenario' },
        { title: 'COT 推理数据配置' },
      ])}
      title="思维链 (COT) 推理数据配置"
      content="配置推理路径、逻辑校验与架构模板,生成可训练的思维链数据。"
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
          onClick={() => message.info('接入逻辑待定,确定后开放保存并部署')}
        >
          保存并部署
        </Button>,
      ]}
    >
      <ScenarioImportCard semanticType="cot" />
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.1fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {/* 左列:推理核心参数 + 逻辑架构模板 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card styles={{ body: { padding: 24 } }}>
            <SectionTitle>推理核心参数</SectionTitle>

            <div style={{ marginTop: 20 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <Text strong style={{ fontSize: 13 }}>
                  推理路径深度 (DEPTH)
                </Text>
                <Tag
                  style={{
                    fontSize: 13,
                    padding: '4px 12px',
                    margin: 0,
                    borderRadius: 6,
                  }}
                >
                  {depth[0]} - {depth[1]} Steps
                </Tag>
              </div>
              <Slider
                range
                min={1}
                max={12}
                value={depth}
                onChange={(v) => setDepth(v as [number, number])}
                style={{ marginTop: 8 }}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                控制思维链推理的最小与最大步骤阈值。
              </Text>
            </div>

            <Text
              strong
              style={{ fontSize: 13, display: 'block', margin: '20px 0 12px' }}
            >
              逻辑校验规则
            </Text>
            <div style={{ display: 'grid', gap: 12 }}>
              {RULES.map((r) => {
                const on = checked[r.key];
                return (
                  <button
                    type="button"
                    key={r.key}
                    data-testid={`cot-rule-${r.key}`}
                    onClick={() => toggle(r.key)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      width: '100%',
                      textAlign: 'left',
                      cursor: 'pointer',
                      padding: '12px 14px',
                      borderRadius: 8,
                      background: '#fff',
                      border: `1px solid ${on ? '#52c41a55' : '#f0f0f0'}`,
                      borderLeft: `3px solid ${on ? r.color : 'transparent'}`,
                    }}
                  >
                    <span style={{ fontSize: 18, color: r.color }}>
                      {r.icon}
                    </span>
                    <Text strong style={{ flex: 1, fontSize: 13 }}>
                      {r.title}
                    </Text>
                    <span
                      style={{
                        width: 18,
                        height: 18,
                        borderRadius: 4,
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: on ? '#1677ff' : '#fff',
                        border: `1px solid ${on ? '#1677ff' : '#d9d9d9'}`,
                        color: '#fff',
                        fontSize: 12,
                      }}
                    >
                      {on ? '✓' : ''}
                    </span>
                  </button>
                );
              })}
            </div>
          </Card>

          <Card styles={{ body: { padding: 24 } }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 16,
              }}
            >
              <SectionTitle>逻辑架构模板</SectionTitle>
              <Tag color="blue" style={{ margin: 0, fontWeight: 600 }}>
                PREMIUM
              </Tag>
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
              }}
            >
              {TEMPLATES.map((t) => {
                const active = template === t.key;
                return (
                  <button
                    type="button"
                    key={t.key}
                    data-testid={`cot-template-${t.key}`}
                    onClick={() => setTemplate(t.key)}
                    style={{
                      textAlign: 'left',
                      cursor: 'pointer',
                      padding: 16,
                      borderRadius: 8,
                      background: active ? '#e6f0ff' : '#fff',
                      border: `1px solid ${active ? '#1677ff' : '#f0f0f0'}`,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 20,
                        color: active ? '#1677ff' : '#8c8c8c',
                      }}
                    >
                      {t.icon}
                    </span>
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: 13,
                        margin: '10px 0 4px',
                      }}
                    >
                      {t.title}
                    </div>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {t.desc}
                    </Text>
                  </button>
                );
              })}
            </div>
          </Card>
        </div>

        {/* 右列:实时推理链预览(静态演示,深色) */}
        <Card
          styles={{ body: { padding: 0 } }}
          style={{ background: '#0f172a', borderColor: '#1e293b' }}
        >
          <div style={{ padding: 20, color: '#cbd5e1' }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 16,
              }}
            >
              <span style={{ fontWeight: 600, color: '#e2e8f0' }}>
                <EyeOutlined style={{ marginRight: 8 }} />
                实时推理链预览 (Preview Mode)
              </span>
              <span
                style={{ fontSize: 11, color: '#4ade80', letterSpacing: 0.5 }}
              >
                ● SIMULATION ACTIVE
              </span>
            </div>

            {/* QUESTION / INPUT */}
            <div
              style={{ background: '#1e293b', borderRadius: 8, padding: 16 }}
            >
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>
                QUESTION / INPUT:
              </div>
              <Text style={{ color: '#e2e8f0' }}>
                “如果 X + 5 = 12 且 Y = X * 2,那么 Y 的值是多少?
                请展示推理过程。”
              </Text>
            </div>

            {/* STEPS */}
            <div
              style={{
                marginTop: 16,
                paddingLeft: 16,
                borderLeft: '2px solid #334155',
                display: 'grid',
                gap: 18,
              }}
            >
              {STEPS.map((s) => (
                <div key={s.no} style={{ position: 'relative' }}>
                  <span
                    style={{
                      position: 'absolute',
                      left: -22,
                      top: 4,
                      width: 8,
                      height: 8,
                      borderRadius: '50%',
                      background: '#64748b',
                    }}
                  />
                  <div style={{ fontWeight: 600, color: '#e2e8f0' }}>
                    Step {s.no}: {s.title}
                  </div>
                  <div
                    style={{
                      marginTop: 6,
                      color: '#94a3b8',
                      fontSize: 13,
                      lineHeight: 1.8,
                    }}
                  >
                    {s.body}
                  </div>
                </div>
              ))}

              {/* VALIDATION */}
              <div
                style={{
                  background: '#0c2a1e',
                  border: '1px solid #14532d',
                  borderRadius: 8,
                  padding: 12,
                }}
              >
                <div style={{ color: '#4ade80', fontWeight: 600 }}>
                  <CheckCircleFilled style={{ marginRight: 8 }} />
                  VALIDATION: 因果连贯性通过
                </div>
                <div
                  style={{
                    marginTop: 6,
                    color: '#86efac',
                    fontSize: 12,
                    fontStyle: 'italic',
                  }}
                >
                  验证逻辑: X(7) + 5 = 12 (True) | Y(14) / 2 = 7
                  (True)。推理链完整。
                </div>
              </div>
            </div>

            {/* FINAL OUTPUT */}
            <div
              style={{
                marginTop: 16,
                background: '#1e293b',
                borderRadius: 8,
                padding: 16,
              }}
            >
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>
                FINAL OUTPUT:
              </div>
              <Title level={4} style={{ margin: 0, color: '#f8fafc' }}>
                答案是 14。
              </Title>
            </div>
          </div>

          {/* 底部工具条 */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '12px 20px',
              borderTop: '1px solid #1e293b',
              color: '#64748b',
              fontSize: 12,
            }}
          >
            <span style={{ display: 'flex', gap: 16 }}>
              <a
                style={{ color: '#94a3b8' }}
                onClick={() => message.info('重新生成待接入')}
              >
                <ReloadOutlined style={{ marginRight: 4 }} />
                重新生成
              </a>
              <a
                style={{ color: '#94a3b8' }}
                onClick={() => message.info('复制代码待接入')}
              >
                <CopyOutlined style={{ marginRight: 4 }} />
                复制代码
              </a>
            </span>
            <span>模拟延迟: 450ms&nbsp;&nbsp;&nbsp;Tokens: 218</span>
          </div>
        </Card>
      </div>

      {/* 底部统计卡 */}
      <div
        style={{
          marginTop: 16,
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 16,
        }}
      >
        <StatCard
          icon={<DatabaseOutlined />}
          label="目标数据集"
          value="COT_Training_v2.jsonl"
        />
        <StatCard
          icon={<ThunderboltOutlined />}
          label="预计处理速度"
          value="~1.2k rows/min"
        />
        <StatCard
          icon={<SafetyCertificateOutlined />}
          label="敏感信息过滤"
          value="Level 3 (Strict)"
        />
      </div>
    </PageContainer>
  );
};

/** 卡片小节标题(蓝色竖条 + 文字) */
const SectionTitle: React.FC<{ children: string }> = ({ children }) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
    <span
      style={{
        width: 4,
        height: 18,
        borderRadius: 2,
        background: '#1677ff',
        display: 'inline-block',
      }}
    />
    <Text strong style={{ fontSize: 16 }}>
      {children}
    </Text>
  </span>
);

/** 底部统计卡 */
const StatCard: React.FC<{
  icon: ReactNode;
  label: string;
  value: string;
}> = ({ icon, label, value }) => (
  <Card styles={{ body: { padding: 16 } }} style={{ background: '#fafafa' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
      <span
        style={{
          fontSize: 20,
          color: '#1677ff',
          width: 44,
          height: 44,
          borderRadius: 8,
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#fff',
          border: '1px solid #f0f0f0',
        }}
      >
        {icon}
      </span>
      <div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {label}
        </Text>
        <div style={{ fontWeight: 600, fontSize: 15 }}>{value}</div>
      </div>
    </div>
  </Card>
);

export default CotIngestPage;
