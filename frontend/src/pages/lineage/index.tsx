// 数据血缘:数据集版本管理(左)+ 血缘关系图(右,数据源→湖快照→版本↔任务全链路 DAG)。
// 血缘端点 GET /api/v1/datasets/{id}/lineage 返回 nodes+edges;用 ReactFlow(@xyflow/react)
// 渲染 + dagre 算上→下树形布局(rankdir=TB),节点复用 antd 卡片,自带平移/缩放/自适应。
// 节点四层:datasource(数据源)→ lake_snapshot(湖快照)→ version(数据集版本)↔ job(任务);
// 支持 ?datasetId= 直达(数据集详情/数据湖详情"查看血缘"入口跳转)。
// 多表版本:version 节点展示成员表清单及各自湖溯源;job 节点按成员分组展示算子链。
import { PageContainer } from '@ant-design/pro-components';
import { history, useSearchParams } from '@umijs/max';
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
import { JobOps } from './components/OperatorChips';

const JOB_TYPE_LABEL: Record<string, string> = {
  clean: '清洗',
  distillation: '数据蒸馏',
  synthesis: '数据合并',
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

// 分层配色:节点卡左侧色带 + 图例共用,标识节点属于链路的哪一层。
const KIND_META: Record<string, { label: string; color: string }> = {
  datasource: { label: '数据源', color: '#1677ff' },
  lake_snapshot: { label: '湖快照', color: '#13c2c2' },
  version: { label: '数据集版本', color: '#52c41a' },
  job: { label: '加工任务', color: '#fa8c16' },
  member: { label: '成员', color: '#9254de' },
};

// 湖/源层边的中文标签(input/output 版本↔任务边保持无标签,与旧版一致)
const EDGE_LABEL: Record<string, string> = {
  extract: '抽取',
  ingest: '采集',
  merge: '合并',
  hosted_source: '直连',
  contains: '包含',
};

// dagre 布局用的节点尺寸。每次须返回**新对象**——dagre layout 会往传入的 label 对象上
// 写 x/y,若共享单例,同类型节点会共用同一个位置对象 → 全部叠到同一坐标。
const sizeOf = (n: DataPlatform.LineageNode) => {
  if (n.kind === 'job') {
    const memberCount = n.memberOperators?.length ?? 0;
    if (memberCount > 1) {
      return {
        width: 250,
        height:
          178 + Math.min(memberCount, 2) * 26 + (memberCount > 2 ? 22 : 0),
      };
    }
    return { width: 250, height: 178 };
  }
  if (n.kind === 'datasource') return { width: 250, height: 96 };
  if (n.kind === 'member') return { width: 250, height: 108 };
  return { width: 250, height: 132 };
};

const scanTag = (v?: string) =>
  v === 'passed'
    ? { c: 'green', t: '安全通过' }
    : v === 'failed'
      ? { c: 'red', t: '安全未过' }
      : { c: 'default', t: '未扫描' };

/** 数据源节点卡(链路最上游:外部数据源连接) */
const DatasourceNode: React.FC<{ n: DataPlatform.LineageNode }> = ({ n }) => (
  <div
    style={{
      height: '100%',
      padding: 10,
      borderRadius: 8,
      background: 'var(--ant-color-bg-container)',
      border: '1px solid var(--ant-color-border)',
      borderLeft: `3px solid ${KIND_META.datasource.color}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      justifyContent: 'center',
      overflow: 'hidden',
    }}
  >
    <Tag color="blue" style={{ margin: 0, width: 'fit-content' }}>
      数据源
    </Tag>
    <Tooltip title={n.name}>
      <Typography.Text strong ellipsis style={{ fontSize: 13 }}>
        {n.name}
      </Typography.Text>
    </Tooltip>
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
      {n.sourceType ?? '-'}
      {n.dbKind ? ` · ${n.dbKind}` : ''}
    </Typography.Text>
  </div>
);

/** 湖快照节点卡(湖对象@第 n 版 + 溯源摘要);点击跳数据湖详情 */
const SnapshotNode: React.FC<{ n: DataPlatform.LineageNode }> = ({ n }) => (
  <div
    onClick={() => n.lakeId && history.push(`/data-lakes/${n.lakeId}`)}
    style={{
      height: '100%',
      padding: 10,
      borderRadius: 8,
      cursor: n.lakeId ? 'pointer' : 'default',
      background: 'var(--ant-color-bg-container)',
      border: '1px solid var(--ant-color-border)',
      borderLeft: `3px solid ${KIND_META.lake_snapshot.color}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      overflow: 'hidden',
    }}
  >
    <Tooltip title={`${n.name ?? ''}(湖 ${n.lakeName ?? n.lakeId ?? '-'})`}>
      <Typography.Text strong ellipsis style={{ fontSize: 13 }}>
        {n.name}
        {n.versionNo != null ? ` @v${n.versionNo}` : ''}
      </Typography.Text>
    </Tooltip>
    <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>
      湖 {n.lakeName ?? n.lakeId ?? '-'}
      {n.rows != null ? ` · ${n.rows} 行` : ''}
    </Typography.Text>
    {n.sourceSummary && (
      <Typography.Text type="secondary" ellipsis style={{ fontSize: 11 }}>
        {n.sourceSummary}
      </Typography.Text>
    )}
    <Space size={4} wrap>
      <Tag color="cyan" style={{ margin: 0, fontSize: 11 }}>
        {n.uploadChannel ?? n.dataCategory ?? '快照'}
      </Tag>
      {n.ingestTaskName && (
        <Tooltip title={`采集任务:${n.ingestTaskName}`}>
          <Tag style={{ margin: 0, fontSize: 11 }}>{n.ingestTaskName}</Tag>
        </Tooltip>
      )}
    </Space>
  </div>
);

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
        borderLeft: `3px solid ${KIND_META.job.color}`,
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
      <JobOps n={n} />
    </div>
  );
};

/** 版本节点卡(focus 数据集高亮;点开跳数据集详情;成员 Tag 可点击展开/折叠为
 *  member 子节点,展开态由 LineageGraph 经 node.data 注入) */
const VersionNode: React.FC<{
  n: DataPlatform.LineageNode;
  expanded?: boolean;
  onToggleMembers?: () => void;
}> = ({ n, expanded, onToggleMembers }) => {
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
        borderLeft: `3px solid ${
          n.isFocus ? 'var(--ant-color-primary)' : KIND_META.version.color
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
        {n.members &&
          (n.members.length > 1 ||
            n.members.some((m) => m.sourceSnapshotId)) && (
            <Tooltip
              title={
                onToggleMembers ? (
                  expanded ? (
                    '点击折叠成员节点'
                  ) : (
                    '点击展开为成员节点'
                  )
                ) : (
                  <span style={{ whiteSpace: 'pre-wrap' }}>
                    {n.members
                      .map(
                        (m) =>
                          `${m.tableName} · ${m.rows ?? '-'} 行 · 来源: ${
                            m.sourceName ??
                            m.sourceUploadChannel ??
                            '本任务加工'
                          }`,
                      )
                      .join('\n')}
                  </span>
                )
              }
            >
              <Tag
                style={{
                  margin: 0,
                  fontSize: 11,
                  cursor: onToggleMembers ? 'pointer' : 'default',
                }}
                onClick={(e) => {
                  if (!onToggleMembers) return;
                  e.stopPropagation();
                  onToggleMembers();
                }}
              >
                {expanded ? '收起' : ''}
                {n.members.length} 成员
                {onToggleMembers ? (expanded ? ' ▲' : ' ▼') : ''}
              </Tag>
            </Tooltip>
          )}
      </Space>
    </div>
  );
};

/** 成员节点卡(版本内某个表/文件的一等节点,由版本卡"N 成员" Tag 展开产生) */
const MemberNode: React.FC<{ n: DataPlatform.LineageNode }> = ({ n }) => (
  <div
    style={{
      height: '100%',
      padding: 10,
      borderRadius: 8,
      background: 'var(--ant-color-bg-container)',
      border: '1px solid var(--ant-color-border)',
      borderLeft: `3px solid ${KIND_META.member.color}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      justifyContent: 'center',
      overflow: 'hidden',
    }}
  >
    <Tag color="purple" style={{ margin: 0, width: 'fit-content' }}>
      成员
    </Tag>
    <Tooltip title={n.tableName}>
      <Typography.Text strong ellipsis style={{ fontSize: 13 }}>
        {n.tableName}
      </Typography.Text>
    </Tooltip>
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
      {n.rows ?? '-'} 行{n.sourceKind ? ` · ${n.sourceKind}` : ''}
    </Typography.Text>
    {(n.sourceName || n.sourceUploadChannel) && (
      <Typography.Text type="secondary" ellipsis style={{ fontSize: 11 }}>
        来源:{n.sourceName ?? n.sourceUploadChannel}
      </Typography.Text>
    )}
  </div>
);

/** 自定义 ReactFlow 节点:复用 antd 版本/任务卡片 + 隐藏 Handle(顶 target/底 source)
 *  没有 Handle ReactFlow 建不出边(error #008);隐藏(opacity:0)保持卡片整洁。 */
const HANDLE_STYLE = { opacity: 0 } as const;
// version 节点的 data 额外挂 expanded/onToggleMembers(见 LineageGraph 的 rfNodes 构造),
// 驱动"N 成员" Tag 的展开/折叠交互。
type VersionNodeData = DataPlatform.LineageNode & {
  expanded?: boolean;
  onToggleMembers?: () => void;
};
const RFVersionNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <VersionNode
        n={d}
        expanded={d.expanded}
        onToggleMembers={d.onToggleMembers}
      />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </>
  );
};
const RFJobNode = ({ data }: NodeProps) => (
  <>
    <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
    <JobNode n={data as DataPlatform.LineageNode} />
    <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
  </>
);
const RFSnapshotNode = ({ data }: NodeProps) => (
  <>
    <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
    <SnapshotNode n={data as DataPlatform.LineageNode} />
    <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
  </>
);
const RFDatasourceNode = ({ data }: NodeProps) => (
  <>
    <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
    <DatasourceNode n={data as DataPlatform.LineageNode} />
    <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
  </>
);
const RFMemberNode = ({ data }: NodeProps) => (
  <>
    <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
    <MemberNode n={data as DataPlatform.LineageNode} />
    <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
  </>
);

/** 版本节点 id → 展开为 member 子节点后新增的节点/边(治理整改 P1-②阶段4):
 *  纯本地渲染,复用 version 节点已带的 members[] payload,不额外请求接口。
 *  extract 边源从 snapshot→version 改挂到 snapshot→member,version→member 补 contains 边。 */
const expandMembers = (
  graph: DataPlatform.LineageGraph,
  expanded: Set<string>,
): DataPlatform.LineageGraph => {
  if (expanded.size === 0) return graph;
  const extraNodes: DataPlatform.LineageNode[] = [];
  const extraEdges: DataPlatform.LineageEdge[] = [];
  const hiddenExtract = new Set<string>(); // `${from}->${to}`,已改挂到 member 的粗粒度 extract 边

  for (const n of graph.nodes) {
    if (n.kind !== 'version' || !expanded.has(n.id) || !n.members?.length) {
      continue;
    }
    for (const m of n.members) {
      const mid = `member:${n.id}:${m.tableName}`;
      extraNodes.push({
        id: mid,
        kind: 'member',
        versionId: n.id,
        tableName: m.tableName,
        rows: m.rows ?? undefined,
        sourceSnapshotId: m.sourceSnapshotId,
        sourceName: m.sourceName,
        sourceUploadChannel: m.sourceUploadChannel,
        sourceKind: m.sourceKind,
      });
      extraEdges.push({ from: n.id, to: mid, kind: 'contains' });
      if (m.sourceSnapshotId) {
        extraEdges.push({ from: m.sourceSnapshotId, to: mid, kind: 'extract' });
        hiddenExtract.add(`${m.sourceSnapshotId}->${n.id}`);
      }
    }
  }
  return {
    nodes: [...graph.nodes, ...extraNodes],
    edges: [
      ...graph.edges.filter(
        (e) =>
          !(e.kind === 'extract' && hiddenExtract.has(`${e.from}->${e.to}`)),
      ),
      ...extraEdges,
    ],
  };
};

/** 血缘图:ReactFlow + dagre(上→下树形布局 rankdir=TB),节点为 antd 卡片,自带平移/缩放/自适应 */
const LineageGraph: React.FC<{ graph?: DataPlatform.LineageGraph }> = ({
  graph,
}) => {
  const nodeTypes = useMemo(
    () => ({
      version: RFVersionNode,
      job: RFJobNode,
      lake_snapshot: RFSnapshotNode,
      datasource: RFDatasourceNode,
      member: RFMemberNode,
    }),
    [],
  );
  // 已展开为 member 子节点的版本 id 集合(点击版本卡"N 成员" Tag 切换)
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleMembers = (vid: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(vid)) {
        next.delete(vid);
      } else {
        next.add(vid);
      }
      return next;
    });
  const effectiveGraph = useMemo(
    () => (graph ? expandMembers(graph, expanded) : undefined),
    [graph, expanded],
  );
  if (!graph || graph.nodes.length === 0 || !effectiveGraph) {
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
  effectiveGraph.nodes.forEach((n) => {
    g.setNode(n.id, sizeOf(n));
  });
  effectiveGraph.edges.forEach((e) => {
    g.setEdge(e.from, e.to);
  });
  dagre.layout(g);

  const rfNodes: Node[] = effectiveGraph.nodes.map((n) => {
    const p = g.node(n.id);
    const s = sizeOf(n);
    const data: Record<string, unknown> = { ...n };
    if (n.kind === 'version') {
      data.expanded = expanded.has(n.id);
      data.onToggleMembers = () => toggleMembers(n.id);
    }
    return {
      id: n.id,
      type: n.kind,
      position: { x: p.x - s.width / 2, y: p.y - s.height / 2 },
      data,
      style: { width: s.width, height: s.height },
    };
  });
  const rfEdges: Edge[] = effectiveGraph.edges.map((e) => ({
    id: `${e.from}->${e.to}->${e.kind}`,
    source: e.from,
    target: e.to,
    type: 'smoothstep',
    // 湖/源层边带中文标签 + 虚线,与版本↔任务实线边区分
    label: EDGE_LABEL[e.kind],
    style: EDGE_LABEL[e.kind] ? { strokeDasharray: '6 3' } : undefined,
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
  // 支持 ?datasetId= 直达(数据集详情/数据湖详情"查看血缘"入口带参跳转)
  const [searchParams] = useSearchParams();
  const [datasetId, setDatasetId] = useState<string | undefined>(
    () => searchParams.get('datasetId') ?? undefined,
  );
  const [detail, setDetail] = useState<DataPlatform.DatasetDetail>();
  const [graph, setGraph] = useState<DataPlatform.LineageGraph>();
  const [loading, setLoading] = useState(false);

  // 首次进入自动选第一个数据集(轻量拉一页 1 条,避免旧版一次拉 200 条);
  // URL 已带 datasetId 时 cur 非空,不会被覆盖。
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
                        <JobOps n={job} />
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
            title="血缘关系图（上 → 下：数据源 → 湖快照 → 版本 → 任务 → 产出版本）"
            extra={
              <Space size={8} wrap>
                {Object.entries(KIND_META).map(([k, m]) => (
                  <Space key={k} size={4} align="center">
                    <span
                      style={{
                        display: 'inline-block',
                        width: 10,
                        height: 10,
                        borderRadius: 2,
                        background: m.color,
                      }}
                    />
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {m.label}
                    </Typography.Text>
                  </Space>
                ))}
              </Space>
            }
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
