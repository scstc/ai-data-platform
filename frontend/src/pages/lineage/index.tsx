// 数据血缘:两种视图模式(顶部 Segmented 切换),默认「焦点探索」。
// ①「焦点探索」(OpenMetadata 式):顶部选定"焦点类型(数据集版本/数据源/加工任务/
//   数据湖)+ 焦点实体"后,以该实体为中心向上/下游各展开若干跳(深度可调 0~3),用
//   GET /lineage/focus 拉取子图;子图未覆盖的直接邻居数(moreUp/moreDown)驱动节点卡
//   「+N」按钮,点击调 GET /lineage/neighbors 增量合并进当前图,不整图重拉。点节点右上
//   角「聚焦」小图标可换焦点(重新整图请求);点边弹出 Drawer 看边详情,关联加工任务的
//   边内嵌 AssetManifest(算子链/参数/LLM 快照)。成员图层 Switch 控制是否把版本内的表/
//   文件展开为一等 member 节点(后端驱动)。数据湖焦点=该湖全部快照及下游数据集。
// ②「全景概览」:GET /lineage/panorama 全局血缘森林,按 湖/类型 过滤;数据湖以
//   一等节点入图(contains 边连到湖内全部快照,同湖快照被布局聚拢在湖节点周围);
//   点任意节点切回焦点模式并以该节点为中心。
// 渲染:ReactFlow(@xyflow/react)+ dagre 布局(rankdir=LR),节点复用 antd 卡片,自带
// 平移/缩放/自适应。深链 ?versionId=/?sourceId=/?snapshotId=/?jobId=/?lakeId=/?datasetId=
// 直达焦点态(lakeId→数据湖焦点、datasetId→该集最新版本)。
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
  Drawer,
  Empty,
  InputNumber,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import AssetManifest from '@/components/JobDetail/AssetManifest';
import {
  getDatasetLineage,
  getFocusLineage,
  getFocusNeighbors,
  getPanoramaLineage,
  listDataLakes,
  listDataSources,
  listJobs,
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
  lake: { label: '数据湖', color: '#2f54eb' },
  lake_snapshot: { label: '湖快照', color: '#13c2c2' },
  version: { label: '数据集版本', color: '#52c41a' },
  job: { label: '加工任务', color: '#fa8c16' },
  member: { label: '成员', color: '#9254de' },
};

// 全景概览过滤栏「类型」多选项:member 仅焦点成员图层产生,全景播种不覆盖,不作过滤项。
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
  if (n.kind === 'lake') return { width: 250, height: 96 };
  if (n.kind === 'member') return { width: 250, height: 108 };
  return { width: 250, height: 132 };
};

const scanTag = (v?: string) =>
  v === 'passed'
    ? { c: 'green', t: '安全通过' }
    : v === 'failed'
      ? { c: 'red', t: '安全未过' }
      : { c: 'default', t: '未扫描' };

// 已删除节点(实体进回收站/已清理):灰显 + 「已删除」徽标 + 禁跳详情。
const DELETED_STYLE = { opacity: 0.5, filter: 'grayscale(1)' } as const;
const DeletedTag: React.FC = () => (
  <Tag color="default" style={{ margin: 0 }}>
    已删除
  </Tag>
);

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

/** 数据湖节点卡(仅全景森林出现:湖容器本体,contains 边连到湖内全部快照,
 *  让"哪些快照属于哪个湖"一眼可辨);点击跳数据湖详情 */
const LakeNode: React.FC<{
  n: DataPlatform.LineageNode;
  onFocus?: () => void;
}> = ({ n, onFocus }) => (
  <div
    onClick={() => history.push(`/data-lakes/${n.id}`)}
    style={{
      position: 'relative',
      height: '100%',
      padding: 10,
      borderRadius: 8,
      cursor: 'pointer',
      background: 'var(--ant-color-bg-container)',
      border: '1px solid var(--ant-color-border)',
      borderLeft: `3px solid ${KIND_META.lake.color}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      justifyContent: 'center',
      overflow: 'hidden',
    }}
  >
    <FocusButton onFocus={onFocus} />
    <Tag color="geekblue" style={{ margin: 0, width: 'fit-content' }}>
      数据湖
    </Tag>
    <Tooltip title={n.description ? `${n.name}:${n.description}` : n.name}>
      <Typography.Text strong ellipsis style={{ fontSize: 13 }}>
        {n.name}
      </Typography.Text>
    </Tooltip>
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
      {n.snapshotCount ?? 0} 个快照
    </Typography.Text>
  </div>
);

/** 湖快照节点卡(湖对象@第 n 版 + 溯源摘要);点击跳数据湖详情 */
const SnapshotNode: React.FC<{
  n: DataPlatform.LineageNode;
  onFocus?: () => void;
}> = ({ n, onFocus }) => (
  <div
    onClick={() => {
      if (n.deleted) return;
      if (n.lakeId) history.push(`/data-lakes/${n.lakeId}`);
    }}
    style={{
      position: 'relative',
      height: '100%',
      padding: 10,
      borderRadius: 8,
      cursor: n.deleted ? 'not-allowed' : n.lakeId ? 'pointer' : 'default',
      background: 'var(--ant-color-bg-container)',
      border: '1px solid var(--ant-color-border)',
      borderLeft: `3px solid ${KIND_META.lake_snapshot.color}`,
      display: 'flex',
      flexDirection: 'column',
      gap: 4,
      overflow: 'hidden',
      ...(n.deleted ? DELETED_STYLE : {}),
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
      {n.deleted && <DeletedTag />}
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
      onClick={() => {
        // 已删除版本禁跳详情(目标已进回收站/清理,详情页会 404)
        if (n.deleted) return;
        if (n.datasetId)
          history.push(`/datasets/${n.datasetId}?version=${n.id}`);
      }}
      style={{
        position: 'relative',
        height: '100%',
        padding: 10,
        borderRadius: 8,
        cursor: n.deleted ? 'not-allowed' : 'pointer',
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
        ...(n.deleted ? DELETED_STYLE : {}),
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
        {n.deleted && <DeletedTag />}
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
      ...(n.deleted ? DELETED_STYLE : {}),
    }}
  >
    <FocusButton onFocus={onFocus} />
    <Space size={4} wrap>
      <Tag color="purple" style={{ margin: 0, width: 'fit-content' }}>
        成员
      </Tag>
      {n.deleted && <DeletedTag />}
    </Space>
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

/** 自定义 ReactFlow 节点:复用 antd 版本/任务卡片 + 隐藏 Handle(左 target/右 source,
 *  配合 LR 左→右布局)。没有 Handle ReactFlow 建不出边(error #008);隐藏(opacity:0)保持卡片整洁。 */
const HANDLE_STYLE = { opacity: 0 } as const;

/** 节点卡「+N」增量展开钮:绝对定位在卡片左(上游)/右(下游)侧中部,点击调
 *  GET /lineage/neighbors 拉一层邻居合并进当前图;count<=0 或未接 onClick 不渲染。 */
const ExpandTab: React.FC<{
  count: number;
  onClick?: () => void;
  side: 'left' | 'right';
}> = ({ count, onClick, side }) => {
  if (!count || count <= 0 || !onClick) return null;
  return (
    <Button
      size="small"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      style={{
        position: 'absolute',
        ...(side === 'left' ? { left: -14 } : { right: -14 }),
        top: '50%',
        transform: 'translateY(-50%)',
        zIndex: 2,
        fontSize: 11,
        padding: '0 6px',
        height: 20,
        lineHeight: '20px',
      }}
    >
      +{count}
    </Button>
  );
};

// version 节点的 data 额外挂 expanded/onToggleMembers(见 LineageGraph 的 rfNodes 构造),
// 驱动"N 成员" Tag 的展开/折叠交互;所有节点类型均挂 onFocus,驱动右上角聚焦按钮;
// onExpandUp/onExpandDown 驱动「+N」增量展开(焦点探索重构新增)。
type VersionNodeData = DataPlatform.LineageNode & {
  expanded?: boolean;
  onToggleMembers?: () => void;
  onFocus?: () => void;
  onExpandUp?: () => void;
  onExpandDown?: () => void;
};
const RFVersionNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <ExpandTab count={d.moreUp ?? 0} onClick={d.onExpandUp} side="left" />
      <VersionNode
        n={d}
        expanded={d.expanded}
        onToggleMembers={d.onToggleMembers}
        onFocus={d.onFocus}
      />
      <ExpandTab
        count={d.moreDown ?? 0}
        onClick={d.onExpandDown}
        side="right"
      />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
};
const RFJobNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <ExpandTab count={d.moreUp ?? 0} onClick={d.onExpandUp} side="left" />
      <JobNode n={d} onFocus={d.onFocus} />
      <ExpandTab
        count={d.moreDown ?? 0}
        onClick={d.onExpandDown}
        side="right"
      />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
};
const RFLakeNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <LakeNode n={d} onFocus={d.onFocus} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
};
const RFSnapshotNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <ExpandTab count={d.moreUp ?? 0} onClick={d.onExpandUp} side="left" />
      <SnapshotNode n={d} onFocus={d.onFocus} />
      <ExpandTab
        count={d.moreDown ?? 0}
        onClick={d.onExpandDown}
        side="right"
      />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
};
const RFDatasourceNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <ExpandTab count={d.moreUp ?? 0} onClick={d.onExpandUp} side="left" />
      <DatasourceNode n={d} onFocus={d.onFocus} />
      <ExpandTab
        count={d.moreDown ?? 0}
        onClick={d.onExpandDown}
        side="right"
      />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
};
const RFMemberNode = ({ data }: NodeProps) => {
  const d = data as VersionNodeData;
  return (
    <>
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <ExpandTab count={d.moreUp ?? 0} onClick={d.onExpandUp} side="left" />
      <MemberNode n={d} onFocus={d.onFocus} />
      <ExpandTab
        count={d.moreDown ?? 0}
        onClick={d.onExpandDown}
        side="right"
      />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </>
  );
};

/** 血缘图:ReactFlow + dagre(左→右布局 rankdir=LR),节点为 antd 卡片,自带平移/缩放/
 *  自适应。焦点探索重构后 members 由后端驱动(不再本地展开),故不持有本地展开态;
 *  onEdgeClick/onExpand 挂给容器实现「点边看详情」「+N 增量展开」两个新特性。 */
const LineageGraph: React.FC<{
  graph?: DataPlatform.LineageGraph;
  onFocusNode?: (n: DataPlatform.LineageNode) => void;
  onEdgeClick?: (edge: DataPlatform.LineageEdge) => void;
  onExpand?: (node: DataPlatform.LineageNode, direction: 'up' | 'down') => void;
}> = ({ graph, onFocusNode, onEdgeClick, onExpand }) => {
  const nodeTypes = useMemo(
    () => ({
      version: RFVersionNode,
      job: RFJobNode,
      lake: RFLakeNode,
      lake_snapshot: RFSnapshotNode,
      datasource: RFDatasourceNode,
      member: RFMemberNode,
    }),
    [],
  );
  if (!graph || graph.nodes.length === 0) {
    return <Empty description="无血缘数据（当前过滤范围内没有节点）" />;
  }

  // dagre 算左→右布局,产出 ReactFlow 节点(含 position)。dagre 的 x/y 是节点中心,
  // ReactFlow 要左上角,故各减半尺寸。
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({
    // 左→右流向(数据源在左,下游往右):每条血缘链横向展开、多棵树纵向堆叠。
    rankdir: 'LR',
    // LR 下 nodesep=同层节点纵向间距、ranksep=层间横向间距(卡片较宽,层间给足)。
    nodesep: 24,
    ranksep: 96,
    marginx: 16,
    marginy: 16,
  });
  graph.nodes.forEach((n) => {
    g.setNode(n.id, sizeOf(n));
  });
  graph.edges.forEach((e) => {
    g.setEdge(e.from, e.to);
  });
  dagre.layout(g);

  const rfNodes: Node[] = graph.nodes.map((n) => {
    const p = g.node(n.id);
    const s = sizeOf(n);
    const data: Record<string, unknown> = { ...n };
    if (onFocusNode) {
      data.onFocus = () => onFocusNode(n);
    }
    if (onExpand) {
      data.onExpandUp = () => onExpand(n, 'up');
      data.onExpandDown = () => onExpand(n, 'down');
    }
    return {
      id: n.id,
      type: n.kind,
      position: { x: p.x - s.width / 2, y: p.y - s.height / 2 },
      data,
      style: { width: s.width, height: s.height },
    };
  });
  // 点边详情靠 id 反查原始边(不额外把整条边塞进 rfEdges.data,保持 ReactFlow 边对象精简)
  const edgeById = new Map<string, DataPlatform.LineageEdge>(
    graph.edges.map((e) => [`${e.from}->${e.to}->${e.kind}`, e]),
  );
  const rfEdges: Edge[] = graph.edges.map((e) => ({
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
        onEdgeClick={
          onEdgeClick
            ? (_, edge) => {
                const orig = edgeById.get(edge.id);
                if (orig) onEdgeClick(orig);
              }
            : undefined
        }
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
};

// 焦点参数:与 GET /lineage/focus、GET /lineage/neighbors 的 kind 入参一一对应
// (节点→入参映射见 nodeToFocusParams)。省略 lake_object——平台节点 kind 不产出
// 该类型,焦点探索用不到。
type FocusParams =
  | { kind: 'dataset_version'; versionId: string }
  | { kind: 'job'; jobId: string }
  | { kind: 'source'; sourceId: string }
  | { kind: 'lake'; lakeId: string }
  | { kind: 'lake_snapshot'; snapshotId: string }
  | { kind: 'member'; versionId: string; tableName: string };

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
    case 'lake':
      return n.name ?? '数据湖';
    case 'member':
      return n.tableName ?? '成员';
    default:
      return n.name ?? n.id;
  }
};

/** 节点 → 焦点/邻居接口入参(节点→焦点/邻居入参映射,见文件头规格)。member 缺
 *  versionId/tableName(理论不会发生,数据完整性兜底)时返回 undefined。 */
const nodeToFocusParams = (
  n: DataPlatform.LineageNode,
): FocusParams | undefined => {
  if (n.kind === 'version') return { kind: 'dataset_version', versionId: n.id };
  if (n.kind === 'job') return { kind: 'job', jobId: n.id };
  if (n.kind === 'lake') return { kind: 'lake', lakeId: n.id };
  if (n.kind === 'lake_snapshot')
    return { kind: 'lake_snapshot', snapshotId: n.id };
  if (n.kind === 'datasource') return { kind: 'source', sourceId: n.id };
  if (n.kind === 'member' && n.versionId && n.tableName) {
    return { kind: 'member', versionId: n.versionId, tableName: n.tableName };
  }
  return undefined;
};

/** 「+N」展开返回的邻居子图合并进当前 graph:节点按 id 去重(已存在的不覆盖,保留
 *  焦点接口算出的真实 moreUp/moreDown),边按 from->to->kind 去重;合并后把被展开
 *  节点该方向的计数清零(表示"已展开")。注意:邻居接口的 moreUp/moreDown 是相对
 *  它返回的这个单跳小子图算的,合并后新并入节点的计数可能比"相对当前完整大图"的
 *  真实值偏大(未扣除已经显示在图上的邻居)——再次点击展开时会用真实数据收敛,MVP 可接受。 */
const mergeNeighbors = (
  prev: DataPlatform.LineageGraph | undefined,
  incoming: DataPlatform.LineageGraph | undefined,
  expandedNodeId: string,
  direction: 'up' | 'down',
): DataPlatform.LineageGraph | undefined => {
  if (!prev || !incoming) return prev;
  const existingIds = new Set(prev.nodes.map((n) => n.id));
  const newNodes = incoming.nodes.filter((n) => !existingIds.has(n.id));
  const existingEdgeKeys = new Set(
    prev.edges.map((e) => `${e.from}->${e.to}->${e.kind}`),
  );
  const newEdges = incoming.edges.filter(
    (e) => !existingEdgeKeys.has(`${e.from}->${e.to}->${e.kind}`),
  );
  const countField = direction === 'up' ? 'moreUp' : 'moreDown';
  const nodes = prev.nodes.map((n) =>
    n.id === expandedNodeId ? { ...n, [countField]: 0 } : n,
  );
  return {
    ...prev,
    nodes: [...nodes, ...newNodes],
    edges: [...prev.edges, ...newEdges],
  };
};

/** 边的中文标签:湖/源层边用 EDGE_LABEL,版本↔任务的 input/output 边补「输入/产出」 */
const edgeKindLabel = (k: DataPlatform.LineageEdge['kind']) =>
  EDGE_LABEL[k] ?? (k === 'input' ? '输入' : k === 'output' ? '产出' : k);

/** 血缘边详情 Drawer 里,非任务边(湖/源层 extract/ingest/merge/hosted_source/
 *  contains)两端节点的关键信息展示(无嵌 AssetManifest 的必要,直接摘要字段)。 */
const NodeSummary: React.FC<{ n?: DataPlatform.LineageNode }> = ({ n }) => {
  if (!n) return <Typography.Text type="secondary">-</Typography.Text>;
  return (
    <Space direction="vertical" size={2}>
      <Typography.Text strong>{nodeLabel(n)}</Typography.Text>
      {n.rows != null && (
        <Typography.Text type="secondary">{n.rows} 行</Typography.Text>
      )}
      {n.kind === 'lake_snapshot' && (
        <Typography.Text type="secondary">
          湖 {n.lakeName ?? n.lakeId ?? '-'}
          {n.sourceSummary ? ` · ${n.sourceSummary}` : ''}
        </Typography.Text>
      )}
      {n.kind === 'datasource' && (
        <Typography.Text type="secondary">
          {n.sourceType ?? '-'}
          {n.dbKind ? ` · ${n.dbKind}` : ''}
        </Typography.Text>
      )}
      {n.kind === 'member' && (n.sourceName || n.sourceUploadChannel) && (
        <Typography.Text type="secondary">
          来源:{n.sourceName ?? n.sourceUploadChannel}
        </Typography.Text>
      )}
      {n.kind === 'version' && n.origin && n.origin !== 'managed' && (
        <Typography.Text type="secondary">来源标记:{n.origin}</Typography.Text>
      )}
    </Space>
  );
};

type FocusType = 'dataset_version' | 'source' | 'job' | 'lake';

const FOCUS_TYPE_OPTIONS: { value: FocusType; label: string }[] = [
  { value: 'dataset_version', label: '数据集版本' },
  { value: 'source', label: '数据源' },
  { value: 'job', label: '加工任务' },
  { value: 'lake', label: '数据湖' },
];

const Lineage: React.FC = () => {
  const [searchParams] = useSearchParams();

  // 视图模式:focus=OpenMetadata 式焦点探索(默认);panorama=全景概览森林。
  const [mode, setMode] = useState<'focus' | 'panorama'>('focus');

  // 焦点类型(顶部 Select,驱动下方「焦点实体」控件切换):数据集版本默认,深链带
  // sourceId/jobId/lakeId 时预选对应类型。
  const [focusType, setFocusType] = useState<FocusType>(() => {
    if (searchParams.get('sourceId')) return 'source';
    if (searchParams.get('jobId')) return 'job';
    if (searchParams.get('lakeId')) return 'lake';
    return 'dataset_version';
  });
  const [selectedSourceId, setSelectedSourceId] = useState<string | undefined>(
    () => searchParams.get('sourceId') ?? undefined,
  );
  const [selectedJobId, setSelectedJobId] = useState<string | undefined>(
    () => searchParams.get('jobId') ?? undefined,
  );
  const [selectedLakeId, setSelectedLakeId] = useState<string | undefined>(
    () => searchParams.get('lakeId') ?? undefined,
  );
  // 数据集版本走 DatasetPicker 选数据集,onChange 后异步解析该集最新版本(见下方 effect)
  const [selectedDatasetId, setSelectedDatasetId] = useState<
    string | undefined
  >(() => searchParams.get('datasetId') ?? undefined);
  // 湖下拉选项(数据湖焦点类型 + 全景湖过滤共用);全景过滤态
  const [lakeOptions, setLakeOptions] = useState<
    { value: string; label: string }[]
  >([]);
  const [filterLakeId, setFilterLakeId] = useState<string>();
  const [filterKinds, setFilterKinds] = useState<string[]>([]);
  const [panoramaGraph, setPanoramaGraph] =
    useState<DataPlatform.LineageGraph>();
  // 每次全景新数据落地自增,作 LineageGraph 的 key 触发重挂载 fitView。
  const [panoramaFitSeq, setPanoramaFitSeq] = useState(0);
  const [sourceOptions, setSourceOptions] = useState<
    { value: string; label: string }[]
  >([]);
  const [jobOptions, setJobOptions] = useState<
    { value: string; label: string }[]
  >([]);

  const [up, setUp] = useState(2);
  const [down, setDown] = useState(2);
  const [members, setMembers] = useState(false);

  // 深链(?versionId=/?sourceId=/?snapshotId=/?jobId=):优先级与前端展示顺序一致;
  // ?datasetId= 走 selectedDatasetId 变化触发的下方 effect 异步解析为版本焦点。
  const [focus, setFocus] = useState<FocusParams | undefined>(() => {
    const versionId = searchParams.get('versionId');
    const sourceId = searchParams.get('sourceId');
    const snapshotId = searchParams.get('snapshotId');
    const jobId = searchParams.get('jobId');
    const lakeId = searchParams.get('lakeId');
    if (versionId) return { kind: 'dataset_version', versionId };
    if (sourceId) return { kind: 'source', sourceId };
    if (snapshotId) return { kind: 'lake_snapshot', snapshotId };
    if (jobId) return { kind: 'job', jobId };
    // ?lakeId=(数据湖详情「查看血缘」入口):聚焦整个数据湖(其全部快照+下游)
    if (lakeId) return { kind: 'lake', lakeId };
    return undefined;
  });
  const [focusLabel, setFocusLabel] = useState('');
  const [graph, setGraph] = useState<DataPlatform.LineageGraph>();
  const [loading, setLoading] = useState(false);
  const [edgeDetail, setEdgeDetail] = useState<DataPlatform.LineageEdge>();

  // 焦点实体下拉的数据源/任务选项:一次性拉够(与原全景页湖下拉同量级假设)
  useEffect(() => {
    listDataSources({ pageSize: 200 })
      .then((res) =>
        setSourceOptions(
          (res.data ?? []).map((d) => ({ value: d.id, label: d.name })),
        ),
      )
      .catch(() => undefined);
    // /jobs 端点 pageSize 上限 100(le=100),超出会 422
    listJobs({ pageSize: 100 })
      .then((res) =>
        setJobOptions(
          (res.data ?? []).map((j) => ({ value: j.id, label: j.name })),
        ),
      )
      .catch(() => undefined);
    listDataLakes({ pageSize: 200 })
      .then((res) =>
        setLakeOptions(
          (res.data ?? []).map((l) => ({ value: l.id, label: l.name })),
        ),
      )
      .catch(() => undefined);
  }, []);

  // 数据集 → 焦点版本:选中数据集后取其血缘 DAG,挑 versionNo 最大的版本做焦点
  // (与数据集/数据湖详情「查看血缘」旧入口 ?datasetId= 深链共用此逻辑)。
  useEffect(() => {
    if (!selectedDatasetId) return;
    let cancelled = false;
    getDatasetLineage(selectedDatasetId)
      .then((res) => {
        if (cancelled) return;
        const versions = res.data.nodes.filter(
          (n) => n.kind === 'version' && n.datasetId === selectedDatasetId,
        );
        if (versions.length === 0) return;
        const latest = versions.reduce((a, b) =>
          (b.versionNo ?? -1) > (a.versionNo ?? -1) ? b : a,
        );
        setFocus({ kind: 'dataset_version', versionId: latest.id });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [selectedDatasetId]);

  // focusKey 用值而非对象引用做依赖——避免加载后回填 focusLabel 触发的重取(与原
  // anchorKey 写法一致)。
  const focusKey = !focus
    ? undefined
    : focus.kind === 'member'
      ? `member:${focus.versionId}:${focus.tableName}`
      : focus.kind === 'dataset_version'
        ? `dataset_version:${focus.versionId}`
        : focus.kind === 'job'
          ? `job:${focus.jobId}`
          : focus.kind === 'source'
            ? `source:${focus.sourceId}`
            : focus.kind === 'lake'
              ? `lake:${focus.lakeId}`
              : `lake_snapshot:${focus.snapshotId}`;

  useEffect(() => {
    if (!focus) {
      setGraph(undefined);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getFocusLineage({ ...focus, up, down, members })
      .then((res) => {
        if (cancelled) return;
        setGraph(res.data);
        const focusNode = res.data.nodes.find((n) => n.isFocus);
        if (focusNode) setFocusLabel(nodeLabel(focusNode));
      })
      .catch(() => {
        if (!cancelled) setGraph(undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, up, down, members]);

  // 全景概览态:按 湖/类型 过滤拉一次(仅 panorama 模式请求)。
  useEffect(() => {
    if (mode !== 'panorama') return;
    let cancelled = false;
    setLoading(true);
    getPanoramaLineage({
      lakeId: filterLakeId,
      kinds: filterKinds.length ? filterKinds.join(',') : undefined,
    })
      .then((res) => {
        if (!cancelled) {
          setPanoramaGraph(res.data);
          setPanoramaFitSeq((s) => s + 1);
        }
      })
      .catch(() => {
        if (!cancelled) setPanoramaGraph(undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mode, filterLakeId, filterKinds]);

  // 再聚焦(节点右上角「聚焦」按钮 / 全景态点节点):换焦点中心并切回焦点模式,
  // 触发焦点 effect 整图重拉——天然清空「+N」展开态(旧图被新焦点图整体替换)。
  const focusOnNode = (n: DataPlatform.LineageNode) => {
    const p = nodeToFocusParams(n);
    if (p) {
      setFocus(p);
      setMode('focus');
    }
  };

  // 「+N」增量展开:只拉该节点单方向一层邻居,合并进当前图(不整图重拉)
  const onExpand = (n: DataPlatform.LineageNode, direction: 'up' | 'down') => {
    const p = nodeToFocusParams(n);
    if (!p) return;
    getFocusNeighbors({ ...p, direction, members })
      .then((res) => {
        setGraph((prev) => mergeNeighbors(prev, res.data, n.id, direction));
      })
      .catch(() => undefined);
  };

  const edgeFromNode = edgeDetail
    ? graph?.nodes.find((n) => n.id === edgeDetail.from)
    : undefined;
  const edgeToNode = edgeDetail
    ? graph?.nodes.find((n) => n.id === edgeDetail.to)
    : undefined;
  const edgeJobNode =
    edgeFromNode?.kind === 'job'
      ? edgeFromNode
      : edgeToNode?.kind === 'job'
        ? edgeToNode
        : undefined;

  return (
    <PageContainer header={{ title: '数据血缘', breadcrumb: {} }}>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space size={16} wrap>
          <Segmented
            options={[
              { label: '焦点探索', value: 'focus' },
              { label: '全景概览', value: 'panorama' },
            ]}
            value={mode}
            onChange={(v) => setMode(v as 'focus' | 'panorama')}
          />
          {mode === 'focus' && (
            <>
              <Space size={6}>
                <Typography.Text type="secondary">焦点类型</Typography.Text>
                <Select
                  style={{ width: 140 }}
                  options={FOCUS_TYPE_OPTIONS}
                  value={focusType}
                  onChange={(v: FocusType) => {
                    setFocusType(v);
                    setFocus(undefined);
                    setFocusLabel('');
                    setSelectedSourceId(undefined);
                    setSelectedJobId(undefined);
                    setSelectedLakeId(undefined);
                    setSelectedDatasetId(undefined);
                  }}
                />
              </Space>
              <Space size={6}>
                <Typography.Text type="secondary">焦点实体</Typography.Text>
                {focusType === 'source' && (
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择数据源"
                    style={{ width: 260 }}
                    options={sourceOptions}
                    value={selectedSourceId}
                    onChange={(v: string | undefined) => {
                      setSelectedSourceId(v);
                      setFocus(v ? { kind: 'source', sourceId: v } : undefined);
                    }}
                  />
                )}
                {focusType === 'job' && (
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择加工任务"
                    style={{ width: 260 }}
                    options={jobOptions}
                    value={selectedJobId}
                    onChange={(v: string | undefined) => {
                      setSelectedJobId(v);
                      setFocus(v ? { kind: 'job', jobId: v } : undefined);
                    }}
                  />
                )}
                {focusType === 'lake' && (
                  <Select
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    placeholder="选择数据湖"
                    style={{ width: 260 }}
                    options={lakeOptions}
                    value={selectedLakeId}
                    onChange={(v: string | undefined) => {
                      setSelectedLakeId(v);
                      setFocus(v ? { kind: 'lake', lakeId: v } : undefined);
                    }}
                  />
                )}
                {focusType === 'dataset_version' && (
                  <DatasetPicker
                    value={selectedDatasetId}
                    onChange={setSelectedDatasetId}
                  />
                )}
              </Space>
              <Space size={6}>
                <Typography.Text type="secondary">上游深度</Typography.Text>
                <InputNumber
                  min={0}
                  max={3}
                  value={up}
                  onChange={(v) => setUp(typeof v === 'number' ? v : 2)}
                  style={{ width: 64 }}
                />
              </Space>
              <Space size={6}>
                <Typography.Text type="secondary">下游深度</Typography.Text>
                <InputNumber
                  min={0}
                  max={3}
                  value={down}
                  onChange={(v) => setDown(typeof v === 'number' ? v : 2)}
                  style={{ width: 64 }}
                />
              </Space>
              <Space size={6}>
                <Typography.Text type="secondary">成员图层</Typography.Text>
                <Switch checked={members} onChange={setMembers} />
              </Space>
            </>
          )}
          {mode === 'panorama' && (
            <>
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
                />
              </Space>
            </>
          )}
        </Space>
      </Card>

      {mode === 'focus' && focus && (
        <Typography.Text
          type="secondary"
          style={{ display: 'block', marginBottom: 12 }}
        >
          当前聚焦:{focusLabel || '加载中…'}
        </Typography.Text>
      )}

      {mode === 'panorama' && panoramaGraph?.truncated && (
        <Alert
          type="warning"
          showIcon
          closable
          message={`血缘图过大已截断（共 ${
            panoramaGraph.totalEstimated ?? '?'
          } 节点），请用 湖/类型 缩小范围`}
          style={{ marginBottom: 12 }}
        />
      )}

      <Card
        size="small"
        title={
          mode === 'panorama'
            ? '血缘全景森林（数据源 → 数据湖 → 湖快照 → 数据集版本 → 加工任务）'
            : '焦点血缘'
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
          {mode === 'panorama' ? (
            panoramaGraph && panoramaGraph.nodes.length > 0 ? (
              // 全景态:点任意节点 → focusOnNode 切回焦点模式并以该节点为中心。
              // key 随新数据落地自增:ReactFlow 的 fitView 只在挂载时生效,换湖/类型
              // 过滤后布局大变,不重挂载会停在旧视口(常见表现为一片空白)。注意不能
              // key 到过滤条件——过滤一变立即重挂载,此时新图还没请求回来,fitView
              // 适配的仍是旧图。
              <LineageGraph
                key={panoramaFitSeq}
                graph={panoramaGraph}
                onFocusNode={focusOnNode}
              />
            ) : loading ? (
              <div style={{ height: 420 }} />
            ) : (
              <Empty description="无血缘数据（当前过滤范围内没有节点）" />
            )
          ) : !focus ? (
            <Empty description="请选择焦点实体开始探索血缘" />
          ) : graph ? (
            <LineageGraph
              graph={graph}
              onFocusNode={focusOnNode}
              onEdgeClick={setEdgeDetail}
              onExpand={onExpand}
            />
          ) : loading ? (
            // 加载期只显 Spin 转圈,不渲染"无数据"空态(避免误导)
            <div style={{ height: 420 }} />
          ) : (
            <Empty description="无血缘数据（当前过滤范围内没有节点）" />
          )}
        </Spin>
      </Card>

      <Drawer
        title="血缘边详情"
        width={520}
        open={!!edgeDetail}
        onClose={() => setEdgeDetail(undefined)}
        destroyOnHidden
      >
        {edgeDetail && (
          <>
            <Typography.Paragraph>
              {nodeLabel(edgeFromNode)}
              {' —['}
              {edgeKindLabel(edgeDetail.kind)}
              {']→ '}
              {nodeLabel(edgeToNode)}
            </Typography.Paragraph>
            {edgeJobNode ? (
              <AssetManifest job={{ id: edgeJobNode.id } as DataPlatform.Job} />
            ) : (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Card size="small" title="起点">
                  <NodeSummary n={edgeFromNode} />
                </Card>
                <Card size="small" title="终点">
                  <NodeSummary n={edgeToNode} />
                </Card>
              </Space>
            )}
          </>
        )}
      </Drawer>
    </PageContainer>
  );
};

export default Lineage;
