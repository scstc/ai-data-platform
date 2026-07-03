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
import {
  getOperatorDetail,
  listOperatorCatalog,
} from '@/services/data-platform';
import { PARAM_ZH_DESC } from './_paramZhDict';
import {
  CATEGORY_LABEL,
  MODALITY_LABEL,
  RESOURCE_LABEL,
  RUNNABLE_TAG,
} from './_labels';

const { Title, Paragraph, Text } = Typography;

/** needs_compute 拆细:Ray 算子要 Ray 集群、vllm 要 vLLM 服务、其余要 GPU。
 *  优先用 resourceClass / frameworks,避免 name 前缀魔法。 */
function runnableTag(op: DataPlatform.CatalogOperator): {
  label: string;
  color: string;
} {
  if (op.runnable === 'needs_compute') {
    if ((op.frameworks ?? []).includes('ray'))
      return { label: '需要 Ray 集群', color: 'volcano' };
    if (op.resourceClass === 'vllm')
      return { label: '需要 vLLM 服务', color: 'volcano' };
    return { label: '需要 GPU', color: 'volcano' };
  }
  return RUNNABLE_TAG[op.runnable];
}

/** 参数类型清洗:`<class 'bool'>` → bool;带模块路径取末段。 */
const cleanType = (t?: string): string => {
  if (!t) return '';
  const m = t.match(/<class '(.+?)'>/);
  const raw = m ? m[1] : t;
  return raw.split('.').pop() ?? raw;
};

/** 参数必填推断:默认值为空/None/None-like → 必填。
 *  DJ 注册参数没有显式 required 字段;约定俗成:默认值是 `None` 或空字符串时视为必填。 */
const isRequired = (defaultVal: unknown): boolean => {
  if (defaultVal === null || defaultVal === undefined) return true;
  const s = String(defaultVal).trim();
  if (s === '') return true;
  if (/^none$/i.test(s)) return true;
  return false;
};

/** 默认值展示:None → "—",带引号字符串去掉一层引号。 */
const formatDefault = (v: unknown): string => {
  if (v === null || v === undefined) return '—';
  const s = String(v);
  if (/^none$/i.test(s.trim())) return '—';
  return s;
};

const PARAM_COLUMNS = [
  { title: '参数', dataIndex: 'name', width: 220 },
  {
    title: '类型',
    dataIndex: 'type',
    width: 260,
    render: (t: string) => <Text code>{cleanType(t)}</Text>,
  },
  {
    title: '默认值',
    dataIndex: 'default',
    width: 280,
    render: (v: unknown) => <Text code>{formatDefault(v)}</Text>,
  },
  {
    title: '中文说明',
    dataIndex: 'desc',
    render: (desc: string, row: { name: string }) => {
      const zh = PARAM_ZH_DESC[row.name];
      // 命中字典 → 显示中文;未命中 → 回退到英文 desc(避免吞掉原始信息)
      return zh ?? desc;
    },
  },
  {
    title: '必填',
    dataIndex: 'required',
    width: 80,
    render: (_: unknown, row: { default: unknown }) =>
      isRequired(row.default) ? (
        <Tag color="red">必填</Tag>
      ) : (
        <Text type="secondary">否</Text>
      ),
  },
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
  const [error, setError] = useState<string | null>(null);
  const [related, setRelated] = useState<DataPlatform.CatalogOperator[]>([]);

  useEffect(() => {
    if (!name) return;
    // 切算子时立即重置,避免旧数据闪烁
    setOperator(undefined);
    setRelated([]);
    setError(null);
    setLoading(true);
    const ac = new AbortController();
    getOperatorDetail(name)
      .then((res) => {
        if (ac.signal.aborted) return;
        const op = res.data;
        setOperator(op);
        // 相关算子:同 category,排除自己,最多 6 个
        if (op?.category) {
          listOperatorCatalog({
            category: op.category,
            pageSize: 7,
            current: 1,
          })
            .then((r) => {
              if (ac.signal.aborted) return;
              setRelated(
                (r.data ?? []).filter((o) => o.name !== op.name).slice(0, 6),
              );
            })
            .catch(() => {
              if (!ac.signal.aborted) setRelated([]);
            });
        } else {
          setRelated([]);
        }
      })
      .catch((err) => {
        if (ac.signal.aborted) return;
        setError(err?.message || '算子加载失败');
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [name]);

  if (!name) {
    return <div>算子名称缺失</div>;
  }

  const runTag = operator ? RUNNABLE_TAG[operator.runnable] : null;
  const demos = operator?.effectDemo ?? [];
  const categoryLabel =
    operator?.category && CATEGORY_LABEL[operator.category]
      ? CATEGORY_LABEL[operator.category]
      : operator?.category;

  return (
    <PageContainer
      loading={loading}
      title={operator?.zhLabel ?? '算子详情页'}
      onBack={() => history.back()}
    >
      {error ? (
        <Alert
          type="error"
          showIcon
          message="算子加载失败"
          description={error}
        />
      ) : operator && (
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
              <Space orientation="vertical" size={8} style={{ flex: 1 }}>
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
                  {operator.isCustom && <Tag color="purple">自定义</Tag>}
                  {operator.recommend && <Tag color="gold">推荐</Tag>}
                  {(operator.usageCount ?? 0) > 0 && (
                    <Tag>{operator.usageCount} 次使用</Tag>
                  )}
                </Space>
                <Space size="middle" wrap>
                  {categoryLabel && (
                    <ChipRow label="DJ 类型">
                      <Tag color="blue">{categoryLabel}</Tag>
                    </ChipRow>
                  )}
                  {operator.scenarioGroup && (
                    <ChipRow label="场景">
                      <Tag>{operator.scenarioGroup}</Tag>
                    </ChipRow>
                  )}
                  {operator.modality && operator.modality.length > 0 && (
                    <ChipRow label="模态">
                      {operator.modality.map((m) => (
                        <Tag key={m} color="cyan">
                          {MODALITY_LABEL[m] ?? m}
                        </Tag>
                      ))}
                    </ChipRow>
                  )}
                  <ChipRow label="资源">
                    <Tag>
                      {RESOURCE_LABEL[operator.resourceClass] ??
                        operator.resourceClass}
                    </Tag>
                  </ChipRow>
                  <ChipRow label="可运行">
                    {runTag && <Tag color={runTag.color}>{runTag.label}</Tag>}
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
                      <div key={d.before + d.after + i}>
                        <EffectBlock
                          label="处理前"
                          text={d.before}
                          url={d.before_url}
                          mediaType={d.media_type}
                        />
                        <EffectBlock
                          label="处理后"
                          text={d.after}
                          highlight
                          url={d.after_url}
                          mediaType={d.media_type}
                        />
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

          {/* 相关算子:同 scenarioGroup + category,卡片式 */}
          {related.length > 0 && (
            <Card title="相关算子">
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                  gap: 12,
                }}
              >
                {related.map((op) => {
                  const tag = RUNNABLE_TAG[op.runnable];
                  return (
                    <Card
                      key={op.name}
                      hoverable
                      size="small"
                      variant="outlined"
                      onClick={() => history.push(`/operators/${op.name}`)}
                      styles={{
                        body: {
                          padding: 12,
                          height: '100%',
                          display: 'flex',
                          flexDirection: 'column',
                        },
                      }}
                      style={{ height: '100%' }}
                    >
                      <Text strong ellipsis>
                        {op.zhLabel}
                      </Text>
                      <Text
                        type="secondary"
                        ellipsis
                        style={{ fontFamily: 'monospace', fontSize: 11 }}
                      >
                        {op.name}
                      </Text>
                      <Paragraph
                        type="secondary"
                        ellipsis={{
                          rows: 2,
                          tooltip: op.zhUsageTip || op.summaryZh,
                        }}
                        style={{
                          margin: '6px 0 0',
                          fontSize: 12,
                          minHeight: 32,
                        }}
                      >
                        {op.zhUsageTip || op.summaryZh}
                      </Paragraph>
                      {tag && (
                        <Tag
                          color={tag.color}
                          style={{
                            marginTop: 'auto',
                            marginInlineEnd: 0,
                            alignSelf: 'flex-start',
                          }}
                        >
                          {tag.label}
                        </Tag>
                      )}
                    </Card>
                  );
                })}
              </div>
            </Card>
          )}
        </Space>
      )}
    </PageContainer>
  );
};

/** 效果展示单块:有 url 时渲染 <img>/<video>/<audio>,回退到文本。 */
const EffectBlock: React.FC<{
  label: string;
  text: string;
  url?: string;
  mediaType?: string;
  highlight?: boolean;
}> = ({ label, text, url, mediaType, highlight }) => (
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
    {url ? (
      <div
        style={{
          border: '1px solid var(--ant-color-border-secondary, #f0f0f0)',
          borderRadius: 6,
          padding: 4,
          background: highlight
            ? 'var(--ant-color-success-bg, #f6ffed)'
            : undefined,
        }}
      >
        {mediaType === 'video' ? (
          <video src={url} controls style={{ width: '100%', borderRadius: 4 }} />
        ) : mediaType === 'audio' ? (
          <audio src={url} controls style={{ width: '100%' }} />
        ) : (
          <img
            src={url}
            alt={label}
            style={{ width: '100%', borderRadius: 4, display: 'block' }}
          />
        )}
      </div>
    ) : (
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
    )}
  </div>
);

export default OperatorDetail;
