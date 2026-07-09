// 数据血缘:全景森林视图(治理整改 P2)。默认不强制先选数据集——进页即渲染全局
// 血缘森林(数据源→湖快照→数据集版本→加工任务),顶部按 湖/类型/时间 过滤;
// 点任意节点卡右上角「聚焦」小图标,换成以该节点为根的下钻视图(returns 全景入口)。
// 渲染沿用原实现:ReactFlow(@xyflow/react)+ dagre 布局(rankdir=TB),节点复用
// antd 卡片,自带平移/缩放/自适应。节点五类:datasource/lake_snapshot/version/job
// (全景森林覆盖)+ member(仅版本卡"N 成员" Tag 本地展开产生,不在全景播种范围)。
// 数据集聚焦(?datasetId= 或顶部「跳转到数据集」)走独立的 GET /datasets/{id}/lineage
// (多版本 DAG),其余聚焦(source/lake_snapshot/dataset_version/job/member)走
// entity-agnostic 的 GET /lineage?kind=。
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
import { AimOutlined } from '@ant-design/icons';
import dagre from '@dagrejs/dagre';
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Empty,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import type { Dayjs } from 'dayjs';
import { useEffect, useMemo, useState } from 'react';
import {
  getDataset,
  getDatasetLineage,
  getLineageByAnchor,
  getPanoramaLineage,
  listDataLakes,
} from '@/services/data-platform';
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

// 全景过滤栏「类型」多选项:member 仅本地展开产生,全景播种不覆盖,不作为过滤项。
const KIND_FILTER_OPTIONS = Object.entries(KIND_META)
  .filter(([k]) => k !== 'member')
  .map(([value, m]) => ({ value, label: m.label }));

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

/** 节点卡右上角「聚焦」小图标:点任意节点下钻的统一入口,stopPropagation 避免
 *  与卡片体既有的跳转导航(版本→数据集详情、快照→湖详情)冲突。 */
const FocusButton: React.FC<{ onFocus?: () => void }> = ({ onFocus }) => {
  if (!onFocus) return null;
  return (
    <Tooltip title="聚焦此节点(下钻查看以它为根的血缘)">
      <Button
        type="text"
        size="small"
        icon={<AimOutlined style={{ fontSize: 12 }} />}
        onClick={(e) => {
          e.stopPropagation();
          onFocus();
        }}
        style={{
          position: 'absolute',
          top: 2,
          right: 2,
          width: 20,
          height: 20,
          minWidth: 20,
          padding: 0,
          lineHeight: '20px',
          zIndex: 1,
        }}
      />
    </Tooltip>
  );
};

/** 数据源节点卡(链路最上游:外部数据源连接) */
const DatasourceNode: React.FC<{
  n: DataPlatform.LineageNode;
  onFocus?: () => void;
}> = ({ n, onFocus }) => (
  <div
    style={{
      position: 'relative',
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
    <FocusButton onFocus={onFocus} />
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
const SnapshotNode: React.FC<{
  n: DataPlatform.LineageNode;
  onFocus?: () => void;
}> = ({ n, onFocus }) => (
  <div
    onClick={() => n.lakeId && history.push(`/data-lakes/${n.lakeId}`)}
    style={{
      position: 'relative',
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
    <FocusButton onFocus={onFocus} />
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
const JobNode: React.FC<{
  n: DataPlatform.LineageNode;
  onFocus?: () => void;
}> = ({ n, onFocus }) => {
  const st = STATE_TEXT[n.state ?? ''] ?? { t: n.state ?? '-', c: 'default' };
  return (
    <div
      style={{
        position: 'relative',
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
      <FocusButton onFocus={onFocus} />
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
  onFocus?: () => void;
}> = ({ n, expanded, onToggleMembers, onFocus }) => {
  const sc = scanTag(n.scanVerdict);
  return (
    <div
      onClick={() =>
        n.datasetId && history.push(`/datasets/${n.datasetId}?version=${n.id}`)
      }
      style={{
        position: 'relative',
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
      <FocusButton onFocus={onFocus} />
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
const MemberNode: React.FC<{
  n: DataPlatform.LineageNode;
  onFocus?: () => void;
}> = ({ n, onFocus }) => (
  <div
    style={{
      position: 'relative',
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
    <FocusButton onFocus={onFocus} />
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
// 驱动"N 成员" Tag 的展开/折叠交互;所有节点类型均挂 onFocus,驱动右上角聚焦按钮。
type VersionNodeData = DataPlatform.LineageNode & {
  expanded?: boolean;
  onToggleMembers?: () => void;
  onFocus?: () => void;
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
        onFocus={d.onFocus}
      />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </>
  );
};
const RFJobNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <JobNode n={d} onFocus={d.onFocus} />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </>
  );
};
const RFSnapshotNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <SnapshotNode n={d} onFocus={d.onFocus} />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </>
  );
};
const RFDatasourceNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <DatasourceNode n={d} onFocus={d.onFocus} />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </>
  );
};
const RFMemberNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <MemberNode n={d} onFocus={d.onFocus} />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
    </>
  );
};

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
const LineageGraph: React.FC<{
  graph?: DataPlatform.LineageGraph;
  onFocusNode?: (n: DataPlatform.LineageNode) => void;
}> = ({ graph, onFocusNode }) => {
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
    return <Empty description="无血缘数据（当前过滤范围内没有节点）" />;
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
    if (onFocusNode) {
      data.onFocus = () => onFocusNode(n);
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

// 聚焦锚点:版本/任务/快照/数据源/成员用 entity-agnostic GET /lineage?kind=;
// dataset 用专门的 GET /datasets/{id}/lineage(多版本 DAG,?datasetId= 直达入口
// 与顶部「跳转到数据集」共用此分支,保留原有整数据集视角能力)。
type FocusAnchor =
  | { kind: 'dataset_version'; id: string }
  | { kind: 'job'; id: string }
  | { kind: 'lake_snapshot'; id: string }
  | { kind: 'source'; id: string }
  | { kind: 'member'; versionId: string; tableName: string }
  | { kind: 'dataset'; id: string };

/** 节点 → 聚焦下钻展示用的简短标签 */
const nodeLabel = (n?: DataPlatform.LineageNode): string => {
  if (!n) return '';
  switch (n.kind) {
    case 'version':
      return `${n.datasetName ?? ''}${n.versionLabel ? ` · ${n.versionLabel}` : ''}`;
    case 'job':
      return n.name ?? '任务';
    case 'lake_snapshot':
      return `${n.name ?? '快照'}${n.versionNo != null ? ` @v${n.versionNo}` : ''}`;
    case 'datasource':
      return n.name ?? '数据源';
    case 'member':
      return n.tableName ?? '成员';
    default:
      return n.name ?? n.id;
  }
};

const Lineage: React.FC = () => {
  // ?datasetId=(数据集/数据湖详情"查看血缘"旧入口)直接进入数据集聚焦态;
  // ?lakeId=(数据湖详情"查看血缘"新入口)预填全景态的湖过滤。
  const [searchParams] = useSearchParams();
  const [filterLakeId, setFilterLakeId] = useState<string | undefined>(
    () => searchParams.get('lakeId') ?? undefined,
  );
  const [filterKinds, setFilterKinds] = useState<string[]>([]);
  const [sinceDate, setSinceDate] = useState<Dayjs | null>(null);
  const [focusAnchor, setFocusAnchor] = useState<FocusAnchor | undefined>(
    () => {
      const did = searchParams.get('datasetId');
      return did ? { kind: 'dataset', id: did } : undefined;
    },
  );
  const [focusLabel, setFocusLabel] = useState('');
  const [panoramaGraph, setPanoramaGraph] =
    useState<DataPlatform.LineageGraph>();
  const [focusGraph, setFocusGraph] = useState<DataPlatform.LineageGraph>();
  const [loading, setLoading] = useState(false);
  const [lakeOptions, setLakeOptions] = useState<
    { value: string; label: string }[]
  >([]);

  // 湖过滤下拉的选项:一次性拉够(治理场景湖数量可控,量级失控需改造为异步搜索,
  // 暂未遇到不预先做)。
  useEffect(() => {
    listDataLakes({ pageSize: 200 })
      .then((res) =>
        setLakeOptions(
          (res.data ?? []).map((l) => ({ value: l.id, label: l.name })),
        ),
      )
      .catch(() => undefined);
  }, []);

  const hasFocus = !!focusAnchor;
  const sinceISO = sinceDate ? sinceDate.toISOString() : undefined;

  // 全景态:按 湖/类型/时间 过滤拉一次;聚焦态下过滤栏禁用,不重复请求。
  useEffect(() => {
    if (hasFocus) return;
    setLoading(true);
    getPanoramaLineage({
      lakeId: filterLakeId,
      kinds: filterKinds.length ? filterKinds.join(',') : undefined,
      since: sinceISO,
    })
      .then((res) => setPanoramaGraph(res.data))
      .catch(() => setPanoramaGraph(undefined))
      .finally(() => setLoading(false));
  }, [hasFocus, filterLakeId, filterKinds, sinceISO]);

  // 聚焦态:按 anchor 类型换对应的血缘查询。anchorKey 用值而非对象引用做依赖——
  // 避免下方"取到真实名称后回填 focusLabel"触发的 setFocusAnchor 造成无限重取。
  const anchorKey = !focusAnchor
    ? undefined
    : focusAnchor.kind === 'member'
      ? `member:${focusAnchor.versionId}:${focusAnchor.tableName}`
      : `${focusAnchor.kind}:${focusAnchor.id}`;

  useEffect(() => {
    if (!focusAnchor) {
      setFocusGraph(undefined);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const run = async () => {
      if (focusAnchor.kind === 'dataset') {
        const [d, g] = await Promise.all([
          getDataset(focusAnchor.id).catch(() => undefined),
          getDatasetLineage(focusAnchor.id).catch(() => undefined),
        ]);
        if (cancelled) return;
        if (d?.data?.name) setFocusLabel(d.data.name);
        setFocusGraph(g?.data);
        return;
      }
      const params =
        focusAnchor.kind === 'member'
          ? {
              kind: 'member' as const,
              versionId: focusAnchor.versionId,
              tableName: focusAnchor.tableName,
            }
          : focusAnchor.kind === 'job'
            ? { kind: 'job' as const, jobId: focusAnchor.id }
            : focusAnchor.kind === 'lake_snapshot'
              ? { kind: 'lake_snapshot' as const, snapshotId: focusAnchor.id }
              : focusAnchor.kind === 'source'
                ? { kind: 'source' as const, sourceId: focusAnchor.id }
                : {
                    kind: 'dataset_version' as const,
                    versionId: focusAnchor.id,
                  };
      const res = await getLineageByAnchor(params).catch(() => undefined);
      if (cancelled) return;
      setFocusGraph(res?.data);
    };
    run().finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorKey]);

  const focusOnNode = (n: DataPlatform.LineageNode) => {
    setFocusLabel(nodeLabel(n));
    if (n.kind === 'version') {
      setFocusAnchor({ kind: 'dataset_version', id: n.id });
    } else if (n.kind === 'job') {
      setFocusAnchor({ kind: 'job', id: n.id });
    } else if (n.kind === 'lake_snapshot') {
      setFocusAnchor({ kind: 'lake_snapshot', id: n.id });
    } else if (n.kind === 'datasource') {
      setFocusAnchor({ kind: 'source', id: n.id });
    } else if (n.kind === 'member' && n.versionId && n.tableName) {
      setFocusAnchor({
        kind: 'member',
        versionId: n.versionId,
        tableName: n.tableName,
      });
    }
  };

  return (
    <PageContainer header={{ title: '数据血缘', breadcrumb: {} }}>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space size={16} wrap>
          <Space size={6}>
            <Typography.Text type="secondary">数据湖</Typography.Text>
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="全部"
              style={{ width: 200 }}
              options={lakeOptions}
              value={filterLakeId}
              onChange={setFilterLakeId}
              disabled={hasFocus}
            />
          </Space>
          <Space size={6}>
            <Typography.Text type="secondary">类型</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              placeholder="全部"
              style={{ minWidth: 260 }}
              options={KIND_FILTER_OPTIONS}
              value={filterKinds}
              onChange={setFilterKinds}
              disabled={hasFocus}
            />
          </Space>
          <Space size={6}>
            <Typography.Text type="secondary">起始时间</Typography.Text>
            <DatePicker
              showTime
              placeholder="不限"
              value={sinceDate}
              onChange={setSinceDate}
              disabled={hasFocus}
            />
          </Space>
          <Space size={6}>
            <Typography.Text type="secondary">跳转到数据集</Typography.Text>
            <DatasetPicker
              value={
                focusAnchor?.kind === 'dataset' ? focusAnchor.id : undefined
              }
              onChange={(id) => {
                setFocusLabel('');
                setFocusAnchor({ kind: 'dataset', id });
              }}
            />
          </Space>
          {hasFocus && (
            <Button onClick={() => setFocusAnchor(undefined)}>
              ← 返回全景
            </Button>
          )}
        </Space>
      </Card>

      {hasFocus && (
        <Typography.Text
          type="secondary"
          style={{ display: 'block', marginBottom: 12 }}
        >
          当前聚焦:{focusLabel || '加载中…'}
        </Typography.Text>
      )}

      {!hasFocus && panoramaGraph?.truncated && (
        <Alert
          type="warning"
          showIcon
          closable
          message={`血缘图过大已截断（共 ${
            panoramaGraph.totalEstimated ?? '?'
          } 节点），请用 湖/类型/时间 缩小范围`}
          style={{ marginBottom: 12 }}
        />
      )}

      <Card
        size="small"
        title={
          hasFocus
            ? '聚焦视图'
            : '血缘全景森林（数据源 → 湖快照 → 数据集版本 → 加工任务）'
        }
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
        styles={{ body: { maxHeight: '74vh', overflow: 'auto' } }}
      >
        <Spin spinning={loading}>
          {(hasFocus ? focusGraph : panoramaGraph) ? (
            <LineageGraph
              graph={hasFocus ? focusGraph : panoramaGraph}
              onFocusNode={focusOnNode}
            />
          ) : loading ? (
            // 加载期只显 Spin 转圈,不渲染"无数据"空态(全景接口较慢,避免误导)
            <div style={{ height: 420 }} />
          ) : (
            <Empty description="无血缘数据（当前过滤范围内没有节点）" />
          )}
        </Spin>
      </Card>
    </PageContainer>
  );
};

export default Lineage;
