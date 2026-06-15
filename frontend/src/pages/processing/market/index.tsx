import { StarFilled } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Divider,
  Drawer,
  Empty,
  Input,
  Menu,
  message,
  Pagination,
  Row,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { listOperatorCatalog } from '@/services/data-platform';

const { Paragraph, Text } = Typography;

/** 可运行状态 → 单一状态信号(一个圆点 + 文案,避免多标签堆叠) */
const RUNNABLE_BADGE: Record<
  DataPlatform.CatalogOperator['runnable'],
  { status: 'success' | 'warning' | 'default'; text: string }
> = {
  ready: { status: 'success', text: '可运行' },
  needs_api: { status: 'warning', text: '需配置 LLM' },
  needs_media: { status: 'default', text: '需媒体数据' },
  needs_compute: { status: 'default', text: '需要算力' },
};

const RESOURCE_LABEL: Record<string, string> = {
  cpu: 'CPU',
  api_llm: 'LLM API',
  hf_model: 'HF 模型',
  gpu: 'GPU',
  vllm: 'vLLM',
};

const PARAM_COLUMNS = [
  { title: '参数', dataIndex: 'name', width: 160 },
  { title: '类型', dataIndex: 'type', width: 150, ellipsis: true },
  { title: '默认值', dataIndex: 'default', width: 120, ellipsis: true },
  { title: '说明', dataIndex: 'desc', ellipsis: true },
];

/** 一行 muted 元信息:资源类 + 模态 */
const metaLine = (op: DataPlatform.CatalogOperator) =>
  [RESOURCE_LABEL[op.resourceClass] ?? op.resourceClass, ...(op.modality ?? [])]
    .filter(Boolean)
    .join(' · ');

const PAGE_SIZE = 24;

const Market: React.FC = () => {
  const { steps, add, clear } = useModel('opCart');

  // 全量算子(一次性拉取)
  const [allOps, setAllOps] = useState<DataPlatform.CatalogOperator[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);

  // 过滤条件
  const [scenario, setScenario] = useState<string>();
  const [keyword, setKeyword] = useState<string>();
  const [onlyReady, setOnlyReady] = useState(false);
  const [onlyRecommend, setOnlyRecommend] = useState(true);

  // 分页
  const [current, setCurrent] = useState(1);

  const [detail, setDetail] = useState<DataPlatform.CatalogOperator>();

  // 拉全量目录:按 total 翻页取齐(目录是构建期快照,当前 212 条;
  // 后端单页上限 500,目录将来超限也不会被静默截断)
  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const first = await listOperatorCatalog({ pageSize: 500, current: 1 });
      const ops = [...(first.data ?? [])];
      while (ops.length < (first.total ?? 0)) {
        const next = await listOperatorCatalog({
          pageSize: 500,
          current: Math.floor(ops.length / 500) + 1,
        });
        if (!next.data?.length) break;
        ops.push(...next.data);
      }
      setAllOps(ops);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  // 先按开关 + 关键字过滤(不含场景),用于计算分面计数
  const switchFiltered = useMemo(() => {
    return allOps.filter((op) => {
      if (onlyReady && op.runnable !== 'ready') return false;
      if (onlyRecommend && !op.recommend) return false;
      if (keyword) {
        const kw = keyword.toLowerCase();
        if (
          !op.name.toLowerCase().includes(kw) &&
          !op.zhLabel.toLowerCase().includes(kw) &&
          !(op.summaryZh ?? '').toLowerCase().includes(kw)
        )
          return false;
      }
      return true;
    });
  }, [allOps, onlyReady, onlyRecommend, keyword]);

  // 分面计数:在 switchFiltered 上按场景分组
  const scenarioCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const op of switchFiltered) {
      const sg = op.scenarioGroup ?? '';
      counts[sg] = (counts[sg] ?? 0) + 1;
    }
    return counts;
  }, [switchFiltered]);

  // 场景全集(成员与排序按全量算子数固定,不随开关增减——0 计数类目保留置灰)
  const allScenarios = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const op of allOps) {
      const sg = op.scenarioGroup ?? '';
      totals[sg] = (totals[sg] ?? 0) + 1;
    }
    return Object.entries(totals)
      .sort((a, b) => b[1] - a[1])
      .map(([name]) => name);
  }, [allOps]);

  // 左侧菜单项(计数随开关/搜索实时变化,与列表同源)
  const menuItems = useMemo(() => {
    return [
      { key: 'all', label: `全部　${switchFiltered.length}` },
      ...allScenarios.map((name) => {
        const count = scenarioCounts[name] ?? 0;
        return {
          key: name,
          label: `${name}　${count}`,
          style: count === 0 ? { opacity: 0.45 } : undefined,
        };
      }),
    ];
  }, [allScenarios, scenarioCounts, switchFiltered.length]);

  // 最终展示列表(在 switchFiltered 基础上再加场景过滤)
  const filtered = useMemo(() => {
    if (!scenario) return switchFiltered;
    return switchFiltered.filter((op) => op.scenarioGroup === scenario);
  }, [switchFiltered, scenario]);

  // 当前页数据
  const pageData = useMemo(() => {
    const start = (current - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, current]);

  // 页头统计(来自全量,不受过滤影响)
  const headerStats = useMemo(() => {
    if (allOps.length === 0) return null;
    const readyCount = allOps.filter((op) => op.runnable === 'ready').length;
    const recCount = allOps.filter((op) => op.recommend).length;
    return `共 ${allOps.length} 个算子 · ${readyCount} 个现在可运行 · ${recCount} 个推荐`;
  }, [allOps]);

  const onAdd = (op: DataPlatform.CatalogOperator) => {
    add(op.name);
    message.success(`已加入「${op.zhLabel}」`);
  };

  return (
    <PageContainer
      content={headerStats ?? ' '}
      footer={
        steps.length
          ? [
              <Space key="cart">
                <Text type="secondary">已选 {steps.length} 个算子</Text>
                <Button onClick={clear}>清空</Button>
                <Button
                  type="primary"
                  onClick={() => history.push('/processing/editor')}
                >
                  去新建加工任务
                </Button>
              </Space>,
            ]
          : undefined
      }
    >
      <Row gutter={16}>
        {/* 左:场景分面(用 inline Menu,与平台左导航统一) */}
        <Col xs={24} md={6} lg={5} xl={4}>
          <Card styles={{ body: { padding: 0 } }}>
            <Menu
              mode="inline"
              selectedKeys={[scenario ?? 'all']}
              items={menuItems}
              style={{ borderInlineEnd: 'none' }}
              onClick={({ key }) => {
                setScenario(key === 'all' ? undefined : key);
                setCurrent(1);
              }}
            />
          </Card>
        </Col>

        {/* 右:工具栏 + 卡片栅格 */}
        <Col xs={24} md={18} lg={19} xl={20}>
          <Card>
            <Space
              style={{
                marginBottom: 16,
                width: '100%',
                justifyContent: 'space-between',
              }}
              wrap
            >
              <Input.Search
                allowClear
                placeholder="搜索算子名 / 中文说明"
                style={{ width: 280 }}
                onSearch={(v) => {
                  setKeyword(v || undefined);
                  setCurrent(1);
                }}
              />
              <Space size="large">
                <Space size={6}>
                  <Switch
                    size="small"
                    checked={onlyReady}
                    onChange={(v) => {
                      setOnlyReady(v);
                      setCurrent(1);
                    }}
                  />
                  <Text type="secondary">只看可运行</Text>
                </Space>
                <Space size={6}>
                  <Switch
                    size="small"
                    checked={onlyRecommend}
                    onChange={(v) => {
                      setOnlyRecommend(v);
                      setCurrent(1);
                    }}
                  />
                  <Text type="secondary">仅推荐</Text>
                </Space>
              </Space>
            </Space>

            {loadError ? (
              <Alert
                type="error"
                showIcon
                message="算子目录加载失败"
                description="请检查后端服务(:18003)是否在运行。"
                action={
                  <Button size="small" onClick={loadCatalog}>
                    重试
                  </Button>
                }
              />
            ) : !loading && filtered.length === 0 ? (
              <Empty
                description={
                  scenario && (scenarioCounts[scenario] ?? 0) === 0
                    ? '当前筛选下无算子,可关闭「仅推荐」查看全部'
                    : '没有符合条件的算子'
                }
                style={{ padding: '48px 0' }}
              />
            ) : (
              <Spin spinning={loading}>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns:
                      'repeat(auto-fill, minmax(300px, 1fr))',
                    gap: 16,
                  }}
                >
                  {pageData.map((op) => {
                    const badge = RUNNABLE_BADGE[op.runnable];
                    const inCart = steps.some((s) => s.name === op.name);
                    return (
                      <Card
                        key={op.name}
                        hoverable
                        variant="outlined"
                        styles={{
                          body: {
                            padding: 16,
                            height: '100%',
                            display: 'flex',
                            flexDirection: 'column',
                          },
                        }}
                        style={{ height: '100%' }}
                        onClick={() => setDetail(op)}
                      >
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            gap: 8,
                          }}
                        >
                          <Text strong ellipsis style={{ flex: 1 }}>
                            {op.zhLabel}
                          </Text>
                          {op.recommend && (
                            <StarFilled
                              style={{ color: '#faad14', fontSize: 12 }}
                            />
                          )}
                        </div>
                        <Text
                          type="secondary"
                          ellipsis
                          style={{ fontFamily: 'monospace', fontSize: 12 }}
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
                            margin: '10px 0 6px',
                            minHeight: 40,
                            fontSize: 13,
                          }}
                        >
                          {op.zhUsageTip || op.summaryZh}
                        </Paragraph>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {metaLine(op)}
                        </Text>
                        <Divider
                          style={{ marginBlock: 12, marginTop: 'auto' }}
                        />
                        <div
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                          }}
                        >
                          <Badge
                            status={badge.status}
                            text={
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {badge.text}
                              </Text>
                            }
                          />
                          <Button
                            size="small"
                            type="primary"
                            disabled={op.runnable !== 'ready' || inCart}
                            onClick={(e) => {
                              e.stopPropagation();
                              onAdd(op);
                            }}
                          >
                            {inCart ? '已加入' : '加入'}
                          </Button>
                        </div>
                      </Card>
                    );
                  })}
                </div>
                {filtered.length > PAGE_SIZE && (
                  <div style={{ marginTop: 16, textAlign: 'center' }}>
                    <Pagination
                      current={current}
                      pageSize={PAGE_SIZE}
                      total={filtered.length}
                      showSizeChanger={false}
                      onChange={setCurrent}
                    />
                  </div>
                )}
              </Spin>
            )}
          </Card>
        </Col>
      </Row>

      <Drawer
        width={680}
        open={!!detail}
        title={detail && `${detail.zhLabel} · ${detail.name}`}
        onClose={() => setDetail(undefined)}
        extra={
          detail &&
          detail.runnable === 'ready' && (
            <Button
              type="primary"
              disabled={steps.some((s) => s.name === detail.name)}
              onClick={() => onAdd(detail)}
            >
              {steps.some((s) => s.name === detail.name)
                ? '已加入'
                : '加入加工任务'}
            </Button>
          )
        }
      >
        {detail && (
          <>
            <Space wrap style={{ marginBottom: 16 }}>
              <Tag>{detail.category}</Tag>
              {(detail.modality ?? []).map((m) => (
                <Tag key={m}>{m}</Tag>
              ))}
              <Tag>
                {RESOURCE_LABEL[detail.resourceClass] ?? detail.resourceClass}
              </Tag>
              <Badge
                status={RUNNABLE_BADGE[detail.runnable].status}
                text={RUNNABLE_BADGE[detail.runnable].text}
              />
            </Space>
            {detail.zhUsageTip && (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                title="何时使用"
                description={detail.zhUsageTip}
              />
            )}
            <Paragraph>{detail.descZh || detail.summaryZh}</Paragraph>
            {detail.descEn && (
              <Paragraph type="secondary" style={{ fontSize: 12 }}>
                {detail.descEn}
              </Paragraph>
            )}
            <Typography.Title level={5} style={{ marginTop: 16 }}>
              参数
            </Typography.Title>
            {detail.params?.length ? (
              <Table
                size="small"
                rowKey="name"
                pagination={false}
                dataSource={detail.params}
                columns={PARAM_COLUMNS}
              />
            ) : (
              <Text type="secondary">无参数</Text>
            )}
            {detail.example && (
              <>
                <Typography.Title level={5} style={{ marginTop: 16 }}>
                  用法示例
                </Typography.Title>
                <pre
                  style={{
                    background: 'var(--ant-color-fill-quaternary, #f5f5f5)',
                    padding: 12,
                    borderRadius: 6,
                    overflow: 'auto',
                    fontSize: 12,
                  }}
                >
                  {detail.example}
                </pre>
              </>
            )}
            {detail.runnable !== 'ready' && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 16 }}
                title={
                  detail.runnable === 'needs_api'
                    ? '该算子需要 LLM API:在后端 .env 配置 OPENAI_* 后可用。'
                    : detail.runnable === 'needs_compute'
                      ? '该算子需要 GPU / HuggingFace 模型 / vLLM,当前环境暂不可执行。'
                      : '该算子处理图像/音视频,当前受管数据集为文本,暂不适用。'
                }
              />
            )}
          </>
        )}
      </Drawer>
    </PageContainer>
  );
};

export default Market;
