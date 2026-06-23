// 数据血缘:数据集版本管理(左)+ 血缘关系图(右,版本↔任务 DAG,左→右分层)。
// 血缘端点 GET /api/v1/datasets/{id}/lineage 返回 nodes+edges;前端用最长路径分层 +
// 固定网格坐标渲染节点卡片 + SVG 贝塞尔边(自研,不引图库——血缘通常是小图)。
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Card,
  Col,
  Empty,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useState } from 'react';
import {
  getDataset,
  getDatasetLineage,
  listDatasets,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';

const JOB_TYPE_LABEL: Record<string, string> = {
  process: '数据加工',
  clean: '清洗',
  distillation: '数据蒸馏',
  synthesis: '数据合成',
  augmentation: '数据增强',
  quality: '质量评估',
  review: '内容安全',
  ingest: '采集',
  annotate: '标注',
};

const STATE_TEXT: Record<string, { t: string; c: string }> = {
  success: { t: '成功', c: 'success' },
  failed: { t: '失败', c: 'error' },
  running: { t: '运行中', c: 'processing' },
  pending: { t: '待运行', c: 'default' },
  paused: { t: '已暂停', c: 'gold' },
  cancelled: { t: '已取消', c: 'warning' },
};

// 固定网格:每个节点占一格;坐标可算,无需 DOM 测量
const CELL_W = 320;
const CELL_H = 150;
const PAD = 10;

const scanTag = (v?: string) =>
  v === 'passed'
    ? { c: 'green', t: '安全通过' }
    : v === 'failed'
      ? { c: 'red', t: '安全未过' }
      : { c: 'default', t: '未扫描' };

/** 算子参数序列化为可读串 */
const fmtParams = (p?: Record<string, any>) => {
  if (!p) return '';
  const keys = Object.keys(p);
  if (keys.length === 0) return '';
  return keys.map((k) => `${k}=${JSON.stringify(p[k])}`).join('  ');
};

/** 算子链芯片:每算子一个 Tag(name),Tooltip 显示参数;超 3 个折叠为 +N */
const OperatorChips: React.FC<{
  ops?: { name: string; params: Record<string, any> }[];
}> = ({ ops }) => {
  if (!ops || ops.length === 0) return null;
  const shown = ops.slice(0, 3);
  const rest = ops.length - shown.length;
  return (
    <Space size={4} wrap>
      {shown.map((o, i) => {
        const ps = fmtParams(o.params);
        return (
          <Tooltip
            key={i}
            title={
              <span style={{ whiteSpace: 'pre-wrap' }}>
                {o.name}
                {ps ? `:\n${ps}` : ':(无参数)'}
              </span>
            }
          >
            <Tag color="cyan" style={{ margin: 0, fontSize: 11 }}>
              {o.name}
            </Tag>
          </Tooltip>
        );
      })}
      {rest > 0 && <Tag style={{ margin: 0, fontSize: 11 }}>+{rest} 算子</Tag>}
    </Space>
  );
};

/** 任务节点卡(虚线框,与版本卡区分) */
const JobNode: React.FC<{ n: DataPlatform.LineageNode }> = ({ n }) => {
  const st = STATE_TEXT[n.state ?? ''] ?? { t: n.state ?? '-', c: 'default' };
  return (
    <div
      style={{
        height: '100%',
        padding: 10,
        borderRadius: 8,
        background: 'var(--ant-color-fill-quaternary)',
        border: '1px dashed var(--ant-color-border)',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        justifyContent: 'center',
      }}
    >
      <Tag color="purple" style={{ margin: 0, width: 'fit-content' }}>
        {JOB_TYPE_LABEL[n.jobType ?? ''] ?? n.jobType ?? '任务'}
      </Tag>
      <Tooltip title={n.name}>
        <Typography.Text ellipsis style={{ fontSize: 12 }}>
          {n.name}
        </Typography.Text>
      </Tooltip>
      <Tag color={st.c as any} style={{ margin: 0, width: 'fit-content' }}>
        {st.t}
      </Tag>
      <OperatorChips ops={n.operators} />
    </div>
  );
};

/** 版本节点卡(focus 数据集高亮;点开跳数据集详情) */
const VersionNode: React.FC<{ n: DataPlatform.LineageNode }> = ({ n }) => {
  const sc = scanTag(n.scanVerdict);
  return (
    <div
      onClick={() =>
        n.datasetId && history.push(`/datasets/${n.datasetId}?version=${n.id}`)
      }
      style={{
        height: '100%',
        padding: 10,
        borderRadius: 8,
        cursor: 'pointer',
        background: 'var(--ant-color-bg-container)',
        border: `1.5px solid ${
          n.isFocus ? 'var(--ant-color-primary)' : 'var(--ant-color-border)'
        }`,
        boxShadow: n.isFocus ? '0 0 0 3px var(--ant-color-primary-bg)' : 'none',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <Typography.Text strong ellipsis style={{ fontSize: 13 }}>
        {n.isOriginal ? '🌱 ' : ''}
        {n.datasetName}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {n.versionLabel} · {n.rows ?? '-'} 行
      </Typography.Text>
      <Space size={4} wrap>
        {n.origin && n.origin !== 'managed' && (
          <Tag color="gold" style={{ margin: 0 }}>
            {n.origin}
          </Tag>
        )}
        <Tag color={sc.c as any} style={{ margin: 0 }}>
          {sc.t}
        </Tag>
        {n.publishStatus === 'published' && (
          <Tag color="blue" style={{ margin: 0 }}>
            已发布
          </Tag>
        )}
      </Space>
    </div>
  );
};

/** 血缘图:最长路径分层 + 固定网格坐标 + SVG 贝塞尔边 */
const LineageGraph: React.FC<{ graph?: DataPlatform.LineageGraph }> = ({
  graph,
}) => {
  if (!graph || graph.nodes.length === 0) {
    return <Empty description="无血缘数据（该数据集无版本或无加工任务）" />;
  }
  const { nodes, edges } = graph;

  const incoming = new Map<string, string[]>();
  nodes.forEach((n) => incoming.set(n.id, []));
  edges.forEach((e) => incoming.get(e.to)?.push(e.from));

  // 最长路径分层:入度 0 → 0;其余 = max(前驱层)+1;迭代到稳定
  const layer = new Map<string, number>();
  nodes.forEach((n) => {
    if ((incoming.get(n.id) ?? []).length === 0) layer.set(n.id, 0);
  });
  let changed = true;
  let guard = nodes.length + 5;
  while (changed && guard-- > 0) {
    changed = false;
    nodes.forEach((n) => {
      const preds = incoming.get(n.id) ?? [];
      if (preds.length === 0) return;
      const pl = Math.max(...preds.map((p) => layer.get(p) ?? -1));
      if ((layer.get(n.id) ?? -1) < pl + 1) {
        layer.set(n.id, pl + 1);
        changed = true;
      }
    });
  }
  nodes.forEach((n) => {
    if (!layer.has(n.id)) layer.set(n.id, 0);
  });

  // 每层排序(focus 版本靠上,再按 createdAt)分配行号
  const byLayer = new Map<number, DataPlatform.LineageNode[]>();
  layer.forEach((l, id) => {
    const node = nodes.find((n) => n.id === id)!;
    (byLayer.get(l) ?? byLayer.set(l, []).get(l)!).push(node);
  });
  const rowOf = new Map<string, number>();
  byLayer.forEach((list) => {
    list.sort((a, b) => {
      const fa = a.kind === 'version' && a.isFocus ? 0 : 1;
      const fb = b.kind === 'version' && b.isFocus ? 0 : 1;
      if (fa !== fb) return fa - fb;
      return (a.createdAt || '').localeCompare(b.createdAt || '');
    });
    list.forEach((n, i) => rowOf.set(n.id, i));
  });

  const maxLayer = Math.max(...Array.from(layer.values()));
  const maxRow = Math.max(...Array.from(rowOf.values()), 0);
  const width = (maxLayer + 1) * CELL_W;
  const height = (maxRow + 1) * CELL_H;

  const edgePath = (e: DataPlatform.LineageEdge) => {
    const sx = (layer.get(e.from) ?? 0) * CELL_W + CELL_W - PAD;
    const sy = (rowOf.get(e.from) ?? 0) * CELL_H + CELL_H / 2;
    const tx = (layer.get(e.to) ?? 0) * CELL_W + PAD;
    const ty = (rowOf.get(e.to) ?? 0) * CELL_H + CELL_H / 2;
    const dx = Math.max(40, (tx - sx) / 2);
    return `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;
  };

  return (
    <div style={{ position: 'relative', width, height, minWidth: '100%' }}>
      <svg
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width,
          height,
          pointerEvents: 'none',
        }}
      >
        <defs>
          <marker
            id="lin-arrow"
            markerWidth="8"
            markerHeight="8"
            refX="6"
            refY="3"
            orient="auto"
          >
            <path d="M0,0 L6,3 L0,6 Z" fill="var(--ant-color-border)" />
          </marker>
        </defs>
        {edges.map((e, i) => (
          <path
            key={i}
            d={edgePath(e)}
            fill="none"
            stroke="var(--ant-color-border)"
            strokeWidth={1.5}
            markerEnd="url(#lin-arrow)"
          />
        ))}
      </svg>
      {nodes.map((n) => (
        <div
          key={n.id}
          style={{
            position: 'absolute',
            left: (layer.get(n.id) ?? 0) * CELL_W + PAD,
            top: (rowOf.get(n.id) ?? 0) * CELL_H + PAD,
            width: CELL_W - 2 * PAD,
            height: CELL_H - 2 * PAD,
          }}
        >
          {n.kind === 'version' ? <VersionNode n={n} /> : <JobNode n={n} />}
        </div>
      ))}
    </div>
  );
};

const Lineage: React.FC = () => {
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [datasetId, setDatasetId] = useState<string>();
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [graph, setGraph] = useState<DataPlatform.LineageGraph>();
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 200 })
      .then((res) => {
        const list = res.data ?? [];
        setDatasets(list);
        if (list.length) setDatasetId((cur) => cur ?? list[0].id);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!datasetId) return;
    setLoading(true);
    Promise.all([
      getDataset(datasetId).catch(() => undefined),
      getDatasetLineage(datasetId).catch(() => undefined),
    ]).then(([d, g]) => {
      setDetail(d?.data);
      setGraph(g?.data);
      setLoading(false);
    });
  }, [datasetId]);

  // 版本管理卡要显示"经什么任务 + 算子":从血缘图的 job 节点按 id 取
  const jobById = new Map<string, DataPlatform.LineageNode>(
    (graph?.nodes ?? []).filter((n) => n.kind === 'job').map((n) => [n.id, n]),
  );

  return (
    <PageContainer
      header={{ title: '数据血缘', breadcrumb: {} }}
      content={
        <Space>
          <Typography.Text type="secondary">数据集</Typography.Text>
          <Select
            showSearch
            style={{ width: 380 }}
            placeholder="选择数据集查看其版本与血缘"
            value={datasetId}
            onChange={setDatasetId}
            options={datasets.map((d) => ({ label: d.name, value: d.id }))}
            filterOption={(input, option) =>
              ((option?.label as string) ?? '')
                .toLowerCase()
                .includes(input.toLowerCase())
            }
          />
        </Space>
      }
    >
      <Row gutter={12}>
        <Col xs={24} md={6}>
          <Card
            size="small"
            title={`版本管理（${detail?.versions.length ?? 0}）`}
            styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}
          >
            {(detail?.versions ?? []).map((v) => {
              const sc = scanTag(v.scanVerdict);
              const job = v.producedByJobId
                ? jobById.get(v.producedByJobId)
                : undefined;
              return (
                <div
                  key={v.id}
                  onClick={() =>
                    history.push(`/datasets/${v.datasetId}?version=${v.id}`)
                  }
                  style={{
                    cursor: 'pointer',
                    padding: '8px 10px',
                    marginBottom: 8,
                    borderRadius: 6,
                    border: '1px solid var(--ant-color-border)',
                    background:
                      v.producedByJobId == null
                        ? 'var(--ant-color-fill-quaternary)'
                        : 'var(--ant-color-bg-container)',
                  }}
                >
                  <Typography.Text strong style={{ fontSize: 13 }}>
                    {v.versionLabel}
                  </Typography.Text>
                  {v.producedByJobId == null && (
                    <Tag color="green" style={{ marginInlineStart: 6 }}>
                      原始
                    </Tag>
                  )}
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--ant-color-text-secondary)',
                    }}
                  >
                    {v.rows ?? '-'} 行 · {formatDateTime(v.createdAt)}
                  </div>
                  <Space size={4} wrap style={{ marginTop: 4 }}>
                    {v.origin && v.origin !== 'managed' && (
                      <Tag color="gold" style={{ margin: 0 }}>
                        {v.origin}
                      </Tag>
                    )}
                    <Tag color={sc.c as any} style={{ margin: 0 }}>
                      {sc.t}
                    </Tag>
                    {v.publishStatus === 'published' && (
                      <Tag color="blue" style={{ margin: 0 }}>
                        已发布
                      </Tag>
                    )}
                  </Space>
                  {job && (
                    <div
                      style={{
                        marginTop: 6,
                        borderTop: '1px dashed var(--ant-color-border)',
                        paddingTop: 6,
                      }}
                    >
                      <Typography.Text
                        type="secondary"
                        style={{ fontSize: 11 }}
                      >
                        经{' '}
                        {JOB_TYPE_LABEL[job.jobType ?? ''] ??
                          job.jobType ??
                          '任务'}
                      </Typography.Text>
                      <div style={{ marginTop: 4 }}>
                        <OperatorChips ops={job.operators} />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            {detail && detail.versions.length === 0 && (
              <Empty description="无版本" />
            )}
          </Card>
        </Col>
        <Col xs={24} md={18}>
          <Card
            size="small"
            title="血缘关系图（左 → 右：上游版本 → 任务 → 产出版本）"
            styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}
          >
            <Spin spinning={loading}>
              <LineageGraph graph={graph} />
            </Spin>
          </Card>
        </Col>
      </Row>
    </PageContainer>
  );
};

export default Lineage;
