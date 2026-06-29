// 数据血缘:数据集版本管理(左)+ 血缘关系图(右,版本↔任务 DAG)。
// 血缘端点 GET /api/v1/datasets/{id}/lineage 返回 nodes+edges;用 ReactFlow(@xyflow/react)
// 渲染 + dagre 算上→下树形布局(rankdir=TB),节点复用 antd 版本/任务卡片,自带平移/缩放/自适应。
import { PageContainer } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from '@dagrejs/dagre';
import {
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  getDataset,
  getDatasetLineage,
  listDatasets,
} from '@/services/data-platform';
import { formatDateTime } from '@/utils/format';
import DatasetPicker from './components/DatasetPicker';

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

// dagre 布局用的节点尺寸。每次须返回**新对象**——dagre layout 会往传入的 label 对象上
// 写 x/y,若共享单例,同类型节点会共用同一个位置对象 → 全部叠到同一坐标。
const sizeOf = (n: DataPlatform.LineageNode) =>
  n.kind === 'job' ? { width: 250, height: 178 } : { width: 250, height: 132 };

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
        overflow: 'hidden',
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
        overflow: 'hidden',
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

/** 自定义 ReactFlow 节点:复用 antd 版本/任务卡片 + 隐藏 Handle(顶 target/底 source)
 *  没有 Handle ReactFlow 建不出边(error #008);隐藏(opacity:0)保持卡片整洁。 */
const HANDLE_STYLE = { opacity: 0 } as const;
const RFVersionNode = ({ data }: NodeProps) => (
  <>
    <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
    <VersionNode n={data as DataPlatform.LineageNode} />
    <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
  </>
);
const RFJobNode = ({ data }: NodeProps) => (
  <>
    <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
    <JobNode n={data as DataPlatform.LineageNode} />
    <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
  </>
);

/** 血缘图:ReactFlow + dagre(上→下树形布局 rankdir=TB),节点为 antd 卡片,自带平移/缩放/自适应 */
const LineageGraph: React.FC<{ graph?: DataPlatform.LineageGraph }> = ({
  graph,
}) => {
  const nodeTypes = useMemo(
    () => ({ version: RFVersionNode, job: RFJobNode }),
    [],
  );
  if (!graph || graph.nodes.length === 0) {
    return <Empty description="无血缘数据（该数据集无版本或无加工任务）" />;
  }

  // dagre 算上→下布局,产出 ReactFlow 节点(含 position)。dagre 的 x/y 是节点中心,
  // ReactFlow 要左上角,故各减半尺寸。
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    rankdir: 'TB',
    nodesep: 32,
    ranksep: 72,
    marginx: 16,
    marginy: 16,
  });
  graph.nodes.forEach((n) => g.setNode(n.id, sizeOf(n)));
  graph.edges.forEach((e) => g.setEdge(e.from, e.to));
  dagre.layout(g);

  const rfNodes: Node[] = graph.nodes.map((n) => {
    const p = g.node(n.id);
    const s = sizeOf(n);
    return {
      id: n.id,
      type: n.kind,
      position: { x: p.x - s.width / 2, y: p.y - s.height / 2 },
      data: n as unknown as Record<string, unknown>,
      style: { width: s.width, height: s.height },
    };
  });
  const rfEdges: Edge[] = graph.edges.map((e) => ({
    id: `${e.from}->${e.to}`,
    source: e.from,
    target: e.to,
    type: 'smoothstep',
    // ReactFlow 边默认无箭头,显式加箭头标记
    markerEnd: { type: MarkerType.ArrowClosed },
  }));

  return (
    <div style={{ width: '100%', height: '72vh' }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.3}
        fitViewOptions={{ padding: 0.12, minZoom: 0.3 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
};

const Lineage: React.FC = () => {
  const [datasetId, setDatasetId] = useState<string>();
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [graph, setGraph] = useState<DataPlatform.LineageGraph>();
  const [loading, setLoading] = useState(false);

  // 首次进入自动选第一个数据集(轻量拉一页 1 条,避免旧版一次拉 200 条)
  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1 })
      .then((res) => {
        const first = res.data?.[0];
        if (first) setDatasetId((cur) => cur ?? first.id);
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
        <Space size={8} align="center">
          <Typography.Text type="secondary">数据集</Typography.Text>
          <DatasetPicker value={datasetId} onChange={setDatasetId} />
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
            title="血缘关系图（上 → 下：上游版本 → 任务 → 产出版本）"
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
