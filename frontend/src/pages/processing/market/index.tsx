import {
  EyeInvisibleOutlined,
  EyeOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Empty,
  Input,
  Menu,
  Pagination,
  Row,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  getOperatorCapabilities,
  listOperatorCatalog,
  updateOperatorVisible,
} from '@/services/data-platform';
import {
  CATEGORY_LABEL,
  MODALITY_LABEL,
  RESOURCE_LABEL,
  RUNNABLE_TAG,
} from './_labels';

const { Paragraph, Text } = Typography;

/** 顶部 segmented:执行要求。 */
const RUNNABLE_FILTER_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '可直接执行', value: 'ready' },
  { label: '需要 AI', value: 'needs_api' },
  { label: '需要算力', value: 'needs_compute' },
];

/** DJ 算子 8 大类型(category 字段),与 Operators.md §Overview 一一对应。 */
const DJ_CATEGORIES = [
  'aggregator',
  'deduplicator',
  'filter',
  'formatter',
  'grouper',
  'mapper',
  'pipeline',
  'selector',
];

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

const MODALITY_CHIPS = (
  ['text', 'image', 'audio', 'video', 'multimodal'] as const
).map((v) => ({ value: v, label: MODALITY_LABEL[v] ?? v }));
const RESOURCE_CHIPS = (
  ['cpu', 'api_llm', 'hf_model', 'gpu', 'vllm'] as const
).map((v) => ({ value: v, label: RESOURCE_LABEL[v] }));
const ALL_KEY = '__all__';
const PAGE_SIZE = 24;

const Market: React.FC = () => {
  const access = useAccess();
  const { message } = App.useApp();
  const canUploadOperator = access.hasPerm('operator:upload');
  // 隐藏/恢复算子(隐藏后市场与编排不再展示,已编排任务不受影响)
  const canManageVisibility = access.hasPerm('operator:visibility');

  // 全量算子(一次性拉取)
  const [allOps, setAllOps] = useState<DataPlatform.CatalogOperator[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);

  // 当前环境执行能力(GPU/LLM/vLLM/Ray)
  const [caps, setCaps] = useState<DataPlatform.OperatorCapabilities>();

  // 过滤条件
  const [category, setCategory] = useState<string | null>(null);
  const [modalities, setModalities] = useState<string[]>([]);
  const [resources, setResources] = useState<string[]>([]);
  const [runnableFilter, setRunnableFilter] = useState<string>('all');
  const [onlyRecommended, setOnlyRecommended] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [keyword, setKeyword] = useState<string | null>(null);

  // 分页
  const [current, setCurrent] = useState(1);

  // 拉全量目录(后端单页上限 500)
  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      getOperatorCapabilities()
        .then((r) => setCaps(r.data))
        .catch((err) =>
          console.warn('[market] capabilities probe failed', err),
        );
      // 有可见性管理权限时连已隐藏的一起拉,由「显示已隐藏」开关控制展示
      const first = await listOperatorCatalog({
        pageSize: 500,
        current: 1,
        includeHidden: canManageVisibility,
      });
      const ops = [...(first.data ?? [])];
      while (ops.length < (first.total ?? 0)) {
        const next = await listOperatorCatalog({
          pageSize: 500,
          current: Math.floor(ops.length / 500) + 1,
          includeHidden: canManageVisibility,
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
  }, [canManageVisibility]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  /** 公共过滤(不含 category 自身):chips + 搜索 + 推荐 + 已隐藏。 */
  const baseFiltered = useMemo(() => {
    const kw = keyword?.toLowerCase();
    return allOps.filter((op) => {
      if (op.visible === false && !showHidden) return false;
      if (runnableFilter !== 'all' && op.runnable !== runnableFilter)
        return false;
      if (onlyRecommended && !op.recommend) return false;
      if (
        modalities.length > 0 &&
        !modalities.some((m) => (op.modality ?? []).includes(m))
      )
        return false;
      if (resources.length > 0 && !resources.includes(op.resourceClass))
        return false;
      if (kw) {
        const hay = (
          op.name +
          (op.zhLabel ?? '') +
          (op.summaryZh ?? '') +
          (op.tags ?? []).join(' ')
        ).toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }, [
    allOps,
    modalities,
    resources,
    runnableFilter,
    onlyRecommended,
    showHidden,
    keyword,
  ]);

  /** 列表展示(在公共过滤之上再叠 category)。 */
  const filtered = useMemo(() => {
    if (!category) return baseFiltered;
    return baseFiltered.filter((op) => op.category === category);
  }, [baseFiltered, category]);

  /** 菜单计数(基于 baseFiltered,切类时其它类与"全部"数字保持稳定)。 */
  const categoryLiveCounts = useMemo(() => {
    const counts: Record<string, number> = { [ALL_KEY]: baseFiltered.length };
    for (const k of DJ_CATEGORIES) counts[k] = 0;
    for (const op of baseFiltered) {
      if (op.category && counts[op.category] !== undefined)
        counts[op.category] += 1;
    }
    return counts;
  }, [baseFiltered]);

  const currentPageData = useMemo(() => {
    const start = (current - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, current]);

  const headerStats = useMemo(() => {
    if (allOps.length === 0) return null;
    const visibleOps = allOps.filter((op) => op.visible !== false);
    const readyCount = visibleOps.filter(
      (op) => op.runnable === 'ready',
    ).length;
    const hiddenCount = allOps.length - visibleOps.length;
    const base = `共 ${visibleOps.length} 个算子 · ${readyCount} 个现在可运行`;
    return hiddenCount > 0 ? `${base} · ${hiddenCount} 个已隐藏` : base;
  }, [allOps]);

  // 顶部 chips 重置:任何 filter 变化都翻回第 1 页
  const resetPage = () => setCurrent(1);

  /** 隐藏/恢复算子:落库后本地同步,免整页重拉。 */
  const toggleVisible = async (op: DataPlatform.CatalogOperator) => {
    const next = op.visible === false;
    try {
      await updateOperatorVisible(op.name, next);
      setAllOps((prev) =>
        prev.map((o) => (o.name === op.name ? { ...o, visible: next } : o)),
      );
      message.success(
        next
          ? `已恢复显示「${op.zhLabel}」`
          : `已隐藏「${op.zhLabel}」,市场与编排不再展示`,
      );
    } catch {
      message.error('操作失败,请重试');
    }
  };

  return (
    <PageContainer
      content={
        <Space direction="vertical" size={4}>
          <Text type="secondary">{headerStats ?? ' '}</Text>
          {caps && (
            <Space size={6} wrap>
              <Text type="secondary" style={{ fontSize: 12 }}>
                环境能力:
              </Text>
              {(
                [
                  ['cuda', 'GPU'],
                  ['llm', 'LLM'],
                  ['vllm', 'vLLM'],
                  ['ray', 'Ray'],
                ] as [keyof DataPlatform.OperatorCapabilities, string][]
              ).map(([k, label]) => (
                <Tag key={k} color={caps[k] ? 'success' : 'default'}>
                  {label} {caps[k] ? '✓' : '✗'}
                </Tag>
              ))}
            </Space>
          )}
        </Space>
      }
    >
      <Row gutter={16}>
        {/* 左:DJ 算子类型(Operators.md §Overview 8 类)一级菜单 */}
        <Col xs={24} md={6} lg={5} xl={4}>
          <Card
            title={<Text strong>算子类型</Text>}
            styles={{ body: { padding: 0 } }}
          >
            <Menu
              mode="inline"
              selectedKeys={[category ?? ALL_KEY]}
              style={{ borderInlineEnd: 'none' }}
              onClick={({ key }) => {
                setCategory(key === ALL_KEY ? null : key);
                resetPage();
              }}
              items={[
                {
                  key: ALL_KEY,
                  label: (
                    <Space>
                      <span>📦</span>
                      <span>全部算子</span>
                      <Text type="secondary">
                        {categoryLiveCounts[ALL_KEY]}
                      </Text>
                    </Space>
                  ),
                },
                ...DJ_CATEGORIES.map((k) => {
                  const live = categoryLiveCounts[k] ?? 0;
                  return {
                    key: k,
                    label: (
                      <Space>
                        <span>{CATEGORY_LABEL[k] ?? k}</span>
                        <Text type="secondary">{live}</Text>
                      </Space>
                    ),
                    style: live === 0 ? { opacity: 0.45 } : undefined,
                  };
                }),
              ]}
            />
          </Card>
        </Col>

        {/* 右:多维 chips + 卡片栅格 */}
        <Col xs={24} md={18} lg={19} xl={20}>
          <Card>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {/* 搜索 + runnable + 推荐 + 上传 */}
              <Space
                wrap
                style={{ width: '100%', justifyContent: 'space-between' }}
              >
                <Input.Search
                  allowClear
                  placeholder="搜索算子名 / 中文标签 / 描述"
                  style={{ width: 280 }}
                  onSearch={(v) => {
                    setKeyword(v || null);
                    resetPage();
                  }}
                />
                <Space size="middle" wrap>
                  <Segmented
                    size="small"
                    value={runnableFilter}
                    options={RUNNABLE_FILTER_OPTIONS}
                    onChange={(v) => {
                      setRunnableFilter(v as string);
                      resetPage();
                    }}
                  />
                  <Checkbox
                    checked={onlyRecommended}
                    onChange={(e) => {
                      setOnlyRecommended(e.target.checked);
                      resetPage();
                    }}
                  >
                    只看推荐
                  </Checkbox>
                  {canManageVisibility && (
                    <Checkbox
                      checked={showHidden}
                      onChange={(e) => {
                        setShowHidden(e.target.checked);
                        resetPage();
                      }}
                    >
                      显示已隐藏
                    </Checkbox>
                  )}
                  {canUploadOperator && (
                    <Button
                      icon={<UploadOutlined />}
                      onClick={() => history.push('/operators/upload')}
                    >
                      上传自定义算子
                    </Button>
                  )}
                </Space>
              </Space>

              {/* 模态 + 资源 收窄 */}
              <Space wrap size={6}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  模态:
                </Text>
                <Checkbox.Group
                  options={MODALITY_CHIPS}
                  value={modalities}
                  onChange={(v) => {
                    setModalities(v as string[]);
                    resetPage();
                  }}
                />
              </Space>
              <Space wrap size={6}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  资源:
                </Text>
                <Checkbox.Group
                  options={RESOURCE_CHIPS}
                  value={resources}
                  onChange={(v) => {
                    setResources(v as string[]);
                    resetPage();
                  }}
                />
              </Space>
            </Space>

            <Divider style={{ margin: '12px 0' }} />

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
                description="当前筛选下无算子,调整筛选项或换类型"
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
                  {currentPageData.map((op) => {
                    const tag = runnableTag(op);
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
                        onClick={() => history.push(`/operators/${op.name}`)}
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
                          {op.visible === false && <Tag>已隐藏</Tag>}
                          {op.isCustom && <Tag color="purple">自定义</Tag>}
                          {op.recommend && <Tag color="gold">推荐</Tag>}
                          {canManageVisibility && (
                            <Button
                              size="small"
                              type="text"
                              title={
                                op.visible === false ? '恢复显示' : '隐藏算子'
                              }
                              icon={
                                op.visible === false ? (
                                  <EyeOutlined />
                                ) : (
                                  <EyeInvisibleOutlined />
                                )
                              }
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleVisible(op);
                              }}
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
                          {[
                            RESOURCE_LABEL[op.resourceClass] ??
                              op.resourceClass,
                            ...(op.modality ?? []).map(
                              (m) => MODALITY_LABEL[m] ?? m,
                            ),
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </Text>
                        <Divider
                          style={{ marginBlock: 12, marginTop: 'auto' }}
                        />
                        <Tag color={tag.color} style={{ marginInlineEnd: 0 }}>
                          {tag.label}
                        </Tag>
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
    </PageContainer>
  );
};

export default Market;
